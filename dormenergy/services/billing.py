"""The single authoritative path for meter ingestion and monetary updates."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from flask import current_app
from sqlalchemy import select
from ..extensions import db
from ..models import Account, AuditLog, Device, DeviceEvent, LoadRequest, Notification, Rate, Reading, Transaction, Unit, User, utcnow

MONEY_PRECISION = Decimal('0.000001')


class DomainError(Exception):
    def __init__(self, message, status=422, code='validation_failed'):
        self.message, self.status, self.code = message, status, code


def lock_unit(unit_id, allow_archived=False):
    """Serialize unit removal with tenant, credit, and meter updates."""
    unit = db.session.scalar(select(Unit).where(Unit.id == unit_id).with_for_update()
                             .execution_options(populate_existing=True))
    if not unit:
        raise DomainError('Unit not found.', 404)
    if not current_app.config['SIMULATION_MODE'] and unit.is_demo:
        raise DomainError('This is retained demonstration history. Register a hardware unit for real use.', 409)
    if unit.archived_at and not allow_archived:
        raise DomainError('This unit has been removed. Restore it before making changes.', 409)
    return unit


def decimal_value(value, field, maximum=Decimal('1000000000')):
    if isinstance(value, bool) or value is None:
        raise DomainError(f'{field} must be a valid nonnegative number.')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise DomainError(f'{field} must be a valid nonnegative number.') from None
    if not result.is_finite() or result < 0 or result > maximum:
        raise DomainError(f'{field} is outside the allowed range.')
    return result


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_timestamp(value):
    try:
        timestamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError()
        timestamp = timestamp.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise DomainError('timestamp must be an ISO 8601 date with a timezone.') from None
    if timestamp > utcnow() + timedelta(minutes=2):
        raise DomainError('timestamp cannot be in the future.')
    return timestamp


def charge_for_interval(delta, start, end):
    """Prorate interval energy across rate changes; no finer usage is inferred."""
    from .utility_rates import billing_rates, rate_covers
    rates = billing_rates()
    first = db.session.scalar(rates.where(Rate.effective_at <= start).order_by(Rate.effective_at.desc()).limit(1))
    message = ('No owner-confirmed utility rate covers this reading interval. Confirm the applicable utility bill and validity period.'
               if current_app.config['UTILITY_RATE_LOCKED'] else 'No electricity rate covers this reading interval.')
    if not rate_covers(first, start):
        raise DomainError(message, code='tariff_unavailable')
    changes = db.session.scalars(rates.where(Rate.effective_at > start, Rate.effective_at <= end).order_by(Rate.effective_at)).all()
    if end == start:
        return Decimal('0'), first
    duration = Decimal(str((end - start).total_seconds()))
    cost, cursor, rate = Decimal('0'), start, first
    for change in changes:
        if not rate_covers(rate, cursor, aware(change.effective_at)):
            raise DomainError(message, code='tariff_unavailable')
        seconds = Decimal(str((aware(change.effective_at) - cursor).total_seconds()))
        cost += delta * (seconds / duration) * rate.php_per_kwh
        cursor, rate = aware(change.effective_at), change
    if not rate_covers(rate, cursor, end):
        raise DomainError(message, code='tariff_unavailable')
    cost += delta * Decimal(str((end - cursor).total_seconds())) / duration * rate.php_per_kwh
    return cost.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP), rate


def ingest_reading(device_id, payload, source, authenticated_token_hash=None):
    if not isinstance(payload, dict):
        raise DomainError('A JSON object is required.')
    device = db.session.get(Device, device_id)
    if not device or device.source != source:
        raise DomainError('Device source does not match this transmission.', 403)
    unit = lock_unit(device.unit_id)
    device = db.session.scalar(select(Device).where(Device.id == device_id).with_for_update()
                               .execution_options(populate_existing=True))
    if authenticated_token_hash is not None and device.token_hash != authenticated_token_hash:
        raise DomainError('Device credential changed before this reading could be stored.', 401, 'device_authentication_failed')
    if payload.get('device_id') != device.identifier or type(payload.get('unit_id')) is not int or payload.get('unit_id') != device.unit_id:
        raise DomainError('Device and unit must match the registered device.', 403, 'device_mapping_invalid')
    event_id, epoch = payload.get('event_id'), payload.get('meter_epoch')
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 64 or not isinstance(epoch, str) or not 1 <= len(epoch) <= 64:
        raise DomainError('event_id and meter_epoch must be strings of 1–64 characters.')
    if db.session.scalar(select(Reading.id).where(Reading.device_id == device.id, Reading.event_id == event_id)):
        raise DomainError('This reading was already accepted; no additional charge was made.', 409, 'duplicate_event')
    timestamp = parse_timestamp(payload.get('timestamp'))
    if device.last_timestamp and timestamp <= aware(device.last_timestamp):
        raise DomainError('Readings must be synchronized in timestamp order.', 409, 'timestamp_order_conflict')
    def measured(field, maximum, precision):
        value = decimal_value(payload.get(field), field, Decimal(maximum))
        normalized = value.quantize(Decimal(precision))
        if value != normalized:
            raise DomainError(f'{field} has more precision than the stored measurement supports.')
        return normalized
    energy = measured('energy_kwh', '1000000000', '.000001')
    voltage = measured('voltage', '300', '.001')
    current = measured('current', '100', '.0001')
    power = measured('power_w', '25000', '.001')
    status = payload.get('connection_status')
    if status not in ('connected', 'disconnected', 'protective_trip', 'unknown'):
        raise DomainError('Invalid connection_status.')
    reset = device.meter_epoch is not None and device.meter_epoch != epoch
    if reset and not payload.get('reset_confirmed') is True:
        raise DomainError('A changed meter epoch requires reset_confirmed=true; consumption is re-baselined.', code='reset_confirmation_required')
    if not reset and device.last_energy is not None and energy < device.last_energy:
        raise DomainError('Cumulative energy decreased. Send an explicitly confirmed new meter epoch.', code='counter_decreased')
    delta = Decimal('0') if reset or device.last_energy is None else energy - device.last_energy
    start = timestamp if reset or device.last_timestamp is None else aware(device.last_timestamp)
    cost, rate = charge_for_interval(delta, start, timestamp)
    account = db.session.scalar(select(Account).where(Account.unit_id == device.unit_id).with_for_update())
    previous_status = unit.power_status
    reading = Reading(device_id=device.id, event_id=event_id, timestamp=timestamp, voltage=voltage,
                      current=current, power=power, energy=energy, delta_kwh=delta, rate_id=rate.id,
                      source=source, meter_epoch=epoch, connection_status=status)
    db.session.add(reading)
    db.session.flush()
    previous_balance = account.balance
    account.balance = (account.balance - cost).quantize(MONEY_PRECISION)
    if delta > 0:
        db.session.add(Transaction(unit_id=unit.id, kind='consumption', amount=-cost,
                                   balance_after=account.balance, reading_id=reading.id,
                                   description=f'{delta:.6f} kWh · interval energy charge', created_at=timestamp))
    device.last_energy, device.last_timestamp, device.meter_epoch = energy, timestamp, epoch
    # Arrival and measurement time are distinct: buffered packets do not look live.
    device.last_seen = device.gateway_last_seen = utcnow()
    unit.power_status = status
    if reset:
        db.session.add(DeviceEvent(device_id=device.id, kind='meter_reset', state='confirmed', message='New meter epoch accepted; baseline charged at zero.'))
    if previous_balance > account.low_threshold >= account.balance:
        db.session.add(Notification(unit_id=unit.id, kind='low_balance', message='Your prepaid credit is low. Submit a load request to keep your account funded.'))
        if source=='hardware' and not unit.is_demo and current_app.config['EMAIL_BACKEND']!='disabled':
            from .emailing import queue_balance_alert
            tenant=db.session.scalar(select(User).where(User.unit_id==unit.id,User.role=='tenant',User.tenancy_ended_at.is_(None)))
            if tenant:
                queue_balance_alert(tenant,unit,account,reading)
    if previous_balance > 0 >= account.balance:
        db.session.add(DeviceEvent(device_id=device.id, kind='credit_exhausted', state='requires_review', message='Disconnection procedure required. No physical actuation command issued.'))
        db.session.add(Notification(unit_id=unit.id, kind='credit_exhausted', message='Credit exhausted. The approved disconnection procedure requires operator review.'))
        if source=='hardware' and not unit.is_demo and current_app.config['EMAIL_CREDIT_EXHAUSTION']:
            from .emailing import queue_balance_alert, queue_email
            tenant=db.session.scalar(select(User).where(User.unit_id==unit.id,User.role=='tenant',User.tenancy_ended_at.is_(None)))
            if tenant:
                queue_balance_alert(tenant,unit,account,reading,exhausted=True)
            if utcnow()-aware(reading.timestamp)<=timedelta(minutes=5) and current_app.config['EMAIL_BACKEND']!='disabled':
                for admin in db.session.scalars(select(User).where(User.role=='admin',User.active.is_(True),User.email_balance_alerts.is_(True))).all():
                    queue_email(admin,'credit_exhausted','Unit prepaid credit exhausted','Review the unit and its prepaid balance. No physical disconnection command was issued.',
                        details=[('Unit',unit.name),('Room',unit.room),('Balance',f'PHP {account.balance:,.2f}')],dedupe_key=f'exhausted-admin:{reading.id}:{admin.id}')
    if status != previous_status:
        db.session.add(DeviceEvent(device_id=device.id, kind='power_status', state=status, message='Device-reported supply status changed.'))
        db.session.add(Notification(unit_id=unit.id, kind='power_status', message=f'Electricity status reported as {status.replace("_", " ")}.'))
    return reading


def resolve_load(request_id, decision, actor):
    if decision not in ('approved', 'rejected'):
        raise DomainError('Choose approved or rejected.')
    load = db.session.get(LoadRequest, request_id)
    if load is None:
        raise DomainError('Load request not found.', 404)
    lock_unit(load.unit_id)
    load = db.session.scalar(select(LoadRequest).where(LoadRequest.id == request_id).with_for_update()
                             .execution_options(populate_existing=True))
    if load.status != 'pending':
        raise DomainError('This request has already been reviewed. No credit was added twice.', 409)
    account = db.session.scalar(select(Account).where(Account.unit_id == load.unit_id).with_for_update())
    if decision == 'approved':
        account.balance += load.amount
        db.session.add(Transaction(unit_id=load.unit_id, kind='credit', amount=load.amount,
                                   balance_after=account.balance, load_request_id=load.id,
                                   description=f'Prepaid load · {load.reference}'))
    load.status, load.resolved_by, load.resolved_at = decision, actor.id, utcnow()
    db.session.add(Notification(unit_id=load.unit_id, kind=f'load_{decision}', message=f'Your ₱{load.amount:,.2f} load request was {decision}.'))
    db.session.add(AuditLog(actor_id=actor.id, action=f'load_{decision}', detail=f'Request {load.id}, unit {load.unit_id}, PHP {load.amount}'))
    if not load.unit.is_demo and current_app.config['EMAIL_BACKEND']!='disabled':
        from .emailing import queue_load_update
        queue_load_update(load,account)
    return load
