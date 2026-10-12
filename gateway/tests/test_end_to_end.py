"""Opt-in, isolated real-network MQTT/TLS/Flask/PostgreSQL integration check."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from types import SimpleNamespace
import pytest

pytestmark = pytest.mark.skipif(os.getenv('RUN_GATEWAY_E2E') != '1', reason='Opt in with a dedicated PostgreSQL test database and Mosquitto binaries.')


def eventually(check, seconds=45):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(.1)
    raise AssertionError('The isolated integration condition was not reached before its deadline.')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def certificates(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    import ipaddress
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost isolated test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
            .not_valid_after(now+timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False).sign(key, hashes.SHA256()))
    certificate, private = directory/'localhost.crt', directory/'localhost.key'
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private.chmod(0o600)
    return certificate, private


class Faults:
    def __init__(self, application):
        self.application = application; self.unavailable = True; self.lose_one_receipt = False; self.lost = 0; self.malformed = False
    def __call__(self, environ, start_response):
        if environ['PATH_INFO'] == '/api/iot/readings' and environ['REQUEST_METHOD'] == 'POST':
            if self.malformed:
                start_response('201 Created', [('Content-Type', 'application/json')]); return [b'{"accepted":true,"id":true}']
            if self.unavailable:
                start_response('503 Service Unavailable', [('Content-Type', 'application/json')]); return [b'{}']
            if self.lose_one_receipt:
                from werkzeug.test import run_wsgi_app
                body, status, headers = run_wsgi_app(self.application, environ, buffered=True)
                if status.startswith('201'):
                    self.lose_one_receipt = False; self.lost += 1
                    start_response('503 Service Unavailable', [('Content-Type', 'application/json')]); return [b'{}']
                start_response(status, headers); return body
        return self.application(environ, start_response)


def test_three_rooms_network_restart_and_dashboard(tmp_path):
    from sqlalchemy import func, select, text
    from sqlalchemy.engine import make_url
    from werkzeug.security import generate_password_hash
    from werkzeug.serving import make_server, WSGIRequestHandler
    from dormenergy import create_app
    from dormenergy.extensions import db
    from dormenergy.models import Account, Device, Rate, Reading, Transaction, Unit, User, utcnow
    from dormenergy_gateway.config import load
    from dormenergy_gateway.development import publish
    from dormenergy_gateway.service import Gateway, mqtt_client

    database = os.getenv('GATEWAY_TEST_DATABASE_URL', '')
    parsed = make_url(database)
    assert parsed.get_backend_name() == 'postgresql' and (parsed.database or '').endswith('_test'), 'Use an expendable PostgreSQL database ending in _test.'
    broker_binary = os.getenv('MOSQUITTO_BIN') or shutil.which('mosquitto')
    password_binary = os.getenv('MOSQUITTO_PASSWD_BIN') or shutil.which('mosquitto_passwd')
    assert broker_binary and password_binary, 'Install Mosquitto and its password utility.'
    # Credentials are generated for this isolated test only and never printed or passed as argv.
    credentials = {name: secrets.token_urlsafe(32) for name in ('gateway', 'room1', 'room2', 'room3')}
    tokens = {i: secrets.token_urlsafe(32) for i in range(1, 4)}
    password_file = tmp_path/'broker.passwd'
    password_file.write_text(''.join(f'{name}:{value}\n' for name, value in credentials.items())); password_file.chmod(0o600)
    converted = subprocess.run([password_binary, '-U', str(password_file)], capture_output=True, check=False)
    assert converted.returncode == 0, 'The isolated broker password file could not be hashed.'
    for name, password in credentials.items():
        path = tmp_path/f'{name}.secret'; path.write_text(password); path.chmod(0o600)
    for index, token in tokens.items():
        path = tmp_path/f'token-{index}'; path.write_text(token); path.chmod(0o600)
    ca, key = certificates(tmp_path)
    mqtt_port = free_port()
    acl = tmp_path/'broker.acl'
    sections = ['user gateway\n']
    for index in range(1, 4):
        root = f'dormenergy/floor{index}/room{index}'
        sections.append(f'topic read {root}/telemetry\ntopic read {root}/status\ntopic write {root}/ack\n')
    for index in range(1, 4):
        root = f'dormenergy/floor{index}/room{index}'
        sections.append(f'user room{index}\ntopic write {root}/telemetry\ntopic write {root}/status\ntopic read {root}/ack\n')
    acl.write_text(''.join(sections))
    broker_config = tmp_path/'broker.conf'
    settings = [f'listener {mqtt_port} 127.0.0.1', 'allow_anonymous false', 'use_username_as_clientid true', f'password_file {password_file.as_posix()}',
                f'acl_file {acl.as_posix()}', f'certfile {ca.as_posix()}', f'keyfile {key.as_posix()}',
                'tls_version tlsv1.2', 'persistence true', f'persistence_location {tmp_path.as_posix()}/', 'autosave_interval 1']
    if os.name != 'nt' and os.getuid() == 0:
        settings.append('user root')  # Loopback temporary test broker only; production runs as mosquitto.
    broker_config.write_text('\n'.join(settings)+'\n')
    broker_log = open(tmp_path/'broker.log', 'wb')
    broker = None; gateway = None; worker = None; server = None; server_thread = None; killed_process = None
    started = time.monotonic(); cpu_started = time.process_time(); recoveries = {}; upload_latency = []
    def start_broker():
        process = subprocess.Popen([broker_binary, '-c', str(broker_config)], stdout=broker_log, stderr=subprocess.STDOUT)
        def listening():
            try:
                with socket.create_connection(('127.0.0.1', mqtt_port), timeout=.2): return True
            except OSError: return False
        eventually(listening, 10)
        return process
    app = create_app({'TESTING': True, 'SECRET_KEY': secrets.token_urlsafe(32), 'SQLALCHEMY_DATABASE_URI': database,
                      'SIMULATION_MODE': False, 'UTILITY_RATE_LOCKED': False, 'EMAIL_BACKEND': 'disabled',
                      'SESSION_COOKIE_SECURE': False, 'PUBLIC_BASE_URL': 'https://localhost'})
    test_password = secrets.token_urlsafe(24)
    with app.app_context():
        db.drop_all(); db.session.execute(text('DROP TABLE IF EXISTS alembic_version')); db.session.commit()
        from flask_migrate import upgrade
        upgrade(directory='migrations')
        for index in range(1, 4):
            unit = Unit(id=index, name=f'Isolated room {index}', room=f'Floor {index}', is_demo=False)
            db.session.add(unit); db.session.flush()
            db.session.add_all([Account(unit_id=index, balance=Decimal('1000'), low_threshold=Decimal('20')),
                               Device(identifier=f'DEV-00{index}', unit_id=index, source='hardware', token_hash=generate_password_hash(tokens[index]))])
            tenant = User(name=f'Isolated tenant {index}', email=f'tenant{index}@example.test', role='tenant', unit_id=index)
            tenant.set_password(test_password); db.session.add(tenant)
        owner = User(name='Isolated owner', email='owner@example.test', role='admin'); owner.set_password(test_password); db.session.add(owner)
        db.session.add(Rate(php_per_kwh=Decimal('12.5'), effective_at=utcnow()-timedelta(days=2)))
        db.session.commit()
    faults = Faults(app)
    class QuietRequestHandler(WSGIRequestHandler):
        def log(self, *args, **kwargs): pass
    server = make_server('127.0.0.1', 0, faults, threaded=True, ssl_context=(str(ca), str(key)), request_handler=QuietRequestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True); server_thread.start()
    config_path = tmp_path/'gateway.json'
    config_path.write_text(json.dumps(dict(mode='isolated_test', database=str(tmp_path/'queue.sqlite3'),
        mqtt=dict(host='127.0.0.1', port=mqtt_port, ca_file=str(ca), username='gateway', password_file=str(tmp_path/'gateway.secret'), client_id='isolated-gateway'),
        cloud=dict(enabled=True, base_url=f'https://127.0.0.1:{server.server_port}', ca_file=str(ca), timeout_seconds=2, max_attempts=8),
        storage=dict(min_free_bytes=0), devices=[dict(alias=f'room{i}', device_id=f'DEV-00{i}', unit_id=i,
        topic=f'dormenergy/floor{i}/room{i}/telemetry', ack_topic=f'dormenergy/floor{i}/room{i}/ack', status_topic=f'dormenergy/floor{i}/room{i}/status',
        meter_epoch='epoch-1', token_file=str(tmp_path/f'token-{i}')) for i in range(1, 4)])))
    config = load(config_path)
    def launch():
        instance = Gateway(config)
        original_request = instance.sync.cloud.request
        def measured_request(*args, **kwargs):
            beginning = time.perf_counter()
            try: return original_request(*args, **kwargs)
            finally:
                if args[0] == 'POST': upload_latency.append(time.perf_counter()-beginning)
        instance.sync.cloud.request = measured_request
        thread = threading.Thread(target=instance.run); thread.start()
        eventually(lambda: instance.client.is_connected(), 10)
        return instance, thread
    def stop_gateway():
        gateway.stop.set(); worker.join(15); assert not worker.is_alive()
    def count():
        with app.app_context(): return db.session.scalar(select(func.count(Reading.id)))
    def publish_wave(scenario, indexes=(1, 2, 3)):
        errors = []
        def send(index):
            try:
                publish(config, SimpleNamespace(device=f'room{index}', username=f'room{index}', password_file=str(tmp_path/f'room{index}.secret'),
                                               journal=str(tmp_path/f'publisher-{index}.sqlite3'), scenario=scenario))
            except Exception as error: errors.append(type(error).__name__)
        threads = [threading.Thread(target=send, args=(i,)) for i in indexes]
        for thread in threads: thread.start()
        for thread in threads: thread.join(20); assert not thread.is_alive()
        assert not errors
        import sqlite3
        for index in indexes:
            with sqlite3.connect(tmp_path/f'publisher-{index}.sqlite3') as fixtures:
                assert fixtures.execute('SELECT count(*) FROM fixtures WHERE acked=0').fetchone()[0] == 0
    try:
        broker = start_broker(); gateway, worker = launch()
        from dataclasses import replace
        from dormenergy_gateway.cloud import Cloud
        assert Cloud(replace(config, cloud_ca_file=None)).request('GET', '/health').status == 0
        unauthorized = mqtt_client(config, 'isolated-unauthorized', 'unknown-test-user', 'invalid-test-only-password')
        rejected = threading.Event()
        unauthorized.on_connect = lambda client, userdata, flags, reason, properties: rejected.set() if reason.is_failure else None
        unauthorized.connect(config.mqtt_host, mqtt_port); unauthorized.loop_start()
        assert rejected.wait(5) and not unauthorized.is_connected()
        unauthorized.disconnect(); unauthorized.loop_stop()
        publish_wave('duplicate')
        assert gateway.journal.health()['unsent'] == 9 and count() == 0
        outage_started = time.monotonic()
        while time.monotonic()-outage_started < 60:
            assert gateway.journal.health()['unsent'] == 9 and not gateway.failed and worker.is_alive()
            time.sleep(.2)
        outage_seconds = round(time.monotonic()-outage_started, 3)
        faults.unavailable = False
        eventually(lambda: gateway.journal.health()['unsent'] == 0)
        assert count() == 9
        # Authenticated room 1 must not inject a frame on room 2's authorized topic.
        wrong = mqtt_client(config, 'isolated-forged-topic', 'room1', credentials['room1'])
        wrong.connect(config.mqtt_host, mqtt_port); wrong.loop_start()
        original = json.loads(gateway.journal.db.execute("SELECT payload FROM measurements WHERE alias='room2' LIMIT 1").fetchone()[0])
        original.pop('gateway_fixture'); original.update(measurement_origin='development_fixture', clock_synchronized=True, event_id='forged-cross-room')
        wrong.publish(config.devices[1].topic, json.dumps(original), qos=1).wait_for_publish(3)
        time.sleep(.5); wrong.disconnect(); wrong.loop_stop()
        assert gateway.journal.db.execute("SELECT count(*) FROM measurements WHERE event_id='forged-cross-room'").fetchone()[0] == 0
        faults.unavailable = True
        publish_wave('disconnect')
        assert gateway.journal.health()['unsent'] == 9
        stop_gateway()
        faults.unavailable = False; faults.lose_one_receipt = True
        gateway, worker = launch()
        eventually(lambda: gateway.journal.health()['unsent'] == 0)
        assert faults.lost == 1 and count() == 18
        assert gateway.journal.db.execute("SELECT count(*) FROM measurements WHERE error='confirmed_duplicate'").fetchone()[0] == 1
        # Local broker failure/restart exercises the persistent client reconnect path.
        broker.terminate(); broker.wait(10)
        eventually(lambda: not gateway.client.is_connected(), 10)
        broker = start_broker()
        eventually(lambda: gateway.client.is_connected(), 15)
        publish_wave('normal')
        eventually(lambda: gateway.journal.health()['unsent'] == 0)
        assert count() == 27
        with app.app_context():
            assert all(a.balance == Decimal('900') for a in db.session.scalars(select(Account)))
            assert db.session.scalar(select(func.count(Transaction.id))) == 24
            assert all(r.source == 'hardware' for r in db.session.scalars(select(Reading)))
        # Verify the real dashboard route and the tenant-scoped data it renders.
        for email, expected_units in [('owner@example.test', 3), ('tenant1@example.test', 1), ('tenant2@example.test', 1), ('tenant3@example.test', 1)]:
            browser = app.test_client()
            login_page = browser.get('/login')
            token = re.search(r'name="csrf-token" content="([^"]+)"', login_page.get_data(as_text=True)).group(1)
            signed_in = browser.post('/login', data={'email': email, 'password': test_password, 'csrf_token': token})
            assert signed_in.status_code == 302
            dashboard = browser.get('/dashboard'); assert dashboard.status_code == 200 and b'workspace' in dashboard.data.lower()
            snapshot = browser.get('/api/snapshot'); assert snapshot.status_code == 200
            assert snapshot.json['simulation'] is False and len(snapshot.json['units']) == expected_units
            assert all(Decimal(u['balance']) == Decimal('900') for u in snapshot.json['units'])
        if os.getenv('RUN_GATEWAY_BROWSER') == '1':
            # Browser rendering uses its own explicitly loopback HTTP fixture server.
            # Gateway API traffic above still uses verified HTTPS; no TLS checks are disabled.
            browser_server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequestHandler)
            browser_thread = threading.Thread(target=browser_server.serve_forever, daemon=True); browser_thread.start()
            try:
                import sys
                result = subprocess.run([os.getenv('GATEWAY_BROWSER_PYTHON', sys.executable),
                    str(Path(__file__).resolve().parents[1]/'scripts/check-test-dashboard.py')],
                    input=json.dumps(dict(origin=f'http://127.0.0.1:{browser_server.server_port}', password=test_password,
                                         artifacts=str(Path('artifacts').resolve()))),
                    capture_output=True, text=True, timeout=60, check=False)
                diagnostic = result.stderr
                for value in (test_password, *tokens.values(), *credentials.values()):
                    diagnostic = diagnostic.replace(value, '[redacted]')
                assert result.returncode == 0, 'Optional Chromium check failed: '+diagnostic[-2500:]
                print(result.stdout.strip())
            finally:
                browser_server.shutdown(); browser_server.server_close(); browser_thread.join(5)
        # Only one room reports an online status; its readings cannot appear under another room.
        publish_wave('normal', indexes=(1,))
        eventually(lambda: gateway.journal.health()['unsent'] == 0)
        assert count() == 30
        status_client = mqtt_client(config, 'isolated-status', 'room1', credentials['room1'])
        status_client.connect(config.mqtt_host, mqtt_port); status_client.loop_start()
        eventually(status_client.is_connected, 5)
        status_client.publish(config.devices[0].status_topic, json.dumps(dict(device_id='DEV-001',unit_id=1,online=True)),qos=1,retain=True).wait_for_publish(5)
        eventually(lambda: sum(d['online'] for d in gateway.journal.health()['devices']) == 1)
        status_client.disconnect(); status_client.loop_stop()

        def drain(label):
            beginning = time.monotonic()
            eventually(lambda: gateway.journal.health()['unsent'] == 0)
            recoveries[label] = round(time.monotonic()-beginning, 3)
            assert not gateway.failed and worker.is_alive()

        # Rotated credentials block only room 1; the original valid credential cannot be reused.
        rotated = secrets.token_urlsafe(32)
        with app.app_context():
            db.session.get(Device,1).token_hash = generate_password_hash(rotated); db.session.commit()
        publish_wave('normal')
        eventually(lambda: gateway.journal.health()['queue_states'].get('blocked_auth') == 1 and count() == 36)
        assert gateway.journal.health()['unsent'] == 3
        assert Cloud(config).request('GET','/api/iot/readings/receipt?event_id=unknown',config.devices[0]).status == 401
        config.devices[0].token_file.write_text(rotated)
        gateway.journal.resume('room1'); drain('credential_rotation'); assert count() == 39

        # Unavailable coverage must not commit a partial reading or charge.
        with app.app_context():
            db.session.get(Rate,1).valid_until = utcnow()-timedelta(hours=1); db.session.commit()
        publish_wave('normal')
        eventually(lambda: gateway.journal.health()['queue_states'].get('blocked_tariff') == 3)
        assert count() == 39 and gateway.journal.health()['unsent'] == 9
        with app.app_context():
            db.session.get(Rate,1).valid_until = None; db.session.commit()
        for device in config.devices: gateway.journal.resume(device.alias)
        drain('tariff_restored'); assert count() == 48

        # Database-driver failure is injected only on this disposable app's engine.
        # The shared native PostgreSQL daemon and other databases stay available.
        from sqlalchemy import event
        from sqlalchemy.exc import OperationalError
        with app.app_context(): engine = db.engine
        def unavailable_database(*args):
            raise OperationalError('test database unavailable',{},OSError('isolated fault'))
        event.listen(engine,'before_cursor_execute',unavailable_database)
        try:
            assert Cloud(config).request('GET','/health').status == 503
            publish_wave('normal')
            assert gateway.journal.health()['unsent'] == 9
        finally: event.remove(engine,'before_cursor_execute',unavailable_database)
        assert count() == 48
        drain('database_restored'); assert count() == 57

        # A severed loopback HTTPS path models an unavailable internet uplink.
        # This is transport loss, not a physical Wi-Fi or ISP field test.
        server_port = server.server_port
        server.shutdown(); server.server_close(); server_thread.join(5)
        assert Cloud(config).request('GET','/health').status == 0
        publish_wave('normal'); assert gateway.journal.health()['unsent'] == 9 and count() == 57
        server = make_server('127.0.0.1',server_port,faults,threaded=True,ssl_context=(str(ca),str(key)),request_handler=QuietRequestHandler)
        server_thread = threading.Thread(target=server.serve_forever,daemon=True); server_thread.start()
        drain('https_path_restored'); assert count() == 66

        faults.malformed = True; publish_wave('normal')
        assert gateway.journal.health()['unsent'] == 9 and count() == 66
        faults.malformed = False; drain('malformed_response_repaired'); assert count() == 75

        # Kill a real gateway child process without cleanup, after its SQLite commits.
        import sqlite3, sys
        stop_gateway(); faults.unavailable = True
        with open(tmp_path/'killed-gateway.log','wb') as child_log:
            killed_process = subprocess.Popen([sys.executable,'-m','dormenergy_gateway','run','--config',str(config_path)],stdout=child_log,stderr=child_log)
            def child_connected():
                with sqlite3.connect(config.database) as queue:
                    row = queue.execute("SELECT value FROM metadata WHERE key='broker'").fetchone()
                    return killed_process.poll() is None and row and json.loads(row[0]).get('connected') is True
            eventually(child_connected,10)
            publish_wave('normal')
            with sqlite3.connect(config.database) as queue:
                assert queue.execute("SELECT count(*) FROM measurements WHERE status!='sent'").fetchone()[0] == 9
            killed_process.kill(); killed_process.wait(10)
            assert killed_process.returncode != 0
        assert count() == 75
        faults.unavailable = False; gateway, worker = launch(); drain('sigkill_restart'); assert count() == 84
        # Sustained repeated three-room communication, with exact reconciliation after every wave.
        for wave in range(10):
            publish_wave('normal'); drain(f'stress_wave_{wave+1}')
            assert count() == 84+9*(wave+1)
        with app.app_context():
            readings = db.session.scalar(select(func.count(Reading.id)))
            transactions = db.session.scalars(select(Transaction)).all()
            assert readings == 174 and len(transactions) == 171
            assert len({t.reading_id for t in transactions}) == 171
            for account in db.session.scalars(select(Account)):
                charged = sum(-t.amount for t in transactions if t.unit_id == account.unit_id)
                assert account.balance == Decimal('1000')-charged
        from statistics import median
        import math, resource
        ordered = sorted(upload_latency)
        report = dict(software_fixtures_only=True,rooms=3,accepted_readings=174,consumption_transactions=171,
            duplicate_billing_incidents=0,final_unreconciled_readings=0,delivery_percent=100,
            elapsed_seconds=round(time.monotonic()-started,3),cloud_outage_seconds=outage_seconds,process_cpu_seconds=round(time.process_time()-cpu_started,3),
            peak_process_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,3),
            unexpected_worker_failures=0,intentional_process_kills=1,recovery_seconds=recoveries,
            https_post=dict(samples=len(ordered),median_ms=round(median(ordered)*1000,3),p95_ms=round(ordered[math.ceil(len(ordered)*.95)-1]*1000,3)),
            database_failure='Scoped SQLAlchemy OperationalError injection; native shared database daemon was not stopped.',
            internet_failure='Loopback HTTPS listener removed and restored; no physical WAN outage claimed.')
        artifacts = Path('artifacts/audit'); artifacts.mkdir(parents=True,exist_ok=True)
        (artifacts/'network-reliability.json').write_text(json.dumps(report,indent=2))
        print('ISOLATED E2E: 3 rooms, 174 readings, 171 unique charges; verified TLS/ACL, lost receipts, transport/SQL failures, credential/tariff recovery, actual process kill, stress and admin/tenant scopes passed.')
    finally:
        if killed_process and killed_process.poll() is None: killed_process.kill(); killed_process.wait(10)
        if gateway and worker and worker.is_alive(): stop_gateway()
        if broker and broker.poll() is None: broker.terminate(); broker.wait(10)
        if server: server.shutdown(); server.server_close()
        if server_thread: server_thread.join(5)
        broker_log.close()
        with app.app_context():
            db.session.remove(); db.drop_all(); db.session.execute(text('DROP TABLE IF EXISTS alembic_version')); db.session.commit(); db.engine.dispose()
