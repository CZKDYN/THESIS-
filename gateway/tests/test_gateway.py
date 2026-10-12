from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import pytest
from dormenergy_gateway import clock as clock_module
from dormenergy_gateway.clock import Clock, ClockState
from dormenergy_gateway.cloud import Cloud, Response, Synchronizer
from dormenergy_gateway.config import ConfigurationError, load, secret
from dormenergy_gateway.queue import Journal, StorageUnavailable
from dormenergy_gateway.service import Gateway, instance_lock
from dormenergy_gateway.validation import InvalidMeasurement, validate
from .conftest import frame, item


@pytest.mark.parametrize('fields', [
    {'unit_id': 2}, {'device_id': 'other'}, {'unit_id': True}, {'event_id': 'bad/event'},
    {'clock_synchronized': False}, {'timestamp': '2026-01-01T12:00:00'}, {'seconds': 500},
    {'voltage': '-1'}, {'current': 'NaN'}, {'power_w': True}, {'energy_kwh': 'Infinity'},
    {'energy_kwh': '1.0000001'}, {'connection_status': 'online'}, {'reset_confirmed': 'true'},
    {'unexpected': 'not permitted'}, {'voltage': '301'},
])
def test_invalid_measurements(config, fields):
    with pytest.raises(InvalidMeasurement):
        item(config, **fields)


def test_precision_and_original_time(config):
    original = frame(config, energy='12345.123456', timestamp='2026-01-01T12:00:00+08:00')
    accepted = validate(original, config.devices[0], config, time.time())
    assert accepted.payload['energy_kwh'] == '12345.123456'
    assert accepted.payload['timestamp'] == '2026-01-01T04:00:00+00:00'
    assert accepted.payload['gateway_fixture'] is True


def test_large_and_ambiguous_json(config):
    for raw in (b' '*8193, b'{"unit_id":1,"unit_id":2}', b'[]', b'\xff', b'['*4096+b']'*4096):
        with pytest.raises(InvalidMeasurement):
            validate(raw, config.devices[0], config, time.time())


def test_real_fixture_guard(config):
    with pytest.raises(InvalidMeasurement, match='fixture_forbidden'):
        item(replace(config, mode='real'))


def test_durable_restart_conflict_and_cleanup(config):
    one = item(config)
    db = Journal(config)
    assert db.enqueue(one) == 'stored'
    row = db.claim(); assert row['attempts'] == 1
    db.close()
    db = Journal(config); db.recover()
    assert db.health()['unsent'] == 1
    row = db.claim(); db.outcome(row['id'], 'sent')
    db.db.execute('UPDATE measurements SET synced_at=?', (time.time()-10*86400,))
    db.purge()
    assert db.db.execute('SELECT count(*) FROM measurements').fetchone()[0] == 0
    assert db.enqueue(one) == 'already_stored'
    with pytest.raises(InvalidMeasurement, match='event_content_conflict'):
        db.enqueue(item(config, energy='11', timestamp=one.payload['timestamp']))
    with pytest.raises(InvalidMeasurement, match='counter_decreased'):
        db.enqueue(item(config, event='decrease', seconds=-50, energy='9'))
    db.close()


def test_delayed_sorting_and_room_isolation(config, journal):
    journal.enqueue(item(config, event='new', seconds=-10, energy='12'))
    journal.enqueue(item(config, event='old', seconds=-30, energy='10'))
    journal.enqueue(item(config, index=1, event='room2', seconds=-20))
    first = journal.claim(); assert first['event_id'] == 'old'
    journal.outcome(first['id'], 'blocked_auth', 'token_invalid')
    second = journal.claim(); assert second['alias'] == 'room2'
    journal.outcome(second['id'], 'sent')
    assert journal.claim() is None
    journal.resume('room1'); assert journal.claim()['event_id'] == 'old'


def test_backdated_counter_contradiction(config, journal):
    journal.enqueue(item(config, seconds=-10, energy='12'))
    with pytest.raises(InvalidMeasurement, match='counter_decreased'):
        journal.enqueue(item(config, event='older', seconds=-20, energy='13'))


def test_confirmed_reset_and_epoch_reuse(config, journal):
    journal.enqueue(item(config))
    reset = item(config, event='reset', seconds=-40, energy='0', meter_epoch='epoch-2', reset_confirmed=True)
    with pytest.raises(InvalidMeasurement, match='meter_reset_not_approved'):
        journal.enqueue(reset)
    journal.approve_reset('room1', 'epoch-2')
    with pytest.raises(InvalidMeasurement, match='reset_confirmation_required'):
        journal.enqueue(item(config, event='reset', seconds=-40, energy='0', meter_epoch='epoch-2'))
    assert journal.enqueue(reset) == 'stored'
    with pytest.raises(InvalidMeasurement, match='meter_epoch_reused'):
        journal.enqueue(item(config, event='wrong-reboot', seconds=-20, energy='11', reset_confirmed=True))


def test_capacity_never_deletes_unsent(config):
    db = Journal(replace(config, max_pending=1))
    db.enqueue(item(config))
    with pytest.raises(StorageUnavailable):
        db.enqueue(item(config, event='full', seconds=-30, energy='11'))
    db.purge(); assert db.health()['unsent'] == 1
    db.close()


def test_disk_space_no_commit(config, journal, monkeypatch):
    monkeypatch.setattr('dormenergy_gateway.queue.shutil.disk_usage', lambda _: SimpleNamespace(free=-1))
    with pytest.raises(StorageUnavailable):
        journal.enqueue(item(config))
    assert journal.db.execute('SELECT count(*) FROM measurements').fetchone()[0] == 0


def test_profile_cannot_retarget_data(config, journal):
    with pytest.raises(ConfigurationError, match='different mode'):
        Journal(replace(config, cloud_url='http://127.0.0.1:60000'))


def test_backup_is_consistent(config, journal, tmp_path):
    journal.enqueue(item(config))
    destination = tmp_path/'backup.sqlite3'
    journal.backup(destination)
    with sqlite3.connect(destination) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT count(*) FROM measurements').fetchone()[0] == 1
    with pytest.raises(ConfigurationError):
        journal.backup(destination)


def test_health_distinguishes_delayed_status_and_measurement(config, journal):
    journal.enqueue(item(config, seconds=-3600))
    journal.connection('room1', True)
    device = journal.health()['devices'][0]
    assert device['connection'] == 'online' and not device['measurement_fresh']
    journal.db.execute('UPDATE activity SET status_at=?', (time.time()-100,))
    assert journal.health()['devices'][0]['connection'] == 'unknown'


class FakeCloud:
    def __init__(self, *responses):
        self.responses = iter(responses); self.calls = []
    def request(self, *args):
        self.calls.append(args)
        return next(self.responses)


def sync(config, journal, *responses):
    return Synchronizer(config, journal, Clock('isolated_test'), FakeCloud(*responses))


@pytest.mark.parametrize('response,expected', [
    (Response(201, {'accepted': True, 'id': 1}), 'sent'),
    (Response(401, {}), 'blocked_auth'), (Response(403, {}), 'blocked_auth'),
    (Response(422, {'error_code': 'tariff_unavailable'}), 'blocked_tariff'),
    (Response(422, {}), 'blocked_validation'), (Response(302, {}), 'blocked_validation'),
    (Response(500, {}), 'pending'), (Response(429, {}), 'pending'), (Response(0, {}), 'pending'),
    (Response(201, {'accepted': True, 'id': True}), 'pending'),
])
def test_delivery_states(config, journal, response, expected):
    journal.enqueue(item(config))
    assert sync(config, journal, response).step()
    row = journal.db.execute('SELECT * FROM measurements').fetchone()
    assert row['status'] == expected
    assert row['payload'] == item(config, timestamp=json.loads(row['payload'])['timestamp']).canonical
    if expected == 'pending':
        assert row['next_attempt'] > time.time()


@pytest.mark.parametrize('changed,receipt_status,expected', [
    ({}, 200, 'sent'), ({'energy_kwh': '999'}, 200, 'blocked_conflict'),
    ({'unit_id': 2}, 200, 'blocked_conflict'), ({'unit_id': True}, 200, 'blocked_conflict'), ({}, 404, 'blocked_conflict'),
    ({}, 503, 'pending'), ({}, 401, 'blocked_auth'),
])
def test_409_requires_exact_scoped_receipt(config, journal, changed, receipt_status, expected):
    original = item(config); journal.enqueue(original)
    receipt = dict(original.payload); receipt.update(changed)
    synchronizer = sync(config, journal, Response(409, {'error': 'duplicate or out of order'}),
                        Response(receipt_status, {'accepted': True, 'reading': receipt}))
    synchronizer.step()
    assert journal.db.execute('SELECT status FROM measurements').fetchone()[0] == expected
    assert synchronizer.cloud.calls[1][0] == 'GET'


def test_bounded_retry_retains_payload(config, journal):
    journal.enqueue(item(config))
    synchronizer = sync(config, journal, *[Response(0, {})]*config.max_attempts)
    for _ in range(config.max_attempts):
        synchronizer.step(now=time.time()+10000)
        journal.db.execute('UPDATE measurements SET next_attempt=0')
    assert journal.health()['queue_states'] == {'blocked_retry': 1}
    assert journal.claim() is None


def test_cloud_disabled_and_clock_hold(config, journal):
    journal.enqueue(item(config))
    assert not sync(replace(config, mode='development'), journal).step()
    synchronizer = sync(config, journal)
    synchronizer.clock = SimpleNamespace(state=lambda: ClockState(False, 'ntp_not_synchronized'))
    assert not synchronizer.step()
    assert journal.health()['unsent'] == 1
    with pytest.raises(RuntimeError):
        Cloud(replace(config, mode='development')).request('GET', '/health')


def test_ntp_jump_holdover_and_recovery(monkeypatch):
    times = [1000., 1.]
    monkeypatch.setattr(clock_module.time, 'time', lambda: times[0])
    monkeypatch.setattr(clock_module.time, 'monotonic', lambda: times[1])
    ntp = [False]
    clock = Clock('real', grace=40, probe=lambda: ntp[0])
    assert not clock.state().trusted
    times[:] = [1031., 32.]; ntp[0] = True
    assert clock.state().trusted
    times[:] = [1062., 63.]; ntp[0] = False
    assert clock.state().category == 'ntp_holdover'
    times[:] = [1080., 81.]; assert not clock.state().trusted
    times[:] = [2081., 82.]; ntp[0] = True
    assert clock.state().category == 'clock_jump_detected'
    assert clock.state().category == 'clock_stabilizing'
    times[:] = [2112., 113.]; assert clock.state().trusted


def test_instance_lock_only_one_worker(config):
    with instance_lock(config.database):
        with pytest.raises(ConfigurationError):
            with instance_lock(config.database):
                pass
        with pytest.raises(OSError, match='body error'):
            with instance_lock(config.database.parent/'other'):
                raise OSError('body error')


def test_application_ack_after_commit(config, journal):
    gateway = Gateway(config, journal=journal)
    class Client:
        def __init__(self):
            self.published = []; self.acks = []; self.disconnected = False
        def publish(self, topic, payload, **kwargs):
            assert journal.health()['unsent'] == 1
            self.published.append(json.loads(payload))
        def ack(self, mid, qos): self.acks.append(mid)
        def disconnect(self): self.disconnected = True
    client = Client()
    packet = SimpleNamespace(topic=config.devices[0].topic, payload=frame(config), mid=1, qos=1)
    gateway.on_message(client, None, packet)
    assert client.published[0]['status'] == 'stored' and client.acks == [1]
    journal.config = replace(config, max_pending=1)
    packet.payload = frame(config, event='full', energy='11', seconds=-30)
    gateway.on_message(client, None, packet)
    assert client.disconnected and len(client.published) == 1 and client.acks == [1]
    packet.payload = b'['*4096+b']'*4096; packet.mid = 2
    gateway.on_message(client, None, packet)
    assert client.acks == [1, 2] and not gateway.stop.is_set()


def configuration_dict(config):
    return dict(mode=config.mode, database=str(config.database),
                mqtt=dict(host=config.mqtt_host, username=config.mqtt_username, client_id=config.mqtt_client_id,
                          password_file=str(config.mqtt_password_file)),
                cloud=dict(enabled=config.cloud_enabled, base_url=config.cloud_url),
                devices=[dict(alias=d.alias, device_id=d.device_id, unit_id=d.unit_id, topic=d.topic,
                              ack_topic=d.ack_topic, status_topic=d.status_topic, meter_epoch=d.meter_epoch,
                              token_file=str(d.token_file)) for d in config.devices])


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(mode='development'),
    lambda d: d['cloud'].update(base_url='https://dormenergy.onrender.com'),
    lambda d: d['mqtt'].update(host='192.168.1.20'),
    lambda d: d['devices'][1].update(unit_id=1),
    lambda d: d['devices'][0].update(topic='dormenergy/#'),
    lambda d: d['cloud'].update(base_url='http://user:pass@127.0.0.1'),
    lambda d: d.update(mode='real'),
])
def test_configuration_security(config, tmp_path, mutation):
    data = configuration_dict(config); mutation(data)
    path = tmp_path/'config.json'; path.write_text(json.dumps(data))
    with pytest.raises(ConfigurationError): load(path)


def test_valid_configuration_and_secret_permissions(config, tmp_path):
    path = tmp_path/'config.json'; path.write_text(json.dumps(configuration_dict(config)))
    assert load(path).mode == 'isolated_test'
    if __import__('os').name != 'nt':
        config.mqtt_password_file.chmod(0o644)
        with pytest.raises(ConfigurationError): secret(config.mqtt_password_file)


def test_backend_identifier_length_contract_and_header_safety(config, tmp_path):
    data = configuration_dict(config)
    data['devices'][0]['device_id'] = 'D'*80
    path = tmp_path/'config.json'; path.write_text(json.dumps(data))
    assert load(path).devices[0].device_id == 'D'*80
    data['devices'][0]['device_id'] = 'device\r\ninjected'
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigurationError): load(path)


@pytest.mark.parametrize('raw', [b'{"accepted":true}', b'[]', b'{' , b'['*4096+b']'*4096, b'x'*65537])
def test_malformed_cloud_response_is_retained_for_retry(config, journal, raw):
    class Transport:
        def open(self, request, timeout):
            class Reply:
                code = 201
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(self, limit): return raw
            return Reply()
    cloud = Cloud(config); cloud.opener = Transport()
    journal.enqueue(item(config))
    Synchronizer(config, journal, Clock('isolated_test'), cloud).step()
    assert journal.health()['queue_states'] == {'pending': 1}


def test_partial_cloud_body_is_retryable_and_does_not_crash(config, journal):
    import http.client
    class Reply:
        code = 201
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): raise http.client.IncompleteRead(b'partial', 10)
    cloud = Cloud(config)
    cloud.opener = SimpleNamespace(open=lambda *args, **kwargs: Reply())
    journal.enqueue(item(config))
    Synchronizer(config, journal, Clock('isolated_test'), cloud).step()
    assert journal.health()['queue_states'] == {'pending': 1}


def test_timestamp_timezone_overflow_is_validation_error(config):
    with pytest.raises(InvalidMeasurement):
        item(config, timestamp='0001-01-01T00:00:00+14:00')


def test_callback_failure_stops_service_without_secret_logs(config, journal, caplog):
    gateway = Gateway(config, journal=journal)
    def fail(*args): raise RuntimeError('private-test-sentinel-must-not-appear')
    gateway.guarded(fail)()
    assert gateway.failed and gateway.stop.is_set()
    assert 'private-test-sentinel' not in caplog.text
    assert 'mqtt_callback_failed_journal_retained' in caplog.text


def test_uploader_failure_exits_for_systemd_restart(config, journal, caplog):
    gateway = Gateway(config, journal=journal)
    class Client:
        def connect_async(self, *args, **kwargs): pass
        def loop_start(self): pass
        def is_connected(self): return True
        def disconnect(self): pass
        def loop_stop(self): pass
    def fail(): raise RuntimeError('private-worker-sentinel')
    gateway.client = Client(); gateway.sync.step = fail
    with pytest.raises(RuntimeError, match='worker failed'):
        gateway.run()
    assert 'private-worker-sentinel' not in caplog.text
    # run closed its journal; keep the fixture finalizer idempotent.
    journal.close = lambda: None
    reopened = Journal(config)
    assert reopened.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert reopened.health()['service_alive'] is False
    reopened.close()
