import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps
from uuid import uuid4
from zoneinfo import ZoneInfo
from flask import Blueprint, Response, abort, current_app, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .extensions import db
from .models import Account, AuditLog, Device, DeviceEvent, ForecastJob, LoadRequest, Notification, Rate, Reading, Transaction, Unit, User, utcnow
from .services.billing import DomainError, aware, decimal_value, lock_unit, resolve_load
from .services.forecasting import forecast_payload

api = Blueprint('api', __name__)
MANILA = ZoneInfo('Asia/Manila')


def json_payload():
    payload = request.get_json(silent=True)
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


def scoped_units(include_archived=False):
    query = select(Unit).order_by(Unit.id)
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


def transaction_json(item):
    return dict(id=item.id, unit_id=item.unit_id, unit=item.unit.name, kind=item.kind,
                amount=str(item.amount), balance_after=str(item.balance_after),
                description=item.description, timestamp=iso(item.created_at))


def load_json(item):
    return dict(id=item.id, unit_id=item.unit_id, unit=item.unit.name, tenant=item.user.name,
                amount=str(item.amount), status=item.status, reference=item.reference,
                note=item.note, timestamp=iso(item.created_at))


def unit_json(unit):
    device, account = unit.device, unit.account
    latest = db.session.scalar(select(Reading).where(Reading.device_id == device.id).order_by(Reading.timestamp.desc()).limit(1)) if device else None
    recent = bool(device and device.last_seen and (utcnow() - aware(device.last_seen)).total_seconds() < 30)
    fresh = bool(latest and (utcnow() - aware(latest.timestamp)).total_seconds() < 90)
    online = recent and fresh and not unit.archived_at
    tenant = db.session.scalar(select(User).where(User.unit_id == unit.id, User.role == 'tenant'))
    return dict(id=unit.id, name=unit.name, room=unit.room, archived_at=iso(unit.archived_at), tenant=tenant.name if tenant else 'Unassigned',
                balance=str(account.balance) if account else '0', threshold=str(account.low_threshold) if account else '100',
                power_status=unit.power_status if online else 'unknown', last_power_status=unit.power_status,
                online=online, gateway_online=recent,
                last_seen=iso(device.last_seen) if device else None, reading_at=iso(latest.timestamp) if latest else None,
                device=device.identifier if device else 'Not registered', source=device.source if device else 'hardware',
                voltage=str(latest.voltage) if latest else None, current=str(latest.current) if latest else None,
                power=str(latest.power) if latest and online else None, energy=str(latest.energy) if latest else '0')


@api.get('/snapshot')
@login_required
def snapshot():
    units = scoped_units()
    ids = [u.id for u in units]
    devices = [u.device.id for u in units if u.device]
    now = utcnow()
    rows = db.session.scalars(select(Reading).where(Reading.device_id.in_(devices), Reading.timestamp >= now - timedelta(days=30)).order_by(Reading.timestamp)).all()
    unitdata = [unit_json(u) for u in units]
    local_now = now.astimezone(MANILA)
    hours = [(local_now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23-i)) for i in range(24)]
    days = [(local_now.date() - timedelta(days=6-i)) for i in range(7)]
    historical_days = [(local_now.date() - timedelta(days=29-i)) for i in range(30)]
    hourly, daily, historical, by_unit = {}, {}, {}, {device: Decimal('0') for device in devices}
    today = Decimal('0')
    for row in rows:
        local = aware(row.timestamp).astimezone(MANILA)
        key = local.replace(minute=0, second=0, microsecond=0)
        hourly[key] = hourly.get(key, Decimal('0')) + row.delta_kwh
        daily[local.date()] = daily.get(local.date(), Decimal('0')) + row.delta_kwh
        historical[local.date()] = historical.get(local.date(), Decimal('0')) + row.delta_kwh
        if local.date() == local_now.date():
            today += row.delta_kwh
            by_unit[row.device_id] += row.delta_kwh
    for item, unit in zip(unitdata, units):
        item['today_kwh'] = str(by_unit.get(unit.device.id, Decimal('0'))) if unit.device else '0'
    rate = db.session.scalar(select(Rate).where(Rate.effective_at <= now).order_by(Rate.effective_at.desc()).limit(1))
    requests = db.session.scalars(select(LoadRequest).where(LoadRequest.unit_id.in_(ids)).order_by(LoadRequest.created_at.desc()).limit(100)).all()
    transactions = db.session.scalars(select(Transaction).where(Transaction.unit_id.in_(ids)).order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(40)).all()
    notifications = db.session.scalars(select(Notification).where(Notification.unit_id.in_(ids)).order_by(Notification.created_at.desc()).limit(12)).all()
    events = db.session.scalars(select(DeviceEvent).where(DeviceEvent.device_id.in_(devices)).order_by(DeviceEvent.created_at.desc()).limit(20)).all()
    return jsonify(user=dict(name=current_user.name, role=current_user.role, unit_id=current_user.unit_id),
                   simulation=current_app.config['SIMULATION_MODE'], updated_at=iso(now), units=unitdata,
                   summary=dict(balance=str(sum((Decimal(u['balance']) for u in unitdata), Decimal('0'))),
                                today_kwh=str(today), online=sum(u['online'] for u in unitdata),
                                pending=sum(r.status == 'pending' for r in requests),
                                power_w=str(sum((Decimal(u['power'] or '0') for u in unitdata), Decimal('0')))),
                   rate=str(rate.php_per_kwh) if rate else None,
                   charts=dict(hourly=dict(labels=[h.strftime('%I %p').lstrip('0') for h in hours], values=[float(hourly[h]) if h in hourly else None for h in hours]),
                               daily=dict(labels=[d.strftime('%a') for d in days], values=[float(daily[d]) if d in daily else None for d in days]),
                               history=dict(labels=[d.strftime('%b %d') for d in historical_days], values=[float(historical[d]) if d in historical else None for d in historical_days])),
                   requests=[load_json(r) for r in requests], transactions=[transaction_json(t) for t in transactions],
                   notifications=[dict(id=n.id, unit_id=n.unit_id, kind=n.kind, message=n.message, timestamp=iso(n.created_at)) for n in notifications],
                   events=[dict(device_id=e.device_id, kind=e.kind, state=e.state, message=e.message, timestamp=iso(e.created_at)) for e in events],
                   forecast=forecast_payload(units[0], now) if len(units) == 1 else
                   dict(status='select_unit', message='Choose a dormitory unit',
                        detail='Select a unit above to view its XGBoost model and forecast.',
                        remaining_duration=None, horizon_hours=0, model=None, points=[], job=None))


@api.post('/load-requests')
@login_required
def create_load_request():
    if current_user.role != 'tenant':
        abort(403)
    lock_unit(current_user.unit_id)
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
    rates = db.session.scalars(select(Rate).order_by(Rate.effective_at.desc()).limit(50)).all()
    return jsonify(rates=[dict(rate=str(r.php_per_kwh), effective_at=iso(r.effective_at)) for r in rates])


@api.post('/units')
@admin_required
def register_unit():
    payload = json_payload()
    name, room, identifier = (payload.get(k, '') for k in ('name', 'room', 'device_id'))
    if any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 80 for x in (name, room, identifier)):
        raise DomainError('Unit name, room, and device identifier are required (up to 80 characters).')
    unit = Unit(name=name.strip(), room=room.strip())
    db.session.add(unit)
    db.session.flush()
    db.session.add(Account(unit_id=unit.id, balance=0, low_threshold=Decimal(current_app.config['LOW_BALANCE_THRESHOLD'])))
    db.session.add(Device(identifier=identifier.strip(), unit_id=unit.id, source='hardware'))
    db.session.add(AuditLog(actor_id=current_user.id, action='unit_registered', detail=f'Unit {unit.id} registered. Device monitoring only.'))
    db.session.commit()
    return jsonify(message='Unit registered. Provision device authentication using the documented CLI.', id=unit.id), 201


@api.get('/units')
@admin_required
def list_units():
    status = request.args.get('status', 'active')
    if status not in ('active', 'archived'):
        raise DomainError('Choose active or archived units.')
    query = select(Unit).order_by(Unit.id)
    query = query.where(Unit.archived_at.is_(None) if status == 'active' else Unit.archived_at.is_not(None))
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
    tenant = db.session.scalar(select(User).where(User.unit_id == unit.id, User.role == 'tenant'))
    if tenant:
        tenant.active = False
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
    tenant = db.session.scalar(select(User).where(User.unit_id == unit.id, User.role == 'tenant'))
    if tenant:
        tenant.active = True
    db.session.add(AuditLog(actor_id=current_user.id, action='unit_restored',
                           detail=f'Unit {unit.id} ({unit.name}) restored with its existing records and account.'))
    db.session.commit()
    return jsonify(message='Unit restored. Its existing tenant account can sign in again.', id=unit.id)


@api.post('/units/<int:unit_id>/tenant')
@admin_required
def assign_tenant(unit_id):
    lock_unit(unit_id)
    payload = json_payload()
    name, email, password = (payload.get(k, '') for k in ('name', 'email', 'password'))
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or not isinstance(email, str) or '@' not in email or len(email) > 254:
        raise DomainError('A tenant name and valid email are required.')
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise DomainError('Use a password of 12–128 characters.')
    if db.session.scalar(select(User.id).where(User.unit_id == unit_id)):
        raise DomainError('This unit already has an assigned tenant.', 409)
    user = User(name=name.strip(), email=email.strip().lower(), role='tenant', unit_id=unit_id)
    user.set_password(password)
    db.session.add(user)
    db.session.add(AuditLog(actor_id=current_user.id, action='tenant_assigned', detail=f'Tenant assigned to unit {unit_id}'))
    db.session.commit()
    return jsonify(message='Tenant account created and assigned.'), 201


@api.get('/transactions/export')
@login_required
def export_transactions():
    include_archived = current_user.role == 'admin' and request.args.get('include_archived') == 'true'
    ids = [u.id for u in scoped_units(include_archived=include_archived)]
    rows = db.session.scalars(select(Transaction).where(Transaction.unit_id.in_(ids)).order_by(Transaction.created_at.desc()).limit(10000)).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['transaction_id', 'unit', 'timestamp_utc', 'kind', 'amount_php', 'balance_after_php', 'description'])
    def safe(value):
        text = str(value)
        return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
    for row in rows:
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
