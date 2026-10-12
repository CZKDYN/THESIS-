from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import json
import threading
import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash
from dormenergy.extensions import db
from dormenergy.models import Account, Device, LoadRequest, Reading, Unit, User, utcnow
from dormenergy.services.billing import lock_unit
from conftest import login, meter_payload, post, transmit


def test_revoked_device_token_cannot_commit_after_waiting_for_unit_lock(app, monkeypatch):
    import dormenergy.hardware as hardware
    authenticated=threading.Event();original=hardware.authorized_device
    def notified():
        device=original();authenticated.set();return device
    monkeypatch.setattr(hardware,'authorized_device',notified)
    with app.app_context():
        if db.engine.dialect.name!='postgresql':pytest.skip('Actual lock race requires PostgreSQL.')
        lock_unit(1)
        device=db.session.get(Device,1);device.token_hash=generate_password_hash('RotatedTestOnlyToken')
        def send():
            with app.test_client() as client:return transmit(client,meter_payload()).status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(send)
            assert authenticated.wait(5)
            db.session.commit()
            assert pending.result(10)==401
        assert db.session.scalar(select(func.count(Reading.id)))==0
        assert db.session.get(Account,1).balance==Decimal('100')


def test_ended_tenant_cannot_create_credit_request_after_unit_lock_wait(app, monkeypatch):
    import dormenergy.api as api
    waiting=threading.Event();original=api.lock_unit
    def notified(*args,**kwargs):waiting.set();return original(*args,**kwargs)
    with app.app_context():
        if db.engine.dialect.name!='postgresql':pytest.skip('Actual lock race requires PostgreSQL.')
        browser=app.test_client();assert login(browser,'one@test.local').status_code==302
        lock_unit(1)
        tenant=db.session.scalar(select(User).where(User.email=='one@test.local'))
        tenant.active=False;tenant.tenancy_ended_at=utcnow();tenant.session_version+=1
        monkeypatch.setattr(api,'lock_unit',notified)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(lambda:post(browser,'/api/load-requests',{'amount':'50','reference':'tenancy-race'}).status_code)
            assert waiting.wait(5);db.session.commit()
            assert pending.result(10)==403
        assert db.session.scalar(select(func.count(LoadRequest.id)))==0


@pytest.mark.parametrize('fields',[{'unit_id':True},{'energy_kwh':'10.0000001'},{'current':'1.00001'},{'voltage':'230.0001'},{'power_w':'230.0001'},{'timestamp':'0001-01-01T00:00:00+14:00'}])
def test_measurement_precision_and_identity_fail_atomically(app,client,fields):
    body=meter_payload();body.update(fields)
    assert transmit(client,body).status_code in (403,422)
    with app.app_context():
        assert db.session.scalar(select(func.count(Reading.id)))==0
        assert db.session.get(Account,1).balance==Decimal('100')


def test_future_measurement_is_not_current_online_data(client):
    assert transmit(client,meter_payload(timestamp=utcnow()+timedelta(seconds=90))).status_code==201
    login(client)
    unit=client.get('/api/snapshot').json['units'][0]
    assert not unit['online'] and unit['power'] is None


def test_browser_api_deep_json_and_sql_injection_are_safe(client, app):
    login(client,'one@test.local')
    token=__import__('conftest').csrf_token(client)
    response=client.post('/api/load-requests',data='['*4096+']'*4096,content_type='application/json',headers={'X-CSRFToken':token})
    assert response.status_code==422
    assert client.get('/api/snapshot?unit_id=1%20OR%201=1').status_code==404
    assert client.get('/api/snapshot?unit_id=2&include_archived=true&include_demo=true').status_code==403
    assert post(client,'/api/units/2/device-token',{'action':'revoke'}).status_code==403
    assert client.get('/api/email-status').status_code==403
    assert client.get('/api/units?status=demo').status_code==403


def test_runtime_engine_hides_parameters_and_ping_is_enabled(app):
    with app.app_context():
        assert db.engine.hide_parameters is True
        assert db.engine.pool._pre_ping is True


def test_database_disconnect_returns_safe_503_then_recovers(app, client, monkeypatch, caplog):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    def unavailable(*args):
        raise OperationalError('SELECT confidential_parameter', {'private': 'test-secret-sentinel'}, OSError('test database outage'))
    with app.app_context():
        engine = db.engine
        event.listen(engine, 'before_cursor_execute', unavailable)
    try:
        response = client.get('/health')
        assert response.status_code == 503 and response.json['error_code'] == 'database_unavailable'
        assert b'test-secret-sentinel' not in response.data and 'test-secret-sentinel' not in caplog.text
    finally:
        event.remove(engine, 'before_cursor_execute', unavailable)
    assert client.get('/health').status_code == 200


@pytest.mark.parametrize('path', ['/api/rates', '/api/units', '/api/load-requests/1/review',
    '/api/units/2/archive', '/api/units/2/restore', '/api/units/2/edit', '/api/units/2/tenant',
    '/api/units/2/tenant/invitation', '/api/units/2/tenant/password', '/api/units/2/tenant/end',
    '/api/units/2/device-token', '/api/units/2/forecast/train'])
def test_tenant_cannot_perform_administrator_writes(client, path):
    login(client, 'one@test.local')
    assert post(client, path, {}).status_code == 403


def test_tenant_read_endpoints_reject_foreign_units(client):
    login(client, 'one@test.local')
    for path in ('/api/snapshot', '/api/forecast', '/api/transactions/export'):
        assert client.get(path+'?unit_id=2&include_archived=true&include_demo=true').status_code == 403
