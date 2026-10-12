from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pytest
from dormenergy_gateway.config import Config, Device
from dormenergy_gateway.queue import Journal
from dormenergy_gateway.validation import validate


@pytest.fixture
def config(tmp_path):
    password = tmp_path/'mqtt.secret'
    password.write_text('isolated-test-broker-only'); password.chmod(0o600)
    devices = []
    for index in range(1, 4):
        token = tmp_path/f'token-{index}'
        token.write_text('isolated-test-device-only'); token.chmod(0o600)
        topic = f'dormenergy/floor{index}/room{index}'
        devices.append(Device(f'room{index}', f'DEV-00{index}', index, topic+'/telemetry',
                              topic+'/ack', topic+'/status', 'epoch-1', token))
    return Config('isolated_test', tmp_path/'gateway.sqlite3', tuple(devices), '127.0.0.1', 1883,
                  'test-gateway', 'gateway', password, None, True, 'http://127.0.0.1:59999', None,
                  1, 3, 1000, 10000000, 0, 7, 90, 86400)


@pytest.fixture
def journal(config):
    instance = Journal(config)
    yield instance
    instance.close()


def frame(config, index=0, event='event-1', seconds=-60, energy='10', **fields):
    device = config.devices[index]
    body = dict(device_id=device.device_id, unit_id=device.unit_id, event_id=event, meter_epoch='epoch-1',
                timestamp=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(),
                voltage='230', current='1', power_w='230', energy_kwh=energy, connection_status='connected',
                measurement_origin='development_fixture', clock_synchronized=True)
    body.update(fields)
    return json.dumps(body).encode()


def item(config, **fields):
    index = fields.get('index', 0)
    return validate(frame(config, **fields), config.devices[index], config, datetime.now(timezone.utc).timestamp())
