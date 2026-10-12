from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select, func
from dormenergy.extensions import db
from dormenergy.models import Account, Device, DeviceEvent, LoadRequest, Rate, Reading, Transaction, Unit, utcnow
from dormenergy.services.simulation import simulation_step
from conftest import csrf_token, login, meter_payload, post, transmit


def test_authentication_csrf_logout(client):
    assert client.get('/api/snapshot').status_code==401
    response = client.post('/login',data={'email':'admin@test.local','password':'anything'})
    assert response.status_code==303 and response.headers['Location']=='/login'
    assert client.get('/api/snapshot').status_code==401
    assert login(client,password='wrong').status_code==401
    assert login(client).status_code==302
    assert client.get('/dashboard').status_code==200
    assert client.get('/api/snapshot').json['user']['role']=='admin'
    token=csrf_token(client)
    assert client.post('/logout',data={'csrf_token':token}).status_code==302
    assert client.get('/api/snapshot').status_code==401


def test_tenant_isolation_and_admin_permissions(client):
    login(client,'one@test.local')
    data=client.get('/api/snapshot').json
    assert [u['id'] for u in data['units']]==[1]
    assert client.get('/api/snapshot?unit_id=2').status_code==403
    assert client.get('/api/transactions/export?unit_id=2').status_code==403
    assert post(client,'/api/rates',{'rate':'13'}).status_code==403
    assert post(client,'/api/load-requests/1/review',{'decision':'approved'}).status_code==403
    assert post(client,'/api/units',{'name':'Unit 03','room':'203','device_id':'HW-003'}).status_code==403
    assert client.get('/api/rates').status_code==403
    response=post(client,'/api/load-requests',{'amount':'50','unit_id':2,'reference':'isolation'})
    assert response.status_code==201
    assert response.json['request']['unit_id']==1


def test_approval_is_atomic_and_cannot_load_twice(client,app):
    login(client,'one@test.local')
    response=post(client,'/api/load-requests',{'amount':'500','reference':'approval-1'})
    assert response.status_code==201
    request_id=response.json['request']['id']
    assert post(client,'/api/load-requests',{'amount':'500','reference':'approval-1'}).status_code==409
    client.post('/logout',data={'csrf_token':csrf_token(client)})
    login(client)
    assert post(client,f'/api/load-requests/{request_id}/review',{'decision':'approved'}).status_code==200
    assert post(client,f'/api/load-requests/{request_id}/review',{'decision':'approved'}).status_code==409
    with app.app_context():
        assert db.session.get(Account,1).balance==Decimal('600')
        assert db.session.scalar(select(func.count(Transaction.id)).where(Transaction.load_request_id==request_id))==1
        assert db.session.get(LoadRequest,request_id).status=='approved'


def test_rejection_does_not_add_credit(client,app):
    login(client,'one@test.local')
    request_id=post(client,'/api/load-requests',{'amount':'100','reference':'reject-1'}).json['request']['id']
    client.post('/logout',data={'csrf_token':csrf_token(client)})
    login(client)
    assert post(client,f'/api/load-requests/{request_id}/review',{'decision':'rejected'}).status_code==200
    with app.app_context():
        assert db.session.get(Account,1).balance==Decimal('100')
        assert db.session.scalar(select(func.count(Transaction.id)))==0


def test_billing_and_duplicates(client,app):
    now=utcnow()-timedelta(seconds=1)
    first=meter_payload(timestamp=now-timedelta(hours=1))
    assert transmit(client,first).status_code==201
    second=meter_payload('10.1',timestamp=now,event='event-2')
    assert transmit(client,second).status_code==201
    assert transmit(client,second).status_code==409
    assert transmit(client,meter_payload('10.2',timestamp=now-timedelta(seconds=1),event='late')).status_code==409
    with app.app_context():
        account=db.session.get(Account,1)
        assert account.balance==Decimal('98.750000')
        assert db.session.scalar(select(func.count(Reading.id)))==2
        transaction=db.session.scalar(select(Transaction))
        assert transaction.amount==Decimal('-1.250000')
        assert transaction.balance_after==account.balance


def test_invalid_measurements_and_meter_reset(client,app):
    now=utcnow()-timedelta(seconds=1)
    assert transmit(client,meter_payload(timestamp=now-timedelta(minutes=1))).status_code==201
    assert transmit(client,meter_payload('NaN',timestamp=now,event='invalid')).status_code==422
    assert transmit(client,meter_payload('9',timestamp=now,event='decrease')).status_code==422
    assert transmit(client,meter_payload('0',timestamp=now,event='reset',meter_epoch='epoch-2')).status_code==422
    assert transmit(client,meter_payload('0',timestamp=now,event='reset',meter_epoch='epoch-2',reset_confirmed=True)).status_code==201
    with app.app_context():
        assert db.session.get(Account,1).balance==Decimal('100')
        assert db.session.scalar(select(DeviceEvent).where(DeviceEvent.kind=='meter_reset'))


def test_scoped_device_tokens_and_no_control(client):
    assert transmit(client,meter_payload(),token='wrong').status_code==401
    assert transmit(client,meter_payload(unit_id=2)).status_code==403
    assert transmit(client,meter_payload(device_id='SIM-002')).status_code==403
    assert transmit(client,meter_payload(),identifier='SIM-002').status_code==401
    result=client.get('/api/iot/instructions',headers={'X-Device-ID':'HW-001','Authorization':'Bearer TestsOnlyScopedDeviceToken'})
    assert result.status_code==200
    assert result.json['commands']==[] and not result.json['control_enabled']


def test_rates_preserve_history_and_split_intervals(client,app):
    now=utcnow()-timedelta(seconds=1)
    with app.app_context():
        db.session.add(Rate(php_per_kwh=Decimal('20'),effective_at=now-timedelta(minutes=30)))
        db.session.commit()
    assert transmit(client,meter_payload(timestamp=now-timedelta(hours=1))).status_code==201
    assert transmit(client,meter_payload('11',timestamp=now,event='split')).status_code==201
    with app.app_context():
        assert db.session.get(Account,1).balance==Decimal('83.750000')  # 0.5×12.5 + 0.5×20
    login(client)
    assert post(client,'/api/rates',{'rate':'25'}).status_code==200
    with app.app_context():
        assert db.session.scalar(select(Transaction)).amount==Decimal('-16.250000')


def test_simulation_updates_database_and_tenant_snapshot(client,app):
    with app.app_context():
        assert simulation_step()==1
        device=db.session.get(Device,2)
        device.last_timestamp=utcnow()-timedelta(seconds=10)
        db.session.commit()
        assert simulation_step()==1
        readings=db.session.scalars(select(Reading).where(Reading.device_id==2).order_by(Reading.timestamp)).all()
        assert len(readings)==2 and readings[-1].delta_kwh>0
        assert db.session.get(Account,2).balance<100
    login(client,'two@test.local')
    snapshot=client.get('/api/snapshot').json
    assert snapshot['simulation'] and snapshot['units'][0]['source']=='simulation'
    assert snapshot['summary']['online']==1
    assert snapshot['forecast']['status']=='insufficient_data'
    assert snapshot['forecast']['remaining_duration'] is None


def test_offline_readings_are_not_presented_as_live(client,app):
    assert transmit(client,meter_payload(timestamp=utcnow()-timedelta(hours=1))).status_code==201
    login(client)
    unit=client.get('/api/snapshot').json['units'][0]
    assert unit['gateway_online'] and not unit['online']
    assert unit['power'] is None and unit['power_status']=='unknown'


def test_credit_exhaustion_requires_review_not_actuation(client,app):
    now=utcnow()-timedelta(seconds=1)
    assert transmit(client,meter_payload(timestamp=now-timedelta(hours=1))).status_code==201
    assert transmit(client,meter_payload('20',timestamp=now,event='exhausted')).status_code==201
    with app.app_context():
        assert db.session.get(Account,1).balance==Decimal('-25')
        event=db.session.scalar(select(DeviceEvent).where(DeviceEvent.kind=='credit_exhausted'))
        assert event.state=='requires_review'
        assert db.session.get(Unit,1).power_status=='connected'  # observed state, never invented physical control


def test_registration_and_assignment(client,app):
    login(client)
    result=post(client,'/api/units',{'name':'Unit 03','room':'Room 203','device_id':'HW-003'})
    assert result.status_code==201
    unit_id=result.json['id']
    assert post(client,f'/api/units/{unit_id}/tenant',{'name':'New Tenant','email':'new@test.local','password':'InitialPassword!2026'}).status_code==201
    assert post(client,f'/api/units/{unit_id}/tenant',{'name':'Another','email':'another@test.local','password':'InitialPassword!2026'}).status_code==409
    client.post('/logout',data={'csrf_token':csrf_token(client)})
    assert login(client,'new@test.local','InitialPassword!2026').status_code==302
    assert client.get('/api/snapshot').json['password_change_required']
    import re
    token=re.search(r'name="csrf-token" content="([^"]+)"',client.get('/account/password').get_data(as_text=True)).group(1)
    assert client.post('/account/password',data={'csrf_token':token,'current_password':'InitialPassword!2026',
        'new_password':'TenantOwnPassword!2026','confirm_password':'TenantOwnPassword!2026'}).status_code==302
    assert [u['id'] for u in client.get('/api/snapshot').json['units']]==[unit_id]


def test_simultaneous_credit_approvals(app):
    with app.app_context():
        if db.engine.dialect.name!='postgresql':
            import pytest
            pytest.skip('Row-lock concurrency requires PostgreSQL.')
    with app.test_client() as client:
        login(client,'one@test.local')
        request_id=post(client,'/api/load-requests',{'amount':'50','reference':'concurrent'}).json['request']['id']
    def review():
        with app.test_client() as client:
            login(client)
            return post(client,f'/api/load-requests/{request_id}/review',{'decision':'approved'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda _:review(),range(2)))
    assert sorted(statuses)==[200,409]
    with app.app_context():
        assert db.session.get(Account,1).balance==Decimal('150')
        assert db.session.scalar(select(func.count(Transaction.id)))==1


def test_malformed_json_and_money_validation(client):
    login(client,'one@test.local')
    for payload in (['not-an-object'],{'amount':'NaN'},{'amount':'1.234'},{'amount':-10},{'amount':True}):
        assert post(client,'/api/load-requests',payload).status_code==422


def test_simulated_protective_trip_is_never_reclosed(app):
    with app.app_context():
        db.session.get(Unit,2).power_status='protective_trip'
        db.session.commit()
        simulation_step()
        assert db.session.get(Unit,2).power_status=='protective_trip'
        assert db.session.scalar(select(Reading).where(Reading.device_id==2)).power==0


def test_simultaneous_meter_transmissions_charge_once(app):
    with app.app_context():
        if db.engine.dialect.name!='postgresql':
            import pytest
            pytest.skip('Row-lock concurrency requires PostgreSQL.')
    now=utcnow()-timedelta(seconds=1)
    with app.test_client() as client:
        assert transmit(client,meter_payload(timestamp=now-timedelta(minutes=1))).status_code==201
    payload=meter_payload('10.1',timestamp=now,event='same-concurrent-packet')
    def send():
        with app.test_client() as client:
            return transmit(client,payload).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda _:send(),range(2)))
    assert sorted(statuses)==[201,409]
    with app.app_context():
        assert db.session.get(Account,1).balance==Decimal('98.75')
        assert db.session.scalar(select(func.count(Transaction.id)))==1
