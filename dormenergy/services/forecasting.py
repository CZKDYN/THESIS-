"""Per-unit XGBoost forecasts. Models and results survive host disk replacement."""
import math
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import select

from ..extensions import db
from ..models import AuditLog, Forecast, ForecastJob, ForecastModel, Rate, Reading, Unit, utcnow
from .billing import DomainError, aware, lock_unit

MANILA = ZoneInfo('Asia/Manila')
HOUR = timedelta(hours=1)
FEATURES = ['hour_sin', 'hour_cos', 'weekday', 'weekend', 'lag_1', 'lag_2', 'lag_24',
            'mean_6', 'mean_24', 'observed_6', 'observed_24']
MIN_HOURS = {'simulation': 168, 'hardware': 720}
HORIZON = 24


def floor_hour(value):
    return aware(value).replace(minute=0, second=0, microsecond=0)


def hourly_history(rows, source, now):
    """Allocate observed intervals uniformly, without using a later endpoint.

    A bucket needs 95% observed coverage. Cross-boundary intervals contribute
    only to hours whose end is at/after the interval's observation time. Missing
    intervals, resets, and simulator pauses never become zero-consumption hours.
    """
    energy, coverage = {}, {}
    previous = None
    count = 0
    for row in rows:
        count += 1
        if previous is None:
            previous = row
            continue
        start, end = aware(previous.timestamp), aware(row.timestamp)
        seconds = (end-start).total_seconds()
        valid = (row.source == source == previous.source and row.meter_epoch == previous.meter_epoch
                 and 0 < seconds <= 5400 and end <= now and row.delta_kwh >= 0
                 and row.energy >= previous.energy)
        if source == 'simulation' and row.event_id.startswith('sim-') and seconds > 120:
            valid = False
        if valid:
            hour = floor_hour(start)
            while hour < end:
                stop = hour + HOUR
                if end <= stop:
                    observed = max(0, (min(end, stop)-max(start, hour)).total_seconds())
                    energy[hour] = energy.get(hour, 0.0) + float(row.delta_kwh) * observed / seconds
                    coverage[hour] = coverage.get(hour, 0.0) + observed
                hour = stop
        previous = row
    cutoff = floor_hour(now)
    values = {hour: value for hour, value in energy.items()
              if hour+HOUR <= cutoff and coverage[hour] >= 3420 and math.isfinite(value) and 0 <= value <= 25}
    return values, {'readings_examined': count, 'complete_hours': len(values),
                    'coverage_minimum': 0.95, 'maximum_interval_seconds': 5400, 'maximum_hourly_kwh': 25}


def history_for(unit, now, days=45):
    if not unit.device:
        raise DomainError('Register a meter for this unit first.')
    rows = db.session.scalars(select(Reading).where(
        Reading.device_id == unit.device.id, Reading.source == unit.device.source,
        Reading.timestamp >= now-timedelta(days=days), Reading.timestamp <= now)
        .order_by(Reading.timestamp).execution_options(yield_per=1000))
    return hourly_history(rows, unit.device.source, now)


def features(hour, history):
    local = hour.astimezone(MANILA)
    last6 = [history[hour-HOUR*i] for i in range(1, 7) if hour-HOUR*i in history]
    last24 = [history[hour-HOUR*i] for i in range(1, 25) if hour-HOUR*i in history]
    missing = float('nan')
    return [math.sin(2*math.pi*local.hour/24), math.cos(2*math.pi*local.hour/24),
            local.weekday(), int(local.weekday() >= 5), history.get(hour-HOUR, missing),
            history.get(hour-2*HOUR, missing), history.get(hour-24*HOUR, missing),
            sum(last6)/len(last6) if len(last6) >= 4 else missing,
            sum(last24)/len(last24) if len(last24) >= 20 else missing, len(last6), len(last24)]


def training_samples(history):
    if not history:
        return [], [], []
    warmup = min(history) + 24*HOUR
    hours = [hour for hour in sorted(history) if hour >= warmup and hour-HOUR in history
             and sum(hour-HOUR*i in history for i in range(1, 7)) >= 4]
    return hours, [features(hour, history) for hour in hours], [history[hour] for hour in hours]


def metrics(actual, predicted):
    differences = [float(p)-float(a) for a, p in zip(actual, predicted)]
    nonzero = [(float(a), float(p)) for a, p in zip(actual, predicted) if abs(float(a)) > 1e-9]
    return dict(mae=sum(abs(d) for d in differences)/len(differences),
                rmse=math.sqrt(sum(d*d for d in differences)/len(differences)),
                mape=100*sum(abs(p-a)/abs(a) for a, p in nonzero)/len(nonzero) if nonzero else None,
                samples=len(differences), mape_zero_excluded=len(differences)-len(nonzero))


def latest_model(unit):
    if not unit.device:
        return None
    return db.session.scalar(select(ForecastModel).where(
        ForecastModel.unit_id == unit.id, ForecastModel.source == unit.device.source)
        .order_by(ForecastModel.trained_at.desc(), ForecastModel.id.desc()).limit(1))


def train_model(unit_id, actor_id=None, now=None):
    import numpy as np
    import xgboost as xgb

    now = aware(now or utcnow())
    unit = db.session.get(Unit, unit_id)
    if not unit or unit.archived_at or not unit.device:
        raise DomainError('Choose an active unit with a registered meter.', 409)
    source = unit.device.source
    if source == 'simulation' and not current_app.config['SIMULATION_MODE']:
        raise DomainError('Synthetic model training requires simulation mode.', 409)
    history, coverage = history_for(unit, now)
    hours, vectors, targets = training_samples(history)
    if len(history) < MIN_HOURS[source] or len(hours) < 120:
        required = 7 if source == 'simulation' else 30
        raise DomainError(f'Insufficient historical data: need at least {required} days of covered hourly '
                          f'{source} data for this unit; found {len(history)} covered hours.')
    train_end, validation_end = int(len(hours)*0.6), int(len(hours)*0.8)
    x, y = np.asarray(vectors, dtype=np.float32), np.asarray(targets, dtype=np.float32)
    train = xgb.DMatrix(x[:train_end], label=y[:train_end], feature_names=FEATURES)
    validation = xgb.DMatrix(x[train_end:validation_end], label=y[train_end:validation_end], feature_names=FEATURES)
    test = xgb.DMatrix(x[validation_end:], feature_names=FEATURES)
    best, best_mae, best_depth = None, float('inf'), None
    for depth in (2, 4):
        candidate = xgb.train(dict(objective='reg:squarederror', eval_metric='mae',
                                  tree_method='hist', device='cpu', nthread=1, seed=42,
                                  max_depth=depth, eta=0.08), train, num_boost_round=160,
                              evals=[(validation, 'validation')], early_stopping_rounds=15,
                              verbose_eval=False)
        estimates = np.maximum(0, candidate.predict(validation, iteration_range=(0, candidate.best_iteration+1)))
        error = metrics(y[train_end:validation_end], estimates)['mae']
        if error < best_mae:
            best, best_mae, best_depth = candidate, error, depth
    predicted = np.maximum(0, best.predict(test, iteration_range=(0, best.best_iteration+1)))
    actual = y[validation_end:]
    persistence = metrics(actual, x[validation_end:, FEATURES.index('lag_1')])
    seasonal_values = x[validation_end:, FEATURES.index('lag_24')]
    seasonal_mask = np.isfinite(seasonal_values)
    seasonal = metrics(actual[seasonal_mask], seasonal_values[seasonal_mask]) if seasonal_mask.any() else None
    splits = {}
    for name, begin, end in [('train', 0, train_end), ('validation', train_end, validation_end), ('test', validation_end, len(hours))]:
        splits[name] = dict(start=hours[begin].isoformat(), end=(hours[end-1]+HOUR).isoformat(), samples=end-begin)
    report = dict(algorithm='XGBoost', xgboost_version=xgb.__version__, source=source,
                  synthetic=source == 'simulation', feature_schema=1, features=FEATURES,
                  coverage=coverage, splits=splits, best_iteration=best.best_iteration,
                  max_depth=best_depth, validation_mae=best_mae, test=metrics(actual, predicted),
                  baselines=dict(persistence=persistence, seasonal_24h=seasonal),
                  comparison=[dict(timestamp=hour.isoformat(), actual=float(a), predicted=float(p))
                              for hour, a, p in zip(hours[validation_end:], actual, predicted)][-72:],
                  evaluation='Chronological held-out next-hour predictions. Later actual observations '
                             'may become inputs only for subsequent hours. Multi-hour recursive error is not evaluated.')
    lock_unit(unit_id)  # Recheck removal after CPU work; no billing locks during training.
    model = ForecastModel(unit_id=unit_id, version='xgb-'+uuid4().hex, source=source,
                          model_json=best.save_raw(raw_format='json').decode(), metadata_json=report, trained_at=now)
    db.session.add(model)
    db.session.add(AuditLog(actor_id=actor_id, action='forecast_trained',
                           detail=f'Unit {unit_id}, {source}, {len(hours)} samples; held-out test {len(actual)} hours.'))
    db.session.commit()
    return model


def generate_forecasts(unit_id, now=None):
    import numpy as np
    import xgboost as xgb

    now = aware(now or utcnow())
    unit = db.session.get(Unit, unit_id)
    if not unit or unit.archived_at:
        raise DomainError('This unit is unavailable.', 409)
    model = latest_model(unit)
    if model is None:
        raise DomainError('Train a model for this unit first.', 409)
    if model.source == 'simulation' and not current_app.config['SIMULATION_MODE']:
        raise DomainError('Synthetic forecasting requires simulation mode.', 409)
    origin = floor_hour(now)
    version = f'{model.version}:{origin.strftime("%Y%m%dT%H")}'
    if db.session.scalar(select(Forecast.id).where(Forecast.unit_id == unit_id, Forecast.model_version == version).limit(1)):
        return
    history, _ = history_for(unit, now, days=3)
    if origin-HOUR not in history or sum(origin-HOUR*i in history for i in range(1, 7)) < 4:
        raise DomainError('Model trained; waiting for sufficiently covered recent hourly readings.')
    booster = xgb.Booster(params={'nthread': 1, 'device': 'cpu'})
    booster.load_model(bytearray(model.model_json, 'utf-8'))
    predictions = []
    # Include a partial current hour for the prorated remaining-credit estimate.
    for step in range(HORIZON + int(now > origin)):
        hour = origin + step*HOUR
        vector = xgb.DMatrix(np.asarray([features(hour, history)], dtype=np.float32), feature_names=FEATURES)
        value = max(0.0, float(booster.predict(vector, iteration_range=(0, model.metadata_json['best_iteration']+1))[0]))
        if not math.isfinite(value) or value > 25:
            raise DomainError('Model produced an invalid forecast; no forecast was stored.')
        history[hour] = value
        predictions.append((hour, value))
    lock_unit(unit_id)
    # Another worker may have generated the same issue-hour while CPU work ran.
    if not db.session.scalar(select(Forecast.id).where(Forecast.unit_id == unit_id, Forecast.model_version == version).limit(1)):
        for hour, value in predictions:
            db.session.add(Forecast(unit_id=unit_id, timestamp=hour, predicted_kwh=Decimal(str(value)).quantize(Decimal('0.000001')),
                                    model_version=version, source=model.source, generated_at=now))
        db.session.commit()


def credit_duration(balance, curve, rates, now):
    """Bounded projection with scheduled rates; never changes actual balances."""
    from .utility_rates import rate_covers
    balance = Decimal(str(balance))
    if balance <= 0:
        return 0.0
    cursor, elapsed = now, Decimal('0')
    for row in curve:
        start, end = max(now, aware(row.timestamp)), aware(row.timestamp)+HOUR
        if end <= now:
            continue
        if start > cursor:
            return None
        changes = [aware(rate.effective_at) for rate in rates if start < aware(rate.effective_at) < end]
        expirations = [aware(rate.valid_until) for rate in rates if getattr(rate, 'valid_until', None)
                       and start < aware(rate.valid_until) < end]
        boundaries = sorted(set([start, *changes, *expirations, end]))
        for first, last in zip(boundaries, boundaries[1:]):
            applicable = [rate for rate in rates if aware(rate.effective_at) <= first]
            if not applicable or not rate_covers(applicable[-1], first, last):
                return None
            per_second = row.predicted_kwh * applicable[-1].php_per_kwh / Decimal('3600')
            seconds = Decimal(str((last-first).total_seconds()))
            cost = per_second*seconds
            if per_second > 0 and balance <= cost:
                return float(elapsed + balance/per_second)
            balance -= cost
            elapsed += seconds
        cursor = end
    return None


def forecast_payload(unit, now=None):
    now = aware(now or utcnow())
    model = latest_model(unit)
    job = db.session.scalar(select(ForecastJob).where(ForecastJob.unit_id == unit.id)
                            .order_by(ForecastJob.id.desc()).limit(1))
    result = dict(status='insufficient_data', message='No trained model yet', unit_id=unit.id, unit=unit.name,
                  source=unit.device.source if unit.device else None, synthetic=bool(unit.device and unit.device.source == 'simulation'),
                  detail='Train on at least 30 covered days of readings from this meter.' if not current_app.config['SIMULATION_MODE'] else 'Train on stored hourly readings: at least 7 days for a synthetic demo or 30 days for hardware.',
                  remaining_duration=None, horizon_hours=0, points=[], model=None,
                  job=dict(id=job.id, status=job.status, message=job.message) if job else None)
    if not model:
        return result
    result['model'] = dict(version=model.version, trained_at=aware(model.trained_at).isoformat(), **model.metadata_json)
    latest = db.session.scalar(select(Forecast).where(
        Forecast.unit_id == unit.id, Forecast.source == model.source,
        Forecast.model_version.startswith(model.version+':'))
        .order_by(Forecast.generated_at.desc(), Forecast.id.desc()).limit(1))
    if latest:
        curve = db.session.scalars(select(Forecast).where(Forecast.unit_id == unit.id,
            Forecast.model_version == latest.model_version).order_by(Forecast.timestamp)).all()
        future = [row for row in curve if aware(row.timestamp) >= now]
        if future:
            from .utility_rates import billing_rates
            query = billing_rates().where(Rate.effective_at <= aware(curve[-1].timestamp)+HOUR)
            rates = db.session.scalars(query.order_by(Rate.effective_at)).all()
            result.update(status='available', message='Synthetic demonstration forecast' if model.source == 'simulation' else 'XGBoost consumption forecast',
                          detail='Hourly predictions use earlier consumption. Later hours use previous predictions. '
                                 'Credit duration assumes uniform use within each hour and is limited to this forecast horizon.',
                          generated_at=aware(latest.generated_at).isoformat(),
                          remaining_duration=credit_duration(unit.account.balance, curve, rates, now) if unit.account else None,
                          horizon_hours=len(future), points=[dict(timestamp=aware(row.timestamp).isoformat(), kwh=str(row.predicted_kwh)) for row in future])
            return result
    result.update(status='waiting_for_readings', message='Model trained; awaiting recent complete hours',
                  detail='Recent hourly coverage is insufficient. Historical test results remain available below.')
    return result


def process_training_job():
    job = db.session.scalar(select(ForecastJob).where(ForecastJob.status == 'pending')
                            .order_by(ForecastJob.id).with_for_update(skip_locked=True).limit(1))
    if not job:
        db.session.rollback()
        return False
    job.status, job.started_at, job.message = 'running', utcnow(), 'Training and evaluating XGBoost on historical readings.'
    job_id, unit_id, actor_id = job.id, job.unit_id, job.requested_by
    db.session.commit()
    try:
        model = train_model(unit_id, actor_id)
        job = db.session.get(ForecastJob, job_id)
        job.status, job.model_version, job.message = 'completed', model.version, 'Model trained. Held-out evaluation is available.'
    except DomainError as error:
        db.session.rollback()
        job = db.session.get(ForecastJob, job_id)
        job.status, job.message = 'failed', error.message[:300]
    except Exception:
        db.session.rollback()
        current_app.logger.exception('forecast_training_failed job=%s unit=%s', job_id, unit_id)
        job = db.session.get(ForecastJob, job_id)
        job.status, job.message = 'failed', 'Training failed. Ask the administrator to check the forecasting worker logs.'
    job.completed_at = utcnow()
    db.session.commit()
    return True


def forecast_worker_step():
    stale = db.session.scalars(select(ForecastJob).where(ForecastJob.status == 'running',
        ForecastJob.started_at < utcnow()-timedelta(minutes=15)).with_for_update(skip_locked=True)).all()
    for job in stale:
        job.status, job.completed_at, job.message = 'failed', utcnow(), 'Training was interrupted. Request training again.'
    db.session.commit()
    process_training_job()
    for unit_id in db.session.scalars(select(Unit.id).where(Unit.archived_at.is_(None)).order_by(Unit.id)).all():
        try:
            unit = db.session.get(Unit, unit_id)
            if latest_model(unit):
                generate_forecasts(unit_id)
        except DomainError:
            db.session.rollback()
        except Exception:
            db.session.rollback()
            current_app.logger.exception('forecast_refresh_failed unit=%s', unit_id)
    db.session.remove()
