"""Clearly marked mock ESP32 publisher; prohibited with real deployment configuration."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .config import Config, ConfigurationError, loopback, secret
from .service import mqtt_client


def publish(config: Config, args):
    if config.mode == 'real' or not loopback(config.mqtt_host):
        raise ConfigurationError('Mock publishing requires a local development/test MQTT broker.')
    device = next((d for d in config.devices if d.alias == args.device), None)
    if not device:
        raise ConfigurationError('Select a configured development device alias.')
    journal_path = Path(args.journal)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(journal_path)
    journal_path.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS metadata(profile TEXT NOT NULL)')
    binding = config.profile+':'+device.alias
    previous_binding = db.execute('SELECT profile FROM metadata').fetchone()
    if previous_binding and previous_binding[0] != binding:
        db.close()
        raise ConfigurationError('Publisher journal belongs to another fixture device or destination.')
    if not previous_binding:
        db.execute('INSERT INTO metadata VALUES(?)', (binding,))
    db.execute('CREATE TABLE IF NOT EXISTS fixtures(id INTEGER PRIMARY KEY,event_id TEXT UNIQUE,payload TEXT NOT NULL,acked INTEGER DEFAULT 0)')
    existing = db.execute('SELECT payload FROM fixtures WHERE acked=0 ORDER BY id').fetchall()
    if not existing:
        latest = db.execute('SELECT payload FROM fixtures ORDER BY id DESC LIMIT 1').fetchone()
        base = int(float(json.loads(latest[0])['energy_kwh']))+1 if latest else 10
        first_stamp = datetime.now(timezone.utc)-timedelta(seconds=30)
        if latest:
            first_stamp = max(first_stamp, datetime.fromisoformat(json.loads(latest[0])['timestamp'])+timedelta(seconds=1))
        # Fixture counter values exist only in this explicit test publisher, never gateway ingestion.
        for index in range(3):
            epoch = device.meter_epoch if args.scenario != 'reset' else device.meter_epoch+'-reset'
            timestamp = first_stamp+timedelta(seconds=index)
            if args.scenario == 'delayed':
                timestamp -= timedelta(hours=1)
            body = dict(device_id=device.device_id, unit_id=device.unit_id, event_id='fixture-'+uuid.uuid4().hex,
                        meter_epoch=epoch, timestamp=timestamp.isoformat(), voltage='230.000', current='1.0000',
                        power_w='230.000', energy_kwh=str(base+index) if args.scenario != 'reset' else str(index),
                        connection_status='unknown', measurement_origin='development_fixture', clock_synchronized=True)
            if args.scenario == 'reset':
                body['reset_confirmed'] = True
            db.execute('INSERT INTO fixtures(event_id,payload) VALUES(?,?)', (body['event_id'], json.dumps(body)))
        db.commit()
        existing = db.execute('SELECT payload FROM fixtures WHERE acked=0 ORDER BY id').fetchall()
    if args.scenario == 'out-of-order':
        existing = list(reversed(existing))
    client = mqtt_client(config, client_id='fixture-publisher-'+device.alias,
                         username=args.username, password=secret(Path(args.password_file)))
    acknowledged = set()
    pending_ids = {json.loads(row[0])['event_id'] for row in existing}
    subscribed = threading.Event()
    online = dict(device_id=device.device_id, unit_id=device.unit_id, online=True)
    offline = dict(online, online=False)
    client.will_set(device.status_topic, json.dumps(offline), qos=1, retain=True)
    def connected(client, userdata, flags, reason, properties):
        if not reason.is_failure:
            client.subscribe(device.ack_topic, qos=1)
            client.publish(device.status_topic, json.dumps(online), qos=1, retain=True)
    def subscription(client, userdata, mid, reasons, properties):
        if not any(reason.is_failure for reason in reasons):
            subscribed.set()
    client.on_connect, client.on_subscribe = connected, subscription
    def message(client, userdata, packet):
        try:
            data = json.loads(packet.payload)
            if data.get('status') in ('stored', 'already_stored') and data.get('event_id') in pending_ids:
                acknowledged.add(data['event_id'])
            client.ack(packet.mid, packet.qos)
        except (ValueError, KeyError):
            pass
    client.on_message = message
    client.connect(config.mqtt_host, config.mqtt_port, keepalive=30)
    client.loop_start()
    try:
        if not subscribed.wait(5):
            raise RuntimeError('Fixture subscriber was not authorized or connected.')
        for index, row in enumerate(existing):
            payload = row[0]
            client.publish(device.topic, payload, qos=1, retain=False).wait_for_publish(timeout=5)
            if args.scenario == 'duplicate':
                client.publish(device.topic, payload, qos=1, retain=False).wait_for_publish(timeout=5)
            if args.scenario == 'disconnect' and index == 0:
                client.publish(device.status_topic, json.dumps(offline), qos=1, retain=True).wait_for_publish(timeout=5)
                client.disconnect(); client.loop_stop(); subscribed.clear()
                client.connect(config.mqtt_host, config.mqtt_port, keepalive=30); client.loop_start()
                if not subscribed.wait(5):
                    raise RuntimeError('Fixture reconnection failed.')
                client.publish(device.topic, payload, qos=1).wait_for_publish(timeout=5)
            time.sleep(.2)
        deadline = time.monotonic()+10
        while time.monotonic() < deadline and len(acknowledged) < len(existing):
            time.sleep(.1)
        for event_id in acknowledged:
            db.execute('UPDATE fixtures SET acked=1 WHERE event_id=?', (event_id,))
        db.commit()
        print(json.dumps({'fixture_frames': len(existing), 'application_acks': len(acknowledged),
                          'retained_for_retry': db.execute('SELECT count(*) FROM fixtures WHERE acked=0').fetchone()[0],
                          'real_measurements': False}))
    finally:
        if client.is_connected():
            client.publish(device.status_topic, json.dumps(offline), qos=1, retain=True).wait_for_publish(timeout=5)
        client.disconnect(); client.loop_stop(); db.close()
