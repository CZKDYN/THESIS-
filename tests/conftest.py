import os
from datetime import timedelta
from decimal import Decimal
import pytest
from flask_migrate import upgrade
from sqlalchemy.engine import make_url
from werkzeug.security import generate_password_hash
from dormenergy import create_app
from dormenergy.extensions import db
from dormenergy.models import Account, Device, Rate, Unit, User, utcnow

PASSWORD = 'TestsOnlyPassword!2026'
DEVICE_TOKEN = 'TestsOnlyScopedDeviceToken'


@pytest.fixture()
def app(tmp_path):
    url = os.getenv('TEST_DATABASE_URL', f'sqlite:///{tmp_path}/test.sqlite')
    parsed = make_url(url)
    if parsed.get_backend_name() == 'postgresql' and not (parsed.database or '').endswith('_test'):
        pytest.fail('TEST_DATABASE_URL must refer to a dedicated database ending in _test.')
    application = create_app({'TESTING': True, 'SECRET_KEY': 'only-for-tests',
                              'SQLALCHEMY_DATABASE_URI': url, 'SIMULATION_MODE': True,
                              'UTILITY_RATE_LOCKED': False})  # Existing manual-rate regression fixtures.
    with application.app_context():
        # Clear only the dedicated test DB; never the development database.
        db.drop_all()
        if parsed.get_backend_name() == 'postgresql':
            from sqlalchemy import text
            db.session.execute(text('DROP TABLE IF EXISTS alembic_version'))
            db.session.commit()
        upgrade(directory='migrations')
        unit1 = Unit(name='Unit 01', room='Room 201', is_demo=True, power_status='connected')
        unit2 = Unit(name='Unit 02', room='Room 202', is_demo=True, power_status='connected')
        db.session.add_all([unit1, unit2])
        db.session.flush()
        for unit in (unit1, unit2):
            db.session.add(Account(unit_id=unit.id, balance=Decimal('100'), low_threshold=Decimal('20')))
        db.session.add_all([
            Device(identifier='HW-001', unit_id=unit1.id, source='hardware', token_hash=generate_password_hash(DEVICE_TOKEN)),
            Device(identifier='SIM-002', unit_id=unit2.id, source='simulation'),
            Rate(php_per_kwh=Decimal('12.5'), effective_at=utcnow()-timedelta(days=2))])
        for name,email,role,unit_id in [('Admin','admin@test.local','admin',None),('Tenant 1','one@test.local','tenant',unit1.id),('Tenant 2','two@test.local','tenant',unit2.id)]:
            user=User(name=name,email=email,role=role,unit_id=unit_id)
            user.set_password(PASSWORD)
            db.session.add(user)
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf_token(client):
    import re
    response=client.get('/login' if client.get('/api/snapshot').status_code==401 else '/dashboard')
    return re.search(r'name="csrf-token" content="([^"]+)"',response.get_data(as_text=True)).group(1)


def login(client, email='admin@test.local', password=PASSWORD):
    return client.post('/login',data={'email':email,'password':password,'csrf_token':csrf_token(client)})


def post(client, path, payload):
    return client.post(path,json=payload,headers={'X-CSRFToken':csrf_token(client)})


def meter_payload(energy='10', timestamp=None, event='event-1', **overrides):
    payload=dict(device_id='HW-001',unit_id=1,event_id=event,meter_epoch='epoch-1',
                 timestamp=(timestamp or utcnow()-timedelta(seconds=20)).isoformat(),
                 voltage='230',current='1',power_w='230',energy_kwh=energy,connection_status='connected')
    payload.update(overrides)
    return payload


def transmit(client, payload, identifier='HW-001', token=DEVICE_TOKEN):
    return client.post('/api/iot/readings',json=payload,headers={'X-Device-ID':identifier,'Authorization':'Bearer '+token})
