"""MQTT QoS 1 subscriber with an application ACK issued after SQLite commit."""
from contextlib import contextmanager
import json
import logging
import os
import sqlite3
import ssl
import threading
import time

import paho.mqtt.client as mqtt

from .clock import Clock
from .cloud import Synchronizer
from .config import Config, ConfigurationError, secret
from .queue import Journal, StorageUnavailable
from .validation import InvalidMeasurement, validate

LOG = logging.getLogger('dormenergy.gateway')


class JsonLog(logging.Formatter):
    def format(self, record):
        # Only fixed event/category strings and configured aliases are passed here.
        return json.dumps(dict(time=time.time(), level=record.levelname, event=record.getMessage()))


@contextmanager
def instance_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(str(path)+'.service-lock', 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        raise ConfigurationError('Another gateway instance owns this journal.') from None
    try:
        yield
    finally:
        handle.close()


def mqtt_client(config: Config, client_id: str | None = None, username: str | None = None, password: str | None = None):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id or config.mqtt_client_id,
                         clean_session=False, protocol=mqtt.MQTTv311, manual_ack=True)
    client.username_pw_set(username or config.mqtt_username, password or secret(config.mqtt_password_file))
    if config.mqtt_ca_file:
        client.tls_set_context(ssl.create_default_context(cafile=str(config.mqtt_ca_file)))
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.max_inflight_messages_set(20)
    client.max_queued_messages_set(100)
    return client


class Gateway:
    def __init__(self, config: Config, journal: Journal | None = None, clock=None, cloud=None):
        self.config = config
        self.journal = journal or Journal(config)
        self.clock = clock or Clock(config.mode, config.ntp_grace_seconds)
        self.sync = Synchronizer(config, self.journal, self.clock, cloud)
        self.client = mqtt_client(config)
        self.client.on_connect = self.guarded(self.on_connect)
        self.client.on_disconnect = self.guarded(self.on_disconnect)
        self.client.on_message = self.guarded(self.on_message)
        self.client.on_subscribe = self.guarded(self.on_subscribe)
        self.topics = {d.topic: d for d in config.devices}
        self.status_topics = {d.status_topic: d for d in config.devices}
        self.stop = threading.Event()
        self.failed = False

    def guarded(self, callback):
        def wrapped(*args):
            try:
                callback(*args)
            except Exception:
                LOG.error('mqtt_callback_failed_journal_retained')
                self.failed = True
                self.stop.set()
        return wrapped

    def on_subscribe(self, client, userdata, mid, reasons, properties):
        if any(reason.is_failure for reason in reasons):
            LOG.error('broker_subscription_not_authorized')
            self.failed = True
            self.stop.set()

    def on_connect(self, client, userdata, flags, reason, properties):
        connected = not reason.is_failure
        self.journal.metadata('broker', dict(connected=connected, observed_at=time.time()))
        if connected:
            client.subscribe([(topic, 1) for topic in [*self.topics, *self.status_topics]])
        LOG.info('broker_connected' if connected else 'broker_authentication_or_connection_rejected')

    def on_disconnect(self, client, userdata, flags, reason, properties):
        self.journal.metadata('broker', dict(connected=False, observed_at=time.time()))
        LOG.warning('broker_disconnected')

    def on_message(self, client, userdata, message):
        device = self.topics.get(message.topic) or self.status_topics.get(message.topic)
        if device is None:
            # Do not echo an untrusted topic or body; broker ACL is the primary identity boundary.
            LOG.warning('unmapped_topic_rejected')
            client.ack(message.mid, message.qos)
            return
        try:
            if message.qos != 1:
                raise InvalidMeasurement('qos_one_required')
            if message.topic in self.status_topics:
                if len(message.payload) > 1024:
                    raise InvalidMeasurement('status_invalid')
                state = json.loads(message.payload)
                if (not isinstance(state, dict) or state.get('device_id') != device.device_id or type(state.get('unit_id')) is not int or state.get('unit_id') != device.unit_id or
                    type(state.get('online')) is not bool):
                    raise InvalidMeasurement('status_identity_invalid')
                self.journal.connection(device.alias, state['online'])
                client.ack(message.mid, 1)
                return
            trusted = self.clock.state()
            item = validate(message.payload, device, self.config, time.time() if trusted.trusted else None)
            status = self.journal.enqueue(item)
            # MQTT PUBACK alone is not a durability promise to the publisher.
            # ESP32 retains its original frame until this application-level ACK arrives.
            client.publish(device.ack_topic, json.dumps({'event_id': item.payload['event_id'], 'status': status}), qos=1, retain=False)
            client.ack(message.mid, 1)
            LOG.info('measurement_committed device=%s', device.alias)
        except (InvalidMeasurement, ValueError, UnicodeError) as error:
            category = error.category if isinstance(error, InvalidMeasurement) else 'invalid_status_json'
            try:
                self.journal.reject(device.alias, category, message.payload)
                if len(message.payload) <= 8192:
                    try:
                        rejected = json.loads(message.payload)
                        event_id = rejected.get('event_id') if isinstance(rejected, dict) else None
                        if isinstance(event_id, str) and 1 <= len(event_id) <= 64:
                            client.publish(device.ack_topic, json.dumps({'event_id': event_id, 'status': 'rejected', 'reason': category}), qos=1)
                    except (ValueError, UnicodeError, RecursionError):
                        pass
                client.ack(message.mid, message.qos)
            except (sqlite3.Error, OSError):
                LOG.error('storage_unavailable_no_ack'); client.disconnect()
            LOG.warning('measurement_rejected device=%s category=%s', device.alias, category)
        except (StorageUnavailable, sqlite3.Error, OSError):
            LOG.error('storage_unavailable_no_ack')
            # Broker session and ESP32 journal retain the frame. Reconnect resubmits unacked QoS 1.
            client.disconnect()

    def uploader(self):
        last_probe = last_purge = last_heartbeat = 0.0
        while not self.stop.is_set():
            try:
                work = self.sync.step()
                now = time.monotonic()
                if now-last_heartbeat > 15:
                    self.journal.metadata('runtime', {'heartbeat': time.time()})
                    state = self.clock.state()
                    self.journal.metadata('clock', dict(trusted=state.trusted, category=state.category))
                    last_heartbeat = now
                if now-last_probe > 60:
                    self.sync.probe(); last_probe = now
                if now-last_purge > 3600:
                    self.journal.purge(); last_purge = now
            except Exception:
                # Preserve the row and fail the service; systemd restarts under the single-instance lock.
                LOG.error('synchronizer_failed_journal_retained')
                self.failed = True
                self.stop.set()
                return
            self.stop.wait(.1 if work else 1)

    def run(self):
        self.journal.recover()
        self.client.connect_async(self.config.mqtt_host, self.config.mqtt_port, keepalive=30)
        self.client.loop_start()
        worker = threading.Thread(target=self.uploader, name='cloud-uploader')
        worker.start()
        try:
            while not self.stop.wait(1):
                if not self.client.is_connected():
                    # Handles explicit disconnect on disk failure, not just socket outages.
                    try:
                        self.client.reconnect()
                    except OSError:
                        pass
        finally:
            self.stop.set()
            worker.join(timeout=self.config.timeout*2+5)
            self.client.disconnect(); self.client.loop_stop()
            if worker.is_alive():
                raise RuntimeError('Uploader did not stop before shutdown deadline.')
            try:
                self.journal.metadata('broker', dict(connected=False, observed_at=time.time()))
                self.journal.metadata('runtime', dict(heartbeat=0, stopped=True))
            finally:
                self.journal.close()
        if self.failed:
            raise RuntimeError('Gateway worker failed; the durable journal was retained.')
