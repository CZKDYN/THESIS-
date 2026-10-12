from flask import Blueprint, jsonify, request, current_app
from sqlalchemy import select
from werkzeug.security import check_password_hash
from .extensions import db
from .models import Device, Reading, Unit
from .services.billing import DomainError, aware, ingest_reading

hardware = Blueprint('hardware', __name__)


def authorized_device():
    identifier = request.headers.get('X-Device-ID', '')
    authorization = request.headers.get('Authorization', '')
    if not authorization.startswith('Bearer ') or len(authorization) > 1024:
        return None
    device = db.session.scalar(select(Device).join(Unit).where(
        Device.identifier == identifier, Unit.archived_at.is_(None)))
    if not device or device.source != 'hardware' or not device.token_hash:
        return None
    return device if check_password_hash(device.token_hash, authorization[7:]) else None


@hardware.post('/readings')
def readings():
    device = authorized_device()
    if not device:
        return jsonify(error='Invalid device authentication.', error_code='device_authentication_failed'), 401
    try:
        payload = request.get_json(silent=True)
    except RecursionError:
        return jsonify(error='Invalid measurement JSON.', error_code='validation_failed'), 422
    if isinstance(payload, dict) and payload.get('gateway_fixture') is True and not current_app.testing:
        return jsonify(error='Development gateway fixtures are forbidden on this deployment.',
                       error_code='development_fixture_forbidden'), 403
    try:
        reading = ingest_reading(device.id, payload, 'hardware')
        db.session.commit()
        return jsonify(id=reading.id, accepted=True, delta_kwh=str(reading.delta_kwh)), 201
    except DomainError as error:
        db.session.rollback()
        current_app.logger.warning('meter_rejected device=%s status=%s reason=%s', device.id, error.status, error.message)
        return jsonify(error=error.message, error_code=error.code), error.status


@hardware.get('/readings/receipt')
def receipt():
    """Scoped, read-only reconciliation of an uncertain event; never returns balances."""
    device = authorized_device()
    if not device:
        return jsonify(error='Invalid device authentication.', error_code='device_authentication_failed'), 401
    event_id = request.args.get('event_id', '')
    if not 1 <= len(event_id) <= 64:
        return jsonify(accepted=False, error_code='event_id_invalid'), 422
    row = db.session.scalar(select(Reading).where(Reading.device_id == device.id, Reading.event_id == event_id))
    if row is None:
        return jsonify(accepted=False, error_code='event_not_found'), 404
    return jsonify(accepted=True, id=row.id, reading=dict(device_id=device.identifier, unit_id=device.unit_id,
        event_id=row.event_id, meter_epoch=row.meter_epoch, timestamp=aware(row.timestamp).isoformat(),
        voltage=str(row.voltage), current=str(row.current), power_w=str(row.power), energy_kwh=str(row.energy),
        connection_status=row.connection_status))


@hardware.get('/instructions')
def instructions():
    device = authorized_device()
    if not device:
        return jsonify(error='Invalid device authentication.'), 401
    return jsonify(device_id=device.identifier, commands=[], control_enabled=False,
                   message='Monitoring only. Physical actuation requires a separately reviewed safety design.')
