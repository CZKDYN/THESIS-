"""Normalize genuine meter counters without creating energy increments or timestamps."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from .config import Config, Device


class InvalidMeasurement(ValueError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def utc(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise InvalidMeasurement('timestamp_invalid') from None


@dataclass(frozen=True)
class Measurement:
    device: Device
    payload: dict
    canonical: str
    digest: str
    timestamp: float
    origin: str


def validate(raw: bytes, device: Device, config: Config, now: float | None) -> Measurement:
    if len(raw) > 8192:
        raise InvalidMeasurement('payload_too_large')
    try:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise InvalidMeasurement('duplicate_json_field')
                result[key] = value
            return result
        data = json.loads(raw, parse_float=Decimal, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidMeasurement('invalid_json') from None
    if not isinstance(data, dict):
        raise InvalidMeasurement('invalid_json')
    allowed = {'device_id', 'unit_id', 'event_id', 'meter_epoch', 'timestamp', 'voltage', 'current',
               'power_w', 'energy_kwh', 'connection_status', 'reset_confirmed', 'measurement_origin', 'clock_synchronized'}
    if set(data) - allowed:
        raise InvalidMeasurement('unsupported_field')
    if data.get('device_id') != device.device_id or type(data.get('unit_id')) is not int or data['unit_id'] != device.unit_id:
        raise InvalidMeasurement('device_mapping_invalid')
    origin = data.get('measurement_origin')
    if origin not in ('hardware', 'development_fixture') or (config.mode == 'real' and origin != 'hardware'):
        raise InvalidMeasurement('fixture_forbidden')
    if data.get('clock_synchronized') is not True:
        raise InvalidMeasurement('device_clock_unsynchronized')
    for field in ('event_id', 'meter_epoch'):
        if not isinstance(data.get(field), str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,64}', data[field]):
            raise InvalidMeasurement('identifier_invalid')
    stamp = utc(data.get('timestamp')).timestamp()
    if now is not None and stamp > now + 120:
        raise InvalidMeasurement('timestamp_future')
    payload = {key: data[key] for key in ('device_id', 'unit_id', 'event_id', 'meter_epoch')}
    payload['timestamp'] = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    for field, maximum, precision in (('voltage', '300', '.001'), ('current', '100', '.0001'),
                                       ('power_w', '25000', '.001'), ('energy_kwh', '1000000000', '.000001')):
        try:
            if isinstance(data.get(field), bool) or data.get(field) is None:
                raise ValueError()
            value = Decimal(str(data[field]))
            if not value.is_finite() or not 0 <= value <= Decimal(maximum) or value != value.quantize(Decimal(precision)):
                raise ValueError()
            payload[field] = str(value.quantize(Decimal(precision)))
        except (ValueError, InvalidOperation, KeyError):
            raise InvalidMeasurement('measurement_range_or_precision') from None
    if data.get('connection_status') not in ('connected', 'disconnected', 'protective_trip', 'unknown'):
        raise InvalidMeasurement('connection_status_invalid')
    payload['connection_status'] = data['connection_status']
    if 'reset_confirmed' in data:
        if type(data['reset_confirmed']) is not bool:
            raise InvalidMeasurement('reset_flag_invalid')
        payload['reset_confirmed'] = data['reset_confirmed']
    if origin == 'development_fixture':
        payload['gateway_fixture'] = True
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return Measurement(device, payload, canonical, hashlib.sha256(canonical.encode()).hexdigest(), stamp, origin)
