from datetime import timedelta
from decimal import Decimal
from sqlalchemy import func, select
from dormenergy.extensions import db
from dormenergy.models import Account, Device, Reading, Rate, Transaction, utcnow
from conftest import DEVICE_TOKEN, meter_payload, transmit

HEADERS = {'X-Device-ID': 'HW-001', 'Authorization': 'Bearer '+DEVICE_TOKEN}


def test_receipt_is_scoped_read_only_and_exact(client, app):
    body = meter_payload()
    assert transmit(client, body).status_code == 201
    assert transmit(client, body).json['error_code'] == 'duplicate_event'
    receipt = client.get('/api/iot/readings/receipt?event_id=event-1', headers=HEADERS)
    assert receipt.status_code == 200 and receipt.json['accepted'] is True
    assert receipt.json['reading']['energy_kwh'] == '10.000000'
    assert receipt.json['reading']['device_id'] == 'HW-001'
    assert 'balance' not in receipt.json
    assert client.get('/api/iot/readings/receipt?event_id=event-1').status_code == 401
    assert client.get('/api/iot/readings/receipt?event_id=missing', headers=HEADERS).status_code == 404
    assert client.get('/api/iot/readings/receipt?event_id=', headers=HEADERS).status_code == 422
    assert client.get('/api/iot/readings/receipt?event_id=event-1', headers={**HEADERS, 'X-Device-ID': 'SIM-002'}).status_code == 401
    with app.app_context():
        assert db.session.scalar(select(func.count(Reading.id))) == 1
        assert db.session.get(Account, 1).balance == Decimal('100')


def test_duplicate_receipt_does_not_double_charge(client, app):
    stamp = utcnow()-timedelta(seconds=100)
    assert transmit(client, meter_payload(timestamp=stamp)).status_code == 201
    body = meter_payload('11', stamp+timedelta(seconds=10), event='next')
    assert transmit(client, body).status_code == 201
    assert transmit(client, {**body, 'energy_kwh': '999'}).status_code == 409
    stored = client.get('/api/iot/readings/receipt?event_id=next', headers=HEADERS).json['reading']
    assert stored['energy_kwh'] == '11.000000'
    with app.app_context():
        assert db.session.get(Account, 1).balance == Decimal('87.5')
        assert db.session.scalar(select(func.count(Transaction.id))) == 1


def test_fixture_production_guard_and_tariff_error(client, app):
    app.config['TESTING'] = False
    assert transmit(client, meter_payload(gateway_fixture=True)).json['error_code'] == 'development_fixture_forbidden'
    app.config['TESTING'] = True
    with app.app_context():
        db.session.query(Rate).delete(); db.session.commit()
    response = transmit(client, meter_payload())
    assert response.status_code == 422 and response.json['error_code'] == 'tariff_unavailable'
    with app.app_context():
        assert db.session.scalar(select(func.count(Reading.id))) == 0


def test_archived_device_cannot_read_receipts(client, app):
    assert transmit(client, meter_payload()).status_code == 201
    with app.app_context():
        device = db.session.scalar(select(Device).where(Device.identifier == 'HW-001'))
        device.unit.archived_at = utcnow(); db.session.commit()
    assert client.get('/api/iot/readings/receipt?event_id=event-1', headers=HEADERS).status_code == 401


def test_auth_precedes_untrusted_json_and_deep_frames_fail_cleanly(client):
    deeply_nested = '['*4096+']'*4096
    assert client.post('/api/iot/readings', data=deeply_nested, content_type='application/json').status_code == 401
    response = client.post('/api/iot/readings', data=deeply_nested, content_type='application/json', headers=HEADERS)
    assert response.status_code == 422 and response.json['error_code'] == 'validation_failed'
