"""Explicitly simulated meters use the same billing path as authenticated hardware."""
import math
import random
from decimal import Decimal
from sqlalchemy import select
from ..extensions import db
from ..models import Account, Device, DeviceEvent, utcnow
from .billing import aware, ingest_reading


def simulation_step():
    now = utcnow()
    count = 0
    devices = db.session.scalars(select(Device).where(Device.source == 'simulation').order_by(Device.id)).all()
    for device in devices:
        # The ingest service serializes updates with a database row lock.
        device = db.session.scalar(select(Device).where(Device.id == device.id).with_for_update())
        if device.last_timestamp and aware(device.last_timestamp) >= now:
            continue
        seconds = (now - aware(device.last_timestamp)).total_seconds() if device.last_timestamp else 0
        # Do not invent consumption for time the simulator was stopped.
        if seconds > 120:
            db.session.add(DeviceEvent(device_id=device.id, kind='simulation_gap', state='missing_data', message='Simulator paused; gap energy was not fabricated.'))
            seconds = 0
        account = db.session.scalar(select(Account).where(Account.unit_id == device.unit_id).with_for_update())
        hour = (now.hour + 8) % 24
        power = Decimal(str(round((150 + 90 * math.sin(hour * math.pi / 12) ** 2 + device.id * 38) * random.uniform(.88, 1.15), 2)))
        status = 'protective_trip' if device.unit.power_status == 'protective_trip' else ('connected' if account.balance > 0 else 'disconnected')
        if status != 'connected':
            power = Decimal('0')
        energy = (device.last_energy or Decimal('0')) + power * Decimal(str(seconds)) / Decimal('3600000')
        payload = dict(device_id=device.identifier, unit_id=device.unit_id,
                       event_id=f'sim-{now.isoformat()}', meter_epoch=device.meter_epoch or 'demo-1',
                       timestamp=now.isoformat(), voltage='230.4', current=str(power / Decimal('230.4')),
                       power_w=str(power), energy_kwh=str(energy), connection_status=status)
        ingest_reading(device.id, payload, 'simulation')
        db.session.commit()
        count += 1
    return count
