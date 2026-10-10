from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Event

import pytest
from sqlalchemy import func, select

from dormenergy.extensions import db
from dormenergy.models import Account, AuditLog, LoadRequest, Reading, Transaction, Unit, User, utcnow
from dormenergy.services.billing import DomainError, ingest_reading, lock_unit, resolve_load
from dormenergy.services.simulation import simulation_step
from conftest import DEVICE_TOKEN, login, meter_payload, post, transmit


def test_remove_and_restore_require_admin_and_csrf(app, client):
    assert client.post('/api/units/1/archive').status_code == 400
    assert post(client, '/api/units/1/archive', {}).status_code == 401
    login(client, 'one@test.local')
    for action in ('archive', 'restore'):
        assert post(client, f'/api/units/1/{action}', {}).status_code == 403
    assert client.get('/api/units?status=archived').status_code == 403
    client = app.test_client()
    login(client)
    assert client.post('/api/units/1/archive', json={}).status_code == 400
    assert post(client, '/api/units/999/archive', {}).status_code == 404
    assert post(client, '/api/units/1/restore', {}).status_code == 409
    with app.app_context():
        assert db.session.get(Unit, 1).archived_at is None


def test_removal_preserves_billing_and_revokes_tenant_and_device_access(app, client):
    tenant = app.test_client()
    login(tenant, 'one@test.local')
    load = post(tenant, '/api/load-requests', {'amount': '30', 'reference': 'before-removal'}).json['request']
    now = utcnow() - timedelta(seconds=10)
    assert transmit(client, meter_payload(timestamp=now-timedelta(minutes=1))).status_code == 201
    assert transmit(client, meter_payload('11', timestamp=now, event='billed-reading')).status_code == 201
    login(client)
    assert post(client, '/api/units/1/archive', {}).status_code == 200
    assert [u['id'] for u in client.get('/api/snapshot').json['units']] == [2]
    archived = client.get('/api/units?status=archived').json['units']
    assert len(archived) == 1 and archived[0]['id'] == 1
    assert archived[0]['archived_at'] and not archived[0]['online']
    assert Decimal(archived[0]['balance']) == Decimal('87.5')
    assert client.get('/api/transactions/export?unit_id=1').status_code == 404
    history = client.get('/api/transactions/export?unit_id=1&include_archived=true')
    assert history.status_code == 200 and 'consumption' in history.get_data(as_text=True)
    assert tenant.get('/api/snapshot').status_code == 401  # Existing session is revoked.
    assert login(tenant, 'one@test.local').status_code == 401
    assert transmit(client, meter_payload('12', event='after-removal')).status_code == 401
    headers = {'X-Device-ID': 'HW-001', 'Authorization': 'Bearer ' + DEVICE_TOKEN}
    assert client.get('/api/iot/instructions', headers=headers).status_code == 401
    assert post(client, '/api/units/1/tenant', {'name': 'Another', 'email': 'other@test.local',
                                            'password': 'TestsOnlyPassword!2026'}).status_code == 409
    assert post(client, f'/api/load-requests/{load["id"]}/review', {'decision': 'approved'}).status_code == 409
    assert post(client, '/api/units/1/archive', {}).status_code == 409
    with app.app_context():
        assert db.session.get(Unit, 1).power_status == 'connected'
        assert db.session.get(Account, 1).balance == Decimal('87.5')
        assert db.session.scalar(select(func.count(Reading.id))) == 2
        assert db.session.scalar(select(func.count(Transaction.id))) == 1
        request = db.session.get(LoadRequest, load['id'])
        assert request.status == 'rejected' and request.resolved_at
        assert db.session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == 'unit_archived')) == 1
        with pytest.raises(DomainError, match='removed'):
            ingest_reading(1, meter_payload('12', event='already-authenticated'), 'hardware')
        db.session.rollback()
    assert app.test_cli_runner().invoke(args=['provision-device', 'HW-001']).exit_code != 0
    assert post(client, '/api/units/1/restore', {}).status_code == 200
    assert len(client.get('/api/snapshot').json['units']) == 2
    assert client.get('/api/units?status=archived').json['units'] == []
    assert login(tenant, 'one@test.local').status_code == 302
    assert post(client, f'/api/load-requests/{load["id"]}/review', {'decision': 'approved'}).status_code == 409
    assert transmit(client, meter_payload('11', timestamp=utcnow()-timedelta(seconds=1), event='restored')).status_code == 201


def test_removing_every_unit_keeps_empty_dashboard_and_stops_simulation(app, client):
    login(client)
    for unit_id in (1, 2):
        assert post(client, f'/api/units/{unit_id}/archive', {}).status_code == 200
    response = client.get('/api/snapshot')
    assert response.status_code == 200 and response.json['units'] == []
    assert response.json['summary']['balance'] == '0'
    assert response.json['requests'] == []
    assert client.get('/api/units').json['units'] == []
    assert len(client.get('/api/units?status=archived').json['units']) == 2
    assert client.get('/api/transactions/export').status_code == 200
    with app.app_context():
        assert simulation_step() == 0
        assert db.session.scalar(select(func.count(Reading.id))) == 0
    assert post(client, '/api/units/2/restore', {}).status_code == 200
    with app.app_context():
        assert simulation_step() == 1
        assert db.session.scalar(select(Reading.device_id)) == 2


@pytest.mark.parametrize('operation', ['reading', 'credit'])
def test_removal_serializes_with_in_flight_updates(app, operation):
    with app.app_context():
        if db.engine.dialect.name != 'postgresql':
            pytest.skip('Row-lock concurrency requires PostgreSQL.')
        db.session.add(LoadRequest(user_id=2, unit_id=1, amount=Decimal('30'), reference='concurrent-removal'))
        db.session.commit()
        load_id = db.session.scalar(select(LoadRequest.id))
    removal_locked, writer_started = Event(), Event()

    def remove():
        with app.app_context():
            unit = lock_unit(1)
            removal_locked.set()
            assert writer_started.wait(timeout=5)
            resolve_load(load_id, 'rejected', db.session.get(User, 1))
            unit.archived_at = utcnow()
            db.session.get(User, 2).active = False
            db.session.commit()

    def update():
        assert removal_locked.wait(timeout=5)
        with app.app_context():
            writer_started.set()
            try:
                if operation == 'reading':
                    ingest_reading(1, meter_payload(), 'hardware')
                else:
                    resolve_load(load_id, 'approved', db.session.get(User, 1))
                db.session.commit()
            except DomainError as error:
                db.session.rollback()
                return error.status
            return 201

    with ThreadPoolExecutor(max_workers=2) as pool:
        removal = pool.submit(remove)
        writer = pool.submit(update)
        removal.result(timeout=10)
        assert writer.result(timeout=10) == 409
    with app.app_context():
        assert db.session.get(Account, 1).balance == Decimal('100')
        assert db.session.scalar(select(func.count(Reading.id))) == 0
        assert db.session.scalar(select(func.count(Transaction.id))) == 0
        assert db.session.get(LoadRequest, load_id).status == 'rejected'
