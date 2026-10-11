import math
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from dormenergy.extensions import db
from dormenergy.models import Account, Device, Forecast, ForecastJob, ForecastModel, Reading, Transaction, Unit, utcnow
from dormenergy.services.billing import DomainError
from dormenergy.services.forecasting import (FEATURES, credit_duration, features, floor_hour,
    forecast_payload, forecast_worker_step, generate_forecasts, hourly_history, metrics,
    process_training_job, train_model)
from conftest import login, post

HOUR = timedelta(hours=1)


def history(app, unit_id=2, hours=168, end=None):
    end = end or floor_hour(utcnow())
    with app.app_context():
        device = db.session.scalar(select(Device).where(Device.unit_id == unit_id))
        total = Decimal('0')
        for index in range(hours+1):
            timestamp = end-HOUR*(hours-index)
            delta = Decimal(str(round(0.2+0.06*math.sin((timestamp.hour+7)*math.pi/12)**2, 6))) if index else Decimal('0')
            total += delta
            db.session.add(Reading(device_id=device.id, event_id=f'history-{index}', timestamp=timestamp,
                voltage=230, current=1, power=230, energy=total, delta_kwh=delta, rate_id=1,
                source=device.source, meter_epoch='test-history', connection_status='connected'))
        db.session.commit()
    return end


def record(timestamp, energy, delta, epoch='same', event='observed', source='hardware'):
    return SimpleNamespace(timestamp=timestamp, energy=Decimal(energy), delta_kwh=Decimal(delta),
                           source=source, meter_epoch=epoch, event_id=event)


def test_hourly_coverage_avoids_future_endpoint_leakage_and_gaps():
    start = floor_hour(utcnow())-3*HOUR
    rows = [record(start, '0', '0'), record(start+timedelta(seconds=3590), '0.997', '0.997'),
            record(start+HOUR+timedelta(seconds=10), '1.002', '0.005')]
    values, coverage = hourly_history(rows, 'hardware', start+2*HOUR)
    assert coverage['complete_hours'] == 1
    assert values[start] == pytest.approx(0.997)  # Later endpoint did not fill the earlier hour.
    assert start+HOUR not in values
    gaps = [record(start, '0', '0'), record(start+2*HOUR, '1', '1')]
    assert hourly_history(gaps, 'hardware', start+3*HOUR)[0] == {}
    resets = [record(start, '10', '0'), record(start+HOUR, '0', '0', epoch='reset'),
              record(start+2*HOUR, '1', '1', epoch='reset')]
    assert set(hourly_history(resets, 'hardware', start+3*HOUR)[0]) == {start+HOUR}
    pause = [record(start, '0', '0', source='simulation'),
             record(start+HOUR, '0', '0', source='simulation', event='sim-after-pause')]
    assert hourly_history(pause, 'simulation', start+2*HOUR)[0] == {}
    assert hourly_history(rows, 'simulation', start+3*HOUR)[0] == {}  # Sources cannot mix.


def test_features_are_past_only_and_zero_mape_is_explicit():
    hour = floor_hour(utcnow())
    observed = {hour-HOUR*i: float(i) for i in range(1, 25)}
    before = features(hour, observed)
    observed[hour], observed[hour+HOUR] = 9999, 9999
    assert features(hour, observed) == before
    assert before[FEATURES.index('lag_1')] == 1
    assert before[FEATURES.index('lag_24')] == 24
    result = metrics([0, 1, 2], [1, 1, 3])
    assert result['mape'] == 25 and result['mape_zero_excluded'] == 1
    assert metrics([0, 0], [0, 0])['mape'] is None


def test_real_model_roundtrip_chronological_evaluation_and_scoped_forecasts(app, client):
    # A real XGBoost implementation, trained on explicitly synthetic test readings.
    now = history(app)
    with app.app_context():
        model = train_model(2, now=now)
        report = model.metadata_json
        assert model.model_json.startswith('{') and report['synthetic']
        assert report['splits']['train']['end'] <= report['splits']['validation']['start']
        assert report['splits']['validation']['end'] <= report['splits']['test']['start']
        actual = [point['actual'] for point in report['comparison']]
        predicted = [point['predicted'] for point in report['comparison']]
        assert report['test']['mae'] == pytest.approx(metrics(actual, predicted)['mae'])
        assert report['test']['rmse'] == pytest.approx(metrics(actual, predicted)['rmse'])
        generate_forecasts(2, now=now)
        generate_forecasts(2, now=now)  # Idempotent for the issue-hour.
        assert db.session.scalar(select(func.count(Forecast.id))) == 24
        payload = forecast_payload(db.session.get(Unit, 2), now=now)
        assert payload['status'] == 'available' and payload['horizon_hours'] == 24
        assert all(math.isfinite(float(p['kwh'])) and float(p['kwh']) >= 0 for p in payload['points'])
        assert payload['remaining_duration'] is None  # Balance cannot exhaust within this horizon.
        assert db.session.get(Account, 2).balance == 100
        assert db.session.scalar(select(func.count(Transaction.id))) == 0
    login(client, 'two@test.local')
    assert client.get('/api/forecast').json['synthetic']
    assert client.get('/api/forecast?unit_id=1').status_code == 403
    assert post(client, '/api/units/2/forecast/train', {}).status_code == 403


def test_administrator_training_queue_worker_and_insufficient_data(app, client):
    history(app)
    login(client)
    assert client.post('/api/units/2/forecast/train', json={}).status_code == 400
    first = post(client, '/api/units/2/forecast/train', {})
    assert first.status_code == 202
    assert post(client, '/api/units/2/forecast/train', {}).json['job_id'] == first.json['job_id']
    with app.app_context():
        assert process_training_job()
        job = db.session.get(ForecastJob, first.json['job_id'])
        assert job.status == 'completed' and job.model_version
        assert db.session.scalar(select(func.count(ForecastModel.id))) == 1
    response = client.get('/api/forecast?unit_id=2').json
    assert response['model']['synthetic'] and response['job']['status'] == 'completed'
    failing = post(client, '/api/units/1/forecast/train', {})
    with app.app_context():
        assert process_training_job()
        failed = db.session.get(ForecastJob, failing.json['job_id'])
        assert failed.status == 'failed' and '30 days' in failed.message
        assert db.session.scalar(select(func.count(ForecastModel.id))) == 1
    assert post(client, '/api/units/2/archive', {}).status_code == 200
    assert post(client, '/api/units/2/forecast/train', {}).status_code == 409
    assert client.get('/api/forecast?unit_id=2').status_code == 404


def test_source_requirements_and_stale_forecasts_are_honest(app):
    now = history(app, unit_id=1)
    with app.app_context():
        with pytest.raises(DomainError, match='30 days'):
            train_model(1, now=now)
        db.session.rollback()
    now = history(app, unit_id=2)
    with app.app_context():
        app.config['SIMULATION_MODE'] = False
        with pytest.raises(DomainError, match='simulation mode'):
            train_model(2, now=now)
        app.config['SIMULATION_MODE'] = True
        train_model(2, now=now)
        generate_forecasts(2, now=now)
        app.config['SIMULATION_MODE'] = False
        with pytest.raises(DomainError, match='simulation mode'):
            generate_forecasts(2, now=now+HOUR)
        app.config['SIMULATION_MODE'] = True
        payload = forecast_payload(db.session.get(Unit, 2), now=now+3*timedelta(days=1))
        assert payload['status'] == 'waiting_for_readings'
        assert payload['points'] == [] and payload['remaining_duration'] is None
        with pytest.raises(DomainError, match='recent hourly'):
            generate_forecasts(2, now=now+3*timedelta(days=1))


def test_credit_duration_respects_scheduled_rates_and_forecast_horizon():
    start = floor_hour(utcnow())
    curve = [SimpleNamespace(timestamp=start+i*HOUR, predicted_kwh=Decimal('1')) for i in range(2)]
    rates = [SimpleNamespace(effective_at=start-HOUR, php_per_kwh=Decimal('10')),
             SimpleNamespace(effective_at=start+timedelta(minutes=30), php_per_kwh=Decimal('20'))]
    assert credit_duration('5', curve, rates, start) == 1800
    assert credit_duration('15', curve, rates, start) == 3600
    assert credit_duration('0', curve, rates, start) == 0
    assert credit_duration('100', curve, rates, start) is None
    assert credit_duration('1', curve[1:], rates, start) is None  # Missing first interval.
    zero = [SimpleNamespace(timestamp=start, predicted_kwh=Decimal('0'))]
    assert credit_duration('1', zero, rates, start) is None


def test_hardware_model_uses_its_own_thirty_day_history(app):
    now = history(app, unit_id=1, hours=720)
    with app.app_context():
        model = train_model(1, now=now)
        assert model.source == 'hardware' and not model.metadata_json['synthetic']
        assert model.metadata_json['coverage']['complete_hours'] == 720
        assert model.metadata_json['test']['samples'] >= 24
        assert len(model.metadata_json['comparison']) == 72
        assert db.session.scalar(select(func.count(ForecastModel.id)).where(ForecastModel.unit_id == 2)) == 0


def test_worker_marks_interrupted_training_failed(app):
    with app.app_context():
        job = ForecastJob(unit_id=2, requested_by=1, status='running', started_at=utcnow()-timedelta(minutes=16))
        db.session.add(job)
        db.session.commit()
        job_id = job.id
        forecast_worker_step()
        recovered = db.session.get(ForecastJob, job_id)
        assert recovered.status == 'failed' and 'interrupted' in recovered.message
