from flask import Blueprint, jsonify, request, current_app
from sqlalchemy import select
from werkzeug.security import check_password_hash
from .extensions import db
from .models import Device
from .services.billing import DomainError, ingest_reading

hardware = Blueprint('hardware', __name__)


def authorized_device():
    identifier = request.headers.get('X-Device-ID', '')
    authorization = request.headers.get('Authorization', '')
    if not authorization.startswith('Bearer ') or len(authorization) > 1024:
        return None
    device = db.session.scalar(select(Device).where(Device.identifier == identifier))
    if not device or device.source != 'hardware' or not device.token_hash:
        return None
    return device if check_password_hash(device.token_hash, authorization[7:]) else None


@hardware.post('/readings')
def readings():
    device = authorized_device()
    if not device:
        return jsonify(error='Invalid device authentication.'), 401
    try:
        reading = ingest_reading(device.id, request.get_json(silent=True), 'hardware')
        db.session.commit()
        return jsonify(id=reading.id, accepted=True, delta_kwh=str(reading.delta_kwh)), 201
    except DomainError as error:
        db.session.rollback()
        current_app.logger.warning('meter_rejected device=%s status=%s reason=%s', device.id, error.status, error.message)
        return jsonify(error=error.message), error.status


@hardware.get('/instructions')
def instructions():
    device = authorized_device()
    if not device:
        return jsonify(error='Invalid device authentication.'), 401
    return jsonify(device_id=device.identifier, commands=[], control_enabled=False,
                   message='Monitoring only. Physical actuation requires a separately reviewed safety design.')
