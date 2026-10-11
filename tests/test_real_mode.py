"""Real-mode workflows against test fixtures, never physical-meter results."""
import re
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from dormenergy.extensions import db
from dormenergy.models import Account, AuditLog, Device, LoadRequest, Rate, Reading, Transaction, Unit, User, utcnow
from conftest import DEVICE_TOKEN, PASSWORD, login, meter_payload, post, transmit


def real_mode(app):
    app.config['SIMULATION_MODE'] = False
    with app.app_context():
        db.session.get(Unit, 1).is_demo = False
        db.session.commit()


def form_post(client, path, **data):
    token = re.search(r'name="csrf-token" content="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)
    return client.post(path, data={'csrf_token':token, **data})


def test_real_workspace_excludes_demo_and_does_not_invent_measurements(app, client):
    real_mode(app)
    login(client)
    data = client.get('/api/snapshot').json
    assert not data['simulation'] and [u['id'] for u in data['units']] == [1]
    assert data['summary']['power_w'] is None and data['summary']['today_kwh'] is None
    assert data['units'][0]['energy'] is None and data['units'][0]['today_kwh'] is None
    assert all(value is None for value in data['charts']['hourly']['values'])
    assert client.get('/api/snapshot?unit_id=2').status_code == 404
    assert client.get('/api/forecast?unit_id=2').status_code == 404
    assert client.get('/api/units?status=demo').json['units'][0]['source'] == 'simulation'
    assert post(client, '/api/units/2/restore', {}).status_code == 409
    assert client.get('/api/transactions/export?unit_id=2&include_archived=true&include_demo=true').status_code == 200
    tenant = app.test_client()
    assert login(tenant, 'two@test.local').status_code == 401
    assert client.get('/api/forecast?unit_id=1').json['points'] == []
    created=post(client,'/api/units',{'name':'Unit 02','room':'Actual room','device_id':'ACTUAL-002'})
    assert created.status_code==201
    with app.app_context():
        assert db.session.get(Unit,2).is_demo and '(demo 2)' in db.session.get(Unit,2).name
        assert not db.session.get(Unit,created.json['id']).is_demo


def test_first_administrator_setup_requires_token_and_closes_after_creation(app, client):
    app.config['SIMULATION_MODE'] = False
    with app.app_context():
        db.session.delete(db.session.get(User, 1))
        db.session.commit()
    assert client.get('/setup').status_code == 503
    app.config['ADMIN_SETUP_TOKEN'] = 'TestOnlyBootstrapTokenNotForHosting'
    payload=dict(setup_token='wrong',name='Owner',email='owner@example.com',new_password=PASSWORD,confirm_password=PASSWORD)
    assert client.post('/setup',data=payload).status_code == 400
    assert form_post(client, '/setup', **payload).status_code == 403
    payload['setup_token'] = app.config['ADMIN_SETUP_TOKEN']
    assert form_post(client, '/setup', **payload).status_code == 302
    assert client.get('/api/snapshot').status_code == 200
    assert client.get('/setup').status_code == 302
    other = app.test_client()
    token = re.search(r'name="csrf-token" content="([^"]+)"',other.get('/login').get_data(as_text=True)).group(1)
    assert other.post('/setup',data={**payload,'email':'second@example.com','csrf_token':token}).status_code == 302
    with app.app_context():
        assert db.session.scalar(select(func.count(User.id)).where(User.role=='admin')) == 1


def test_profile_and_password_change_revoke_other_sessions(app, client):
    real_mode(app)
    login(client)
    other = app.test_client()
    login(other)
    assert form_post(client, '/account', name='Dorm Owner',email='owner@example.com',current_password='wrong').status_code == 403
    assert form_post(client, '/account', name='Dorm Owner',email='owner@example.com',current_password=PASSWORD).status_code == 302
    new = 'PrivateOwnerPassword!2026'
    assert form_post(client, '/account/password',current_password=PASSWORD,new_password=new,confirm_password=new).status_code == 302
    assert client.get('/api/snapshot').status_code == 200
    assert other.get('/api/snapshot').status_code == 401
    fresh = app.test_client()
    assert login(fresh, 'owner@example.com',PASSWORD).status_code == 401
    assert login(fresh, 'owner@example.com',new).status_code == 302


def test_admin_reset_and_first_login_password_requirement(app, client):
    real_mode(app)
    login(client)
    tenant = app.test_client()
    login(tenant, 'one@test.local')
    temporary = 'TenantTemporaryPassword!2026'
    assert post(client, '/api/units/1/tenant/password', {'password':temporary}).status_code == 200
    assert tenant.get('/api/snapshot').status_code == 401
    assert login(tenant, 'one@test.local',temporary).location.endswith('/account/password')
    assert tenant.get('/api/snapshot').json['password_change_required']
    own = 'TenantPrivatePassword!2026'
    assert form_post(tenant,'/account/password',current_password=temporary,new_password=own,confirm_password=own).status_code == 302
    assert tenant.get('/api/snapshot').json['units'][0]['id'] == 1
    assert post(tenant,'/api/units/1/tenant/password',{'password':temporary}).status_code == 403


def test_tenant_turnover_retains_records_and_does_not_restore_former_account(app, client):
    real_mode(app)
    login(client)
    former = app.test_client()
    login(former, 'one@test.local')
    pending=post(former,'/api/load-requests',{'amount':'50','reference':'turnover-pending'}).json['request']['id']
    assert post(client,'/api/units/1/tenant/end',{}).status_code == 200
    assert former.get('/api/snapshot').status_code == 401
    assert post(client,'/api/units/1/tenant',{'name':'Next Tenant','email':'next@example.com','password':PASSWORD}).status_code == 201
    with app.app_context():
        old = db.session.scalar(select(User).where(User.email=='one@test.local'))
        new = db.session.scalar(select(User).where(User.email=='next@example.com'))
        assert old.unit_id == new.unit_id == 1 and not old.active and old.tenancy_ended_at
        assert db.session.get(Account,1).balance == 100
        assert db.session.get(LoadRequest,pending).status == 'rejected'
        old_id, new_id = old.id, new.id
    assert post(client,'/api/units/1/archive',{}).status_code == 200
    assert post(client,'/api/units/1/restore',{}).status_code == 200
    with app.app_context():
        assert not db.session.get(User,old_id).active and db.session.get(User,new_id).active
    assert client.get('/api/snapshot').json['units'][0]['tenant'] == 'Next Tenant'


def test_browser_device_token_rotation_real_ingestion_and_revocation(app, client):
    real_mode(app)
    login(client)
    assert client.post('/api/units/1/device-token',json={'current_password':PASSWORD}).status_code == 400
    assert post(client,'/api/units/1/device-token',{'current_password':'wrong'}).status_code == 403
    assert post(client,'/api/units/1/device-token',{'current_password':None}).status_code == 403
    result=post(client,'/api/units/1/device-token',{'current_password':PASSWORD})
    assert result.status_code == 200 and result.headers['Cache-Control'] == 'no-store'
    token=result.json['token']
    first=utcnow()-timedelta(seconds=60)
    assert transmit(client,meter_payload(timestamp=first),token=DEVICE_TOKEN).status_code == 401
    assert transmit(client,meter_payload(timestamp=first),token=token).status_code == 201
    assert transmit(client,meter_payload(energy='10.2',timestamp=first+timedelta(seconds=50),event='increment'),token=token).status_code == 201
    with app.app_context():
        assert db.session.get(Account,1).balance == Decimal('97.500000')
        assert db.session.scalar(select(func.count(Reading.id))) == 2
        assert db.session.scalar(select(func.count(Transaction.id))) == 1
        assert token not in db.session.get(Device,1).token_hash
        assert all(token not in row.detail for row in db.session.scalars(select(AuditLog)).all())
    assert client.get('/api/snapshot').json['units'][0]['online']
    assert post(client,'/api/units/1/device-token',{'action':'revoke','current_password':PASSWORD}).status_code == 200
    assert transmit(client,meter_payload(event='revoked'),token=token).status_code == 401
    assert not client.get('/api/snapshot').json['units'][0]['token_provisioned']


def test_unit_editing_preserves_balance_and_rejects_invalid_threshold(app, client):
    real_mode(app)
    login(client)
    values=dict(name='Room A',room='First floor',threshold='75.50')
    assert post(client,'/api/units/1/edit',{**values,'threshold':'0.001'}).status_code == 422
    assert post(client,'/api/units/1/edit',values).status_code == 200
    data=client.get('/api/snapshot').json['units'][0]
    assert data['name']=='Room A' and Decimal(data['threshold'])==Decimal('75.50') and Decimal(data['balance'])==100
    tenant=app.test_client()
    login(tenant,'one@test.local')
    assert post(tenant,'/api/units/1/edit',values).status_code == 403


def test_real_billing_requires_an_actual_rate_not_a_seeded_tariff(app, client):
    real_mode(app)
    with app.app_context():
        db.session.get(Rate,1).is_demo=True
        db.session.commit()
    login(client)
    assert client.get('/api/snapshot').json['rate'] is None
    assert client.get('/api/rates').json['rates'] == []
    assert transmit(client,meter_payload()).status_code == 422
    with app.app_context():
        assert db.session.scalar(select(func.count(Reading.id))) == 0
        assert db.session.get(Account,1).balance == 100
    assert post(client,'/api/rates',{'rate':'11.25'}).status_code == 200
    assert transmit(client,meter_payload(timestamp=utcnow())).status_code == 201
