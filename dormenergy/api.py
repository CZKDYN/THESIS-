import csv
import io
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps
from uuid import uuid4
from zoneinfo import ZoneInfo
from flask import Blueprint, Response, abort, current_app, jsonify, request, session
from flask_login import current_user, login_required
from sqlalchemy import select, func, case, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from werkzeug.security import generate_password_hash
from .extensions import db
from .models import Account, AuditLog, Device, DeviceEvent, EmailOutbox, ForecastJob, ForecastModel, LoadRequest, Notification, Rate, Reading, Transaction, Unit, User, utcnow
from .services.billing import DomainError, aware, decimal_value, lock_unit, resolve_load
from .services.forecasting import forecast_payload
from .services.accounts import change_password, identity, password_value
from .services.emailing import ERRORS, cancel_user_mail, configuration_error, invitation_status, issue_link
from .services.utility_rates import billing_rates, rate_covers, utility_policy
from .services.reporting import paginate, request_query as filtered_requests, transaction_query as filtered_transactions

api = Blueprint('api', __name__)
MANILA = ZoneInfo('Asia/Manila')


def json_payload():
    try:
        payload = request.get_json(silent=True)
    except RecursionError:
        raise DomainError('A valid JSON object is required.') from None
    if not isinstance(payload, dict):
        raise DomainError('A JSON object is required.')
    return payload


def admin_required(function):
    @wraps(function)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.role != 'admin':
            abort(403)
        return function(*args, **kwargs)
    return wrapped


def scoped_units(include_archived=False, include_demo=False):
    query = select(Unit).options(selectinload(Unit.device), selectinload(Unit.account)).order_by(Unit.id)
    if not current_app.config['SIMULATION_MODE'] and not include_demo:
        query = query.where(Unit.is_demo.is_(False))
    if not include_archived:
        query = query.where(Unit.archived_at.is_(None))
    if current_user.role != 'admin':
        query = query.where(Unit.id == current_user.unit_id)
    selected = request.args.get('unit_id')
    if selected:
        try:
            selected = int(selected)
        except ValueError:
            abort(404)
        if current_user.role != 'admin' and selected != current_user.unit_id:
            abort(403)
        query = query.where(Unit.id == selected)
    units = db.session.scalars(query).all()
    if not units and (selected or current_user.role != 'admin'):
        abort(404)
    return units


def iso(value):
    return aware(value).isoformat() if value else None


def transaction_json(item, reference=None):
    return dict(id=item.id, unit_id=item.unit_id, unit=item.unit.name, kind=item.kind,
                amount=str(item.amount), balance_after=str(item.balance_after),
                description=item.description, timestamp=iso(item.created_at), reference=reference)


def load_json(item, credited_amount=None):
    return dict(id=item.id, unit_id=item.unit_id, unit=item.unit.name, tenant=item.user.name,
                amount=str(item.amount), status=item.status, reference=item.reference,
                note=item.note, timestamp=iso(item.created_at), reviewed_at=iso(item.resolved_at),
                credited_amount=str(credited_amount) if credited_amount is not None else None)


def snapshot_context(units):
    """Batch per-unit lookups; reuse the existing device/time unique index."""
    ids = [unit.id for unit in units]
    device_ids = [unit.device.id for unit in units if unit.device]
    latest_id = select(Reading.id).where(Reading.device_id == Device.id, Reading.source == Device.source).order_by(Reading.timestamp.desc()).limit(1).correlate(Device).scalar_subquery()
    latest_ids = select(latest_id).select_from(Device).where(Device.id.in_(device_ids))
    readings = db.session.scalars(select(Reading).where(Reading.id.in_(latest_ids))).all() if device_ids else []
    tenants = db.session.scalars(select(User).where(User.unit_id.in_(ids), User.role == 'tenant', User.tenancy_ended_at.is_(None))).all() if ids else []
    return {'readings': {row.device_id: row for row in readings}, 'tenants': {row.unit_id: row for row in tenants}}


def unit_json(unit, context=None):
    device, account = unit.device, unit.account
    latest = (context['readings'].get(device.id) if context is not None else db.session.scalar(select(Reading).where(Reading.device_id == device.id, Reading.source == device.source).order_by(Reading.timestamp.desc()).limit(1))) if device else None
    now = utcnow()
    recent = bool(device and device.last_seen and 0 <= (now - aware(device.last_seen)).total_seconds() < 30)
    fresh = bool(latest and 0 <= (now - aware(latest.timestamp)).total_seconds() < 90)
    measurement_state = ('awaiting_readings' if latest is None else 'clock_ahead' if aware(latest.timestamp) > now
                         else 'fresh' if fresh else 'stale')
    online = recent and fresh and not unit.archived_at
    tenant = context['tenants'].get(unit.id) if context is not None else current_tenant(unit.id)
    invitation,delivery=invitation_status(tenant) if tenant else (None,None)
    return dict(id=unit.id, name=unit.name, room=unit.room, archived_at=iso(unit.archived_at), tenant=tenant.name if tenant else 'Unassigned',
                balance=str(account.balance) if account else '0', threshold=str(account.low_threshold) if account else '100',
                power_status=unit.power_status if online else 'unknown', last_power_status=unit.power_status,
                online=online, gateway_online=recent,
                last_seen=iso(device.last_seen) if device else None, reading_at=iso(latest.timestamp) if latest else None,
                last_received=iso(latest.received_at) if latest else None, measurement_state=measurement_state,
                communication_state='recent_receipt' if recent else 'no_recent_receipt',
                tenant_id=tenant.id if tenant else None, tenant_email=tenant.email if tenant else None,
                invitation_status=invitation,email_delivery=delivery.status if delivery else None,
                email_delivery_error=ERRORS.get(delivery.error_code) if delivery and delivery.error_code else None,
                device=device.identifier if device else 'Not registered', source=device.source if device else 'hardware', token_provisioned=bool(device and device.token_hash),
                voltage=str(latest.voltage) if latest else None, current=str(latest.current) if latest else None,
                power=str(latest.power) if latest and online else None, energy=str(latest.energy) if latest else None)


def current_tenant(unit_id):
    return db.session.scalar(select(User).where(User.unit_id == unit_id, User.role == 'tenant', User.tenancy_ended_at.is_(None)))


def hourly_totals(devices, since):
    """Aggregate on the database, instead of loading months of ORM readings."""
    if db.engine.dialect.name == 'postgresql':
        hour = func.date_trunc('hour', func.timezone('Asia/Manila', Reading.timestamp))
    else:
        hour = func.strftime('%Y-%m-%d %H:00:00', Reading.timestamp, '+8 hours')
    query = select(Reading.device_id, hour.label('hour'), func.sum(Reading.delta_kwh)).where(
        Reading.device_id.in_(devices), Reading.timestamp >= since, Reading.timestamp <= utcnow(),
        Reading.source == Device.source).join(Device, Device.id == Reading.device_id).group_by(Reading.device_id, hour)
    for device_id, local_hour, energy in db.session.execute(query):
        if isinstance(local_hour, str):
            local_hour = datetime.fromisoformat(local_hour)
        yield device_id, local_hour.replace(tzinfo=MANILA), energy


@api.get('/snapshot')
@login_required
def snapshot():
    units = scoped_units()
    ids = [u.id for u in units]
    devices = [u.device.id for u in units if u.device]
    now = utcnow()
    context = snapshot_context(units)
    unitdata = [unit_json(u, context) for u in units]
    local_now = now.astimezone(MANILA)
    hours = [(local_now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23-i)) for i in range(24)]
    days = [(local_now.date() - timedelta(days=6-i)) for i in range(7)]
    historical_days = [(local_now.date() - timedelta(days=29-i)) for i in range(30)]
    hourly, daily, historical, by_unit = {}, {}, {}, {device: Decimal('0') for device in devices}
    unit_days = {device: set() for device in devices}
    unit_periods = {device: [Decimal('0'), Decimal('0')] for device in devices}
    bucket_devices = {'hourly': {}, 'daily': {}, 'history': {}}
    today = Decimal('0')
    today_devices = set()
    for device_id, local, energy in hourly_totals(devices, now-timedelta(days=30)):
        key = local.replace(minute=0, second=0, microsecond=0)
        hourly[key] = hourly.get(key, Decimal('0')) + energy
        daily[local.date()] = daily.get(local.date(), Decimal('0')) + energy
        historical[local.date()] = historical.get(local.date(), Decimal('0')) + energy
        unit_days[device_id].add(local.date())
        if local.date() >= days[0]: unit_periods[device_id][0] += energy
        if local.date() >= historical_days[0]: unit_periods[device_id][1] += energy
        for period_name, bucket in (('hourly', key), ('daily', local.date()), ('history', local.date())):
            bucket_devices[period_name].setdefault(bucket, set()).add(device_id)
        if local.date() == local_now.date():
            today_devices.add(device_id)
            today += energy
            by_unit[device_id] += energy
    for item, unit in zip(unitdata, units):
        item['today_kwh'] = str(by_unit[unit.device.id]) if unit.device and unit.device.id in today_devices else None
        seen = unit_days.get(unit.device.id, set()) if unit.device else set()
        item['seven_day_kwh'] = str(unit_periods[unit.device.id][0]) if any(d >= days[0] for d in seen) else None
        item['thirty_day_kwh'] = str(unit_periods[unit.device.id][1]) if any(d >= historical_days[0] for d in seen) else None
    rates = billing_rates().where(Rate.effective_at <= now)
    rate = db.session.scalar(rates.order_by(Rate.effective_at.desc()).limit(1))
    if not rate_covers(rate, now):
        rate = None
    request_query = select(LoadRequest).where(LoadRequest.unit_id.in_(ids))
    transaction_query = select(Transaction).where(Transaction.unit_id.in_(ids))
    notification_query = select(Notification).where(Notification.unit_id.in_(ids))
    if current_user.role == 'tenant':
        request_query = request_query.where(LoadRequest.user_id == current_user.id)
        transaction_query = transaction_query.where(Transaction.created_at >= current_user.created_at)
        notification_query = notification_query.where(Notification.created_at >= current_user.created_at)
    pending_count = db.session.scalar(select(func.count()).select_from(request_query.where(LoadRequest.status == 'pending').subquery()))
    period_starts = [datetime.combine(d, datetime.min.time(), MANILA).astimezone(timezone.utc)
                     for d in (local_now.date(), days[0], historical_days[0])]
    expense_query = select(*(func.coalesce(func.sum(case((Transaction.created_at >= start, -Transaction.amount), else_=0)), 0)
                             for start in period_starts)).where(Transaction.unit_id.in_(ids), Transaction.kind == 'consumption', Transaction.created_at <= now)
    credit_query = select(Transaction, LoadRequest.reference).outerjoin(LoadRequest, Transaction.load_request_id == LoadRequest.id).where(
        Transaction.unit_id.in_(ids), Transaction.kind == 'credit', Transaction.created_at <= now)
    if current_user.role == 'tenant':
        expense_query = expense_query.where(Transaction.created_at >= current_user.created_at)
        credit_query = credit_query.where(Transaction.created_at >= current_user.created_at)
    expenses = db.session.execute(expense_query).one()
    latest_credit = db.session.execute(credit_query.order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(1)).first()
    model_id = select(ForecastModel.id).where(ForecastModel.unit_id == Device.unit_id, ForecastModel.source == Device.source).order_by(
        ForecastModel.trained_at.desc(), ForecastModel.id.desc()).limit(1).correlate(Device).scalar_subquery()
    latest_models = dict(db.session.execute(select(ForecastModel.unit_id, ForecastModel.trained_at).where(
        ForecastModel.id.in_(select(model_id).select_from(Device).where(Device.id.in_(devices))))).all()) if devices else {}
    for item in unitdata:
        trained = latest_models.get(item['id'])
        age = max(0, (now-aware(trained)).total_seconds()/86400) if trained else None
        item.update(model_trained_at=iso(trained), model_retraining_due=bool(age is not None and age >= current_app.config['FORECAST_RETRAIN_DAYS']))
    failed_mail = 0
    if current_user.role == 'admin':
        failed_mail = db.session.scalar(select(func.count(EmailOutbox.id)).join(User, User.id == EmailOutbox.user_id).where(
            EmailOutbox.status == 'failed', EmailOutbox.created_at >= now-timedelta(days=7),
            or_(EmailOutbox.unit_id.in_(ids), EmailOutbox.unit_id.is_(None)),
            User.tenancy_ended_at.is_(None), or_(User.active.is_(True), User.activation_status == 'pending'),
            or_(EmailOutbox.error_code.is_(None), EmailOutbox.error_code.not_in(['account_ineligible', 'link_expired', 'retry_window_expired']))))
    operations = dict(active_units=len(unitdata), recent_receipts=sum(u['gateway_online'] for u in unitdata),
        stale_units=sum(u['measurement_state'] == 'stale' for u in unitdata), awaiting_readings=sum(u['measurement_state'] == 'awaiting_readings' for u in unitdata),
        low_balance_units=sum(Decimal(u['balance']) <= Decimal(u['threshold']) for u in unitdata), pending_requests=pending_count,
        pending_activations=sum(bool(u['tenant_id'] and u['invitation_status'] != 'Activated') for u in unitdata),
        failed_emails_recent=failed_mail, retraining_due=sum(u['model_retraining_due'] for u in unitdata))
    actions = []
    if current_user.role == 'admin':
        for key, count, label, detail, view in (
            ('pending_requests', pending_count, 'Review top-up requests', 'Verify payment before approving credit.', 'loads'),
            ('pending_activations', operations['pending_activations'], 'Complete tenant invitations', 'Pending or expired accounts need activation or a replacement invitation.', 'units'),
            ('unassigned', sum(not u['tenant_id'] for u in unitdata), 'Assign tenant accounts', 'These active units have no assigned tenant.', 'units'),
            ('stale_units', sum(not u['online'] for u in unitdata), 'Inspect meter data', 'Fresh measurements and recent receipts are required for live monitoring.', 'devices'),
            ('low_balance_units', operations['low_balance_units'], 'Review low prepaid credit', 'Balances are at or below the configured unit threshold.', 'units'),
            ('failed_emails_recent', failed_mail, 'Review failed email delivery', 'Failed messages from the last seven days for current accounts; check delivery configuration.', 'notifications'),
            ('rate', int(bool(unitdata) and rate is None), 'Confirm billing-rate coverage', 'No currently applicable billing rate is active. The reference price does not authorize charges.', 'settings'),
            ('retraining_due', operations['retraining_due'], 'Review model retraining', 'Check fresh covered data and occupancy changes before requesting training.', 'forecast')):
            if count: actions.append(dict(key=key, count=count, label=label, detail=detail, view=view))
    requests = db.session.scalars(request_query.order_by(LoadRequest.created_at.desc()).limit(100)).all()
    transactions = db.session.scalars(transaction_query.order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(40)).all()
    notifications = db.session.scalars(notification_query.order_by(Notification.created_at.desc()).limit(12)).all()
    events = db.session.scalars(select(DeviceEvent).where(DeviceEvent.device_id.in_(devices)).order_by(DeviceEvent.created_at.desc()).limit(20)).all()
    return jsonify(user=dict(name=current_user.name, role=current_user.role, unit_id=current_user.unit_id),
                   simulation=current_app.config['SIMULATION_MODE'], updated_at=iso(now), units=unitdata,
                   summary=dict(balance=str(sum((Decimal(u['balance']) for u in unitdata), Decimal('0'))),
                                today_kwh=str(today) if today_devices else None, online=sum(u['online'] for u in unitdata),
                                pending=pending_count,
                                power_w=str(sum((Decimal(u['power'] or '0') for u in unitdata), Decimal('0'))) if any(u['online'] for u in unitdata) else None),
                   rate=str(rate.php_per_kwh) if rate else None,
                   utility_rate=utility_policy(),
                   operations=operations, actions=actions,
                   insights=dict(seven_day_kwh=str(sum((Decimal(u['seven_day_kwh']) for u in unitdata if u['seven_day_kwh'] is not None), Decimal('0'))) if any(u['seven_day_kwh'] is not None for u in unitdata) else None,
                       thirty_day_kwh=str(sum((Decimal(u['thirty_day_kwh']) for u in unitdata if u['thirty_day_kwh'] is not None), Decimal('0'))) if any(u['thirty_day_kwh'] is not None for u in unitdata) else None,
                       deductions=dict(today=str(expenses[0]), seven_days=str(expenses[1]), thirty_days=str(expenses[2])),
                       latest_topup=transaction_json(*latest_credit) if latest_credit else None,
                       comparison=None, comparison_note='Recorded totals may contain gaps or partial days. A complete comparable-period coverage record is not available, so no percentage change is asserted.'),
                   charts=dict(hourly=dict(labels=[h.strftime('%I %p').lstrip('0') for h in hours], values=[float(hourly[h]) if h in hourly else None for h in hours], timestamps=[h.isoformat() for h in hours], observed_units=[len(bucket_devices['hourly'].get(h, set())) for h in hours]),
                               daily=dict(labels=[d.strftime('%a') for d in days], values=[float(daily[d]) if d in daily else None for d in days], timestamps=[d.isoformat() for d in days], observed_units=[len(bucket_devices['daily'].get(d, set())) for d in days]),
                               history=dict(labels=[d.strftime('%b %d') for d in historical_days], values=[float(historical[d]) if d in historical else None for d in historical_days], timestamps=[d.isoformat() for d in historical_days], observed_units=[len(bucket_devices['history'].get(d, set())) for d in historical_days])),
                   requests=[load_json(r) for r in requests], transactions=[transaction_json(t) for t in transactions],
                   notifications=[dict(id=n.id, unit_id=n.unit_id, kind=n.kind, message=n.message, timestamp=iso(n.created_at)) for n in notifications],
                   events=[dict(device_id=e.device_id, kind=e.kind, state=e.state, message=e.message, timestamp=iso(e.created_at)) for e in events],
                   forecast=forecast_payload(units[0], now) if len(units) == 1 else
                   dict(status='select_unit', message='Choose a dormitory unit',
                        detail='Select a unit above to view its XGBoost model and forecast.',
                        remaining_duration=None, horizon_hours=0, model=None, points=[], job=None))


@api.get('/transactions')
@login_required
def get_transactions():
    ids = [unit.id for unit in scoped_units()]
    rows, paging = paginate(filtered_transactions(ids, current_user, request.args), request.args, Transaction)
    return jsonify(transactions=[transaction_json(item, reference) for item, reference in rows], pagination=paging)


@api.get('/load-requests')
@login_required
def get_load_requests():
    ids = [unit.id for unit in scoped_units()]
    rows, paging = paginate(filtered_requests(ids, current_user, request.args), request.args, LoadRequest)
    return jsonify(requests=[load_json(item, amount) for item, amount in rows], pagination=paging)


@api.post('/load-requests')
@login_required
def create_load_request():
    if current_user.role != 'tenant':
        abort(403)
    lock_unit(current_user.unit_id)
    actor = db.session.scalar(select(User).where(User.id == current_user.id).with_for_update().execution_options(populate_existing=True))
    if (not actor.active or actor.activation_status != 'active' or actor.tenancy_ended_at or actor.must_change_password
            or session.get('auth_version') != actor.session_version):
        raise DomainError('Your tenancy or sign-in session changed. Sign in again.', 403)
    payload = json_payload()
    amount = decimal_value(payload.get('amount'), 'amount', Decimal('100000'))
    if amount < 10 or amount != amount.quantize(Decimal('0.01')):
        raise DomainError('Enter PHP 10.00–100,000.00 with at most two decimal places.')
    reference = payload.get('reference') or str(uuid4())
    note = payload.get('note', '')
    if not isinstance(reference, str) or not 1 <= len(reference) <= 64 or not isinstance(note, str) or len(note) > 300:
        raise DomainError('Invalid request reference or note.')
    pending = db.session.scalars(select(LoadRequest.id).where(LoadRequest.user_id == current_user.id, LoadRequest.status == 'pending')).all()
    if len(pending) >= 10:
        raise DomainError('You already have 10 pending requests. Please wait for review.', 409)
    load = LoadRequest(user_id=current_user.id, unit_id=current_user.unit_id, amount=amount,
                       reference=reference, note=note.strip())
    db.session.add(load)
    db.session.add(AuditLog(actor_id=current_user.id, action='load_requested', detail=f'Unit {current_user.unit_id}, PHP {amount}'))
    db.session.commit()
    return jsonify(request=load_json(load), message='Your load request has been sent for review.'), 201


@api.get('/forecast')
@login_required
def get_forecast():
    units = scoped_units()
    if len(units) != 1:
        return jsonify(status='select_unit', message='Choose a dormitory unit',
                       detail='Each meter has its own model and historical evaluation.',
                       model=None, points=[], remaining_duration=None, horizon_hours=0, job=None)
    return jsonify(forecast_payload(units[0]))


@api.post('/units/<int:unit_id>/forecast/train')
@admin_required
def queue_forecast_training(unit_id):
    unit = lock_unit(unit_id)
    if not unit.device:
        raise DomainError('Register a meter for this unit first.')
    if unit.device.source == 'simulation' and not current_app.config['SIMULATION_MODE']:
        raise DomainError('Synthetic model training requires simulation mode.', 409)
    job = db.session.scalar(select(ForecastJob).where(
        ForecastJob.unit_id == unit_id, ForecastJob.status.in_(['pending', 'running'])).limit(1))
    if job:
        return jsonify(message='Training is already queued or running.', job_id=job.id), 202
    job = ForecastJob(unit_id=unit_id, requested_by=current_user.id)
    db.session.add(job)
    db.session.add(AuditLog(actor_id=current_user.id, action='forecast_requested',
                           detail=f'XGBoost training requested for unit {unit_id}; {unit.device.source}.'))
    db.session.commit()
    return jsonify(message='XGBoost training queued. This page will show progress automatically.', job_id=job.id), 202


@api.post('/load-requests/<int:request_id>/review')
@admin_required
def review_load(request_id):
    payload = json_payload()
    load = resolve_load(request_id, payload.get('decision'), current_user)
    db.session.commit()
    return jsonify(request=load_json(load), message=f'Load request {load.status}.')


@api.post('/rates')
@admin_required
def update_rate():
    if current_app.config['UTILITY_RATE_LOCKED']:
        raise DomainError('The electricity rate is locked to the utility configuration. Arbitrary rate changes are disabled.', 403)
    payload = json_payload()
    rate = decimal_value(payload.get('rate'), 'rate', Decimal('1000'))
    if rate <= 0 or rate != rate.quantize(Decimal('0.0001')):
        raise DomainError('The rate must be positive with at most four decimal places.')
    effective = utcnow()
    if payload.get('effective_at'):
        try:
            effective = datetime.fromisoformat(payload['effective_at'].replace('Z', '+00:00'))
            if effective.tzinfo is None or effective < utcnow():
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise DomainError('Scheduled rates require a future ISO 8601 timestamp with timezone.') from None
    db.session.add(Rate(php_per_kwh=rate, effective_at=effective, created_by=current_user.id))
    db.session.add(AuditLog(actor_id=current_user.id, action='rate_changed', detail=f'PHP {rate}/kWh effective {effective.isoformat()}'))
    db.session.commit()
    return jsonify(message='Electricity rate saved. Previous charges are preserved.')


@api.get('/rates')
@admin_required
def rate_history():
    query = select(Rate)
    if not current_app.config['SIMULATION_MODE']:
        query = query.where(Rate.is_demo.is_(False))
    rates = db.session.scalars(query.order_by(Rate.effective_at.desc()).limit(50)).all()
    active = db.session.scalar(billing_rates().where(Rate.effective_at <= utcnow()).order_by(Rate.effective_at.desc()).limit(1))
    if not rate_covers(active, utcnow()):
        active = None
    return jsonify(policy=utility_policy(), active_rate=str(active.php_per_kwh) if active else None,
                   active_period=active.billing_period if active else None,
                   active_source=active.source_reference if active else None,
                   active_valid_until=iso(active.valid_until) if active else None,
                   rates=[dict(rate=str(r.php_per_kwh), effective_at=iso(r.effective_at), valid_until=iso(r.valid_until),
                               provider=r.utility_provider, period=r.billing_period, source=r.source_reference,
                               owner_confirmed=bool(r.configuration_key)) for r in rates])


@api.post('/units')
@admin_required
def register_unit():
    payload = json_payload()
    name, room, identifier = (payload.get(k, '') for k in ('name', 'room', 'device_id'))
    if any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 80 for x in (name, room, identifier)):
        raise DomainError('Unit name, room, and device identifier are required (up to 80 characters).')
    if not current_app.config['SIMULATION_MODE']:
        legacy = db.session.scalar(select(Unit).where(Unit.name == name.strip(), Unit.is_demo.is_(True)).with_for_update())
        if legacy:
            original = legacy.name
            legacy.name = f'{original[:60]} (demo {legacy.id})'
            db.session.add(AuditLog(actor_id=current_user.id, action='demo_label_retired', detail=f'Demo unit {legacy.id} originally named {original}; label reserved for a newly registered physical unit.'))
            db.session.flush()
    unit = Unit(name=name.strip(), room=room.strip())
    db.session.add(unit)
    db.session.flush()
    db.session.add(Account(unit_id=unit.id, balance=0, low_threshold=Decimal(current_app.config['LOW_BALANCE_THRESHOLD'])))
    db.session.add(Device(identifier=identifier.strip(), unit_id=unit.id, source='hardware'))
    db.session.add(AuditLog(actor_id=current_user.id, action='unit_registered', detail=f'Unit {unit.id} registered. Device monitoring only.'))
    db.session.commit()
    return jsonify(message='Unit registered. Assign a tenant and generate its device token in Device monitoring.', id=unit.id), 201


@api.get('/units')
@admin_required
def list_units():
    status = request.args.get('status', 'active')
    if status not in ('active', 'archived', 'demo'):
        raise DomainError('Choose active units, archived units, or demonstration history.')
    query = select(Unit).order_by(Unit.id)
    if status == 'demo':
        query = query.where(Unit.is_demo.is_(True))
    else:
        query = query.where(Unit.archived_at.is_(None) if status == 'active' else Unit.archived_at.is_not(None))
        if not current_app.config['SIMULATION_MODE']:
            query = query.where(Unit.is_demo.is_(False))
    return jsonify(units=[unit_json(unit) for unit in db.session.scalars(query).all()])


@api.post('/units/<int:unit_id>/archive')
@admin_required
def archive_unit(unit_id):
    unit = lock_unit(unit_id)
    pending = db.session.scalars(select(LoadRequest.id).where(
        LoadRequest.unit_id == unit.id, LoadRequest.status == 'pending').order_by(LoadRequest.id)).all()
    for request_id in pending:
        resolve_load(request_id, 'rejected', current_user)
    unit.archived_at = utcnow()
    tenant = current_tenant(unit.id)
    if tenant:
        tenant.active = False
        cancel_user_mail(tenant)
    db.session.add(AuditLog(actor_id=current_user.id, action='unit_archived',
                           detail=f'Unit {unit.id} ({unit.name}) removed; records retained; {len(pending)} pending requests rejected.'))
    db.session.commit()
    return jsonify(message='Unit removed. Records are retained under Archived units.', id=unit.id)


@api.post('/units/<int:unit_id>/restore')
@admin_required
def restore_unit(unit_id):
    unit = lock_unit(unit_id, allow_archived=True)
    if not unit.archived_at:
        raise DomainError('This unit is already active.', 409)
    unit.archived_at = None
    tenant = current_tenant(unit.id)
    if tenant:
        tenant.active = tenant.activation_status=='active'
    db.session.add(AuditLog(actor_id=current_user.id, action='unit_restored',
                           detail=f'Unit {unit.id} ({unit.name}) restored with its existing records and account.'))
    db.session.commit()
    return jsonify(message='Unit restored. Its existing tenant account can sign in again.', id=unit.id)


@api.post('/units/<int:unit_id>/tenant')
@admin_required
def assign_tenant(unit_id):
    lock_unit(unit_id)
    payload = json_payload()
    name, email = identity(payload.get('name'), payload.get('email'))
    if current_tenant(unit_id):
        raise DomainError('This unit already has an assigned tenant.', 409)
    method=payload.get('registration_method') or ('temporary_password' if payload.get('password') else 'email')
    if method not in ('email','temporary_password'):
        raise DomainError('Choose email invitation or temporary password.')
    user = User(name=name,email=email,role='tenant',unit_id=unit_id,must_change_password=method=='temporary_password',
                active=method=='temporary_password',activation_status='active' if method=='temporary_password' else 'pending')
    user.set_password(password_value(payload.get('password')) if method=='temporary_password' else secrets.token_urlsafe(40))
    db.session.add(user)
    db.session.flush()
    delivery=issue_link(user,'activation') if method=='email' else None
    db.session.add(AuditLog(actor_id=current_user.id, action='tenant_assigned', detail=f'Tenant assigned to unit {unit_id}'))
    db.session.commit()
    return jsonify(message=('Pending tenant account created. Invitation queued; delivery is still pending.' if delivery.status=='pending' else 'Pending tenant account created. Invitation was not sent. '+ERRORS.get(delivery.error_code,'Check email configuration.')) if delivery else 'Tenant account created and assigned.',
                   invitation_status='Pending' if delivery else 'Activated', email_delivery=delivery.status if delivery else None), 201


@api.post('/units/<int:unit_id>/tenant/invitation')
@admin_required
def resend_invitation(unit_id):
    lock_unit(unit_id)
    user=current_tenant(unit_id)
    if not user or user.activation_status!='pending':
        raise DomainError('Only a pending tenant account can receive an invitation.',409)
    delivery=issue_link(user,'activation')
    db.session.add(AuditLog(actor_id=current_user.id,action='invitation_resent',detail=f'Invitation replaced for tenant {user.id}.'))
    db.session.commit()
    return jsonify(message='Replacement invitation queued; delivery is pending.' if delivery.status=='pending' else 'Invitation was not sent. '+ERRORS.get(delivery.error_code,'Check email configuration.'),email_delivery=delivery.status)


@api.get('/email-status')
@admin_required
def email_status():
    rows=db.session.scalars(select(EmailOutbox).order_by(EmailOutbox.id.desc()).limit(50)).all()
    return jsonify(backend=current_app.config['EMAIL_BACKEND'],configured=configuration_error() is None,
        configuration_message=ERRORS.get(configuration_error(),'Delivery configuration is present; provider acceptance is tracked below.'),
        messages=[dict(id=row.id,kind=row.kind,status=row.status,attempts=row.attempts,
                       error=ERRORS.get(row.error_code),created_at=iso(row.created_at),next_attempt_at=iso(row.next_attempt_at),
                       user_id=row.user_id) for row in rows])


@api.post('/units/<int:unit_id>/edit')
@admin_required
def edit_unit(unit_id):
    unit = lock_unit(unit_id)
    payload = json_payload()
    name, room = payload.get('name'), payload.get('room')
    if any(not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 for value in (name, room)):
        raise DomainError('Enter a unit name and room of 1–80 characters.')
    threshold = decimal_value(payload.get('threshold'), 'threshold', Decimal('100000'))
    if threshold != threshold.quantize(Decimal('0.01')):
        raise DomainError('Use at most two decimal places for the low-credit threshold.')
    unit.name, unit.room, unit.account.low_threshold = name.strip(), room.strip(), threshold
    db.session.add(AuditLog(actor_id=current_user.id, action='unit_updated', detail=f'Unit {unit.id} details and low-credit threshold updated.'))
    db.session.commit()
    return jsonify(message='Unit details saved. Existing balance and history are preserved.')


@api.post('/units/<int:unit_id>/tenant/password')
@admin_required
def reset_tenant_password(unit_id):
    lock_unit(unit_id)
    tenant = current_tenant(unit_id)
    if not tenant:
        raise DomainError('Assign a tenant first.', 409)
    tenant = db.session.scalar(select(User).where(User.id == tenant.id).with_for_update().execution_options(populate_existing=True))
    tenant.active=True
    tenant.activation_status='active'
    change_password(tenant, json_payload().get('password'), temporary=True)
    db.session.add(AuditLog(actor_id=current_user.id, action='tenant_password_reset', detail=f'Tenant {tenant.id} password reset; sessions revoked.'))
    db.session.commit()
    return jsonify(message='Temporary password set. Share it securely; the tenant must change it at sign-in.')


@api.post('/units/<int:unit_id>/tenant/end')
@admin_required
def end_tenancy(unit_id):
    unit = lock_unit(unit_id)
    tenant = current_tenant(unit_id)
    if not tenant:
        raise DomainError('This unit has no current tenant.', 409)
    pending = db.session.scalars(select(LoadRequest.id).where(LoadRequest.user_id == tenant.id, LoadRequest.status == 'pending').order_by(LoadRequest.id)).all()
    for request_id in pending:
        resolve_load(request_id, 'rejected', current_user)
    tenant.active, tenant.tenancy_ended_at = False, utcnow()
    tenant.session_version += 1
    cancel_user_mail(tenant)
    db.session.add(AuditLog(actor_id=current_user.id, action='tenancy_ended', detail=f'Tenant {tenant.id} moved out of unit {unit.id}; records and unit balance retained.'))
    db.session.commit()
    return jsonify(message='Tenant access ended. Records and the unit balance are retained; you may assign the next tenant.')


@api.post('/units/<int:unit_id>/device-token')
@admin_required
def device_token(unit_id):
    unit = lock_unit(unit_id)
    if not unit.device or unit.device.source != 'hardware':
        raise DomainError('Choose a registered hardware meter.', 409)
    payload = json_payload()
    actor = db.session.scalar(select(User).where(User.id == current_user.id).with_for_update().execution_options(populate_existing=True))
    password = payload.get('current_password', '')
    if not isinstance(password, str) or len(password)>128 or not actor.check_password(password):
        raise DomainError('Your current password is incorrect.', 403)
    action = payload.get('action', 'generate')
    if action not in ('generate', 'revoke'):
        raise DomainError('Choose generate or revoke.')
    token = secrets.token_urlsafe(40) if action == 'generate' else None
    unit.device.token_hash = generate_password_hash(token) if token else None
    db.session.add(AuditLog(actor_id=current_user.id, action='device_token_'+action, detail=f'Device {unit.device.id} credential changed; previous token invalidated.'))
    db.session.commit()
    return jsonify(message='Store this token securely on your gateway. It is shown only once.' if token else 'Device token revoked. Future transmissions need a new token.',
                   token=token, device_id=unit.device.identifier, unit_id=unit.id, ingestion_path='/api/iot/readings')


@api.get('/transactions/export')
@login_required
def export_transactions():
    include_archived = current_user.role == 'admin' and request.args.get('include_archived') == 'true'
    include_demo = current_user.role == 'admin' and request.args.get('include_demo') == 'true'
    ids = [u.id for u in scoped_units(include_archived=include_archived, include_demo=include_demo)]
    query = filtered_transactions(ids, current_user, request.args)
    rows = db.session.execute(query.limit(10000)).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['transaction_id', 'unit', 'timestamp_utc', 'kind', 'amount_php', 'balance_after_php', 'description'])
    def safe(value):
        text = str(value)
        return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
    for row, reference in rows:
        writer.writerow([row.id, safe(row.unit.name), iso(row.created_at), row.kind, str(row.amount), str(row.balance_after), safe(row.description)])
    return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=dormenergy-transactions.csv'})


@api.errorhandler(DomainError)
def domain_error(error):
    db.session.rollback()
    return jsonify(error=error.message), error.status


@api.errorhandler(IntegrityError)
def duplicate_error(error):
    db.session.rollback()
    return jsonify(error='This identifier or request already exists. No duplicate change was applied.'), 409
