"""Durable local measurement journal; only confirmed synchronized rows are purged."""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time

from .config import Config, ConfigurationError
from .validation import InvalidMeasurement, Measurement


class StorageUnavailable(RuntimeError):
    pass


class Journal:
    def __init__(self, config: Config):
        self.config = config
        config.database.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(config.database, timeout=10, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if os.name != 'nt':
            config.database.chmod(0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA busy_timeout=10000')
        if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise StorageUnavailable('Local database integrity check failed; preserve it for recovery.')
        self.db.executescript('''
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS measurements(
 id INTEGER PRIMARY KEY, alias TEXT NOT NULL, event_id TEXT NOT NULL, timestamp REAL NOT NULL,
 epoch TEXT NOT NULL, energy TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, origin TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL DEFAULT 0, error TEXT, received_at REAL NOT NULL, synced_at REAL,
 UNIQUE(alias,event_id));
CREATE INDEX IF NOT EXISTS pending_order ON measurements(alias,timestamp,id);
CREATE INDEX IF NOT EXISTS unsent_order ON measurements(alias,timestamp,id) WHERE status!='sent';
CREATE TABLE IF NOT EXISTS receipts(alias TEXT NOT NULL,event_id TEXT NOT NULL,digest TEXT NOT NULL,
 timestamp REAL NOT NULL,epoch TEXT NOT NULL,energy TEXT NOT NULL,synced_at REAL NOT NULL,
 PRIMARY KEY(alias,event_id));
CREATE INDEX IF NOT EXISTS receipt_order ON receipts(alias,timestamp);
CREATE TABLE IF NOT EXISTS reset_approvals(alias TEXT NOT NULL,epoch TEXT NOT NULL,approved_at REAL NOT NULL,
 PRIMARY KEY(alias,epoch));
CREATE TABLE IF NOT EXISTS activity(alias TEXT PRIMARY KEY,last_received REAL,last_measurement REAL,
 connection TEXT NOT NULL DEFAULT 'unknown',status_at REAL);
CREATE TABLE IF NOT EXISTS rejections(id INTEGER PRIMARY KEY,alias TEXT,category TEXT NOT NULL,
 digest TEXT NOT NULL,received_at REAL NOT NULL);
''')
        try:
            with self.transaction():
                existing = self.db.execute("SELECT value FROM metadata WHERE key='profile'").fetchone()
                if existing and existing[0] != config.profile:
                    raise ConfigurationError('This journal belongs to a different mode, destination or device mapping. Keep it; use a separate database.')
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES('profile',?)", (config.profile,))
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                self.db.execute('BEGIN IMMEDIATE')
                yield
                self.db.execute('COMMIT')
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                raise

    def recover(self):
        # Called only by the single service owner after acquiring the instance lock.
        with self.transaction():
            self.db.execute("UPDATE measurements SET status='pending',next_attempt=0,error='restart_recovery' WHERE status='sending'")

    def approve_reset(self, alias: str, epoch: str):
        if alias not in {d.alias for d in self.config.devices} or not epoch or len(epoch) > 64:
            raise ConfigurationError('Select a configured device alias and a valid physical meter epoch.')
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO reset_approvals VALUES(?,?,?)', (alias, epoch, time.time()))

    def enqueue(self, measurement: Measurement) -> str:
        alias, body = measurement.device.alias, measurement.payload
        try:
            with self.transaction():
                existing = self.db.execute('''SELECT digest FROM measurements WHERE alias=? AND event_id=?
 UNION ALL SELECT digest FROM receipts WHERE alias=? AND event_id=? LIMIT 1''',
                                           (alias, body['event_id'], alias, body['event_id'])).fetchone()
                if existing:
                    if existing['digest'] != measurement.digest:
                        raise InvalidMeasurement('event_content_conflict')
                    return 'already_stored'
                pending = self.db.execute("SELECT count(*) FROM measurements WHERE status!='sent'").fetchone()[0]
                total_bytes = sum(p.stat().st_size for p in (
                    self.config.database, Path(str(self.config.database)+'-wal')) if p.exists())
                if (pending >= self.config.max_pending or total_bytes >= self.config.max_bytes or
                    shutil.disk_usage(self.config.database.parent).free < self.config.min_free_bytes):
                    raise StorageUnavailable('Local storage limit reached; no reading acknowledgment was sent.')
                previous, following = self.neighbors(alias, measurement.timestamp)
                approved = self.db.execute('SELECT 1 FROM reset_approvals WHERE alias=? AND epoch=?', (alias, body['meter_epoch'])).fetchone()
                initial_epoch = measurement.device.meter_epoch
                if body['meter_epoch'] != initial_epoch and not approved:
                    raise InvalidMeasurement('meter_reset_not_approved')
                if previous and previous['epoch'] != body['meter_epoch'] and body.get('reset_confirmed') is not True:
                    raise InvalidMeasurement('reset_confirmation_required')
                if previous and previous['epoch'] != body['meter_epoch']:
                    reused = any(self.db.execute('SELECT 1 FROM '+table+' WHERE alias=? AND epoch=? AND timestamp<? LIMIT 1',
                                 (alias, body['meter_epoch'], previous['timestamp'])).fetchone() for table in ('measurements', 'receipts'))
                    if reused:
                        raise InvalidMeasurement('meter_epoch_reused')
                if previous and previous['timestamp'] == measurement.timestamp:
                    raise InvalidMeasurement('timestamp_collision')
                for neighbor, decreasing in ((previous, True), (following, False)):
                    if neighbor and neighbor['epoch'] == body['meter_epoch']:
                        other, current = Decimal(neighbor['energy']), Decimal(body['energy_kwh'])
                        if (decreasing and current < other) or (not decreasing and current > other):
                            raise InvalidMeasurement('counter_decreased')
                self.db.execute('''INSERT INTO measurements(alias,event_id,timestamp,epoch,energy,payload,digest,origin,received_at)
 VALUES(?,?,?,?,?,?,?,?,?)''', (alias, body['event_id'], measurement.timestamp, body['meter_epoch'], body['energy_kwh'],
                              measurement.canonical, measurement.digest, measurement.origin, time.time()))
                self.db.execute('''INSERT INTO activity(alias,last_received,last_measurement) VALUES(?,?,?)
 ON CONFLICT(alias) DO UPDATE SET last_received=excluded.last_received,
 last_measurement=max(coalesce(activity.last_measurement,0),excluded.last_measurement)''',
                                (alias, time.time(), measurement.timestamp))
            return 'stored'
        except sqlite3.Error:
            raise StorageUnavailable('SQLite could not commit the reading; upstream must retain it.') from None

    def reject(self, alias: str | None, category: str, raw: bytes):
        with self.transaction():
            self.db.execute('INSERT INTO rejections(alias,category,digest,received_at) VALUES(?,?,?,?)',
                            (alias, category, hashlib.sha256(raw).hexdigest(), time.time()))
            self.db.execute('DELETE FROM rejections WHERE id NOT IN (SELECT id FROM rejections ORDER BY id DESC LIMIT 1000)')

    def neighbors(self, alias: str, timestamp: float):
        # Two index seeks per table, rather than sorting a union of the entire room history.
        before, after = [], []
        for table in ('measurements', 'receipts'):
            previous = self.db.execute('SELECT timestamp,epoch,energy FROM '+table+' WHERE alias=? AND timestamp<=? ORDER BY timestamp DESC LIMIT 1', (alias, timestamp)).fetchone()
            following = self.db.execute('SELECT timestamp,epoch,energy FROM '+table+' WHERE alias=? AND timestamp>? ORDER BY timestamp LIMIT 1', (alias, timestamp)).fetchone()
            if previous: before.append(previous)
            if following: after.append(following)
        return (max(before, key=lambda row: row['timestamp']) if before else None,
                min(after, key=lambda row: row['timestamp']) if after else None)

    def connection(self, alias: str, online: bool):
        with self.transaction():
            self.db.execute('''INSERT INTO activity(alias,connection,status_at) VALUES(?,?,?)
 ON CONFLICT(alias) DO UPDATE SET connection=excluded.connection,status_at=excluded.status_at''',
                            (alias, 'online' if online else 'offline', time.time()))

    def claim(self, now: float | None = None):
        now = time.time() if now is None else now
        with self.transaction():
            heads = [self.db.execute("SELECT * FROM measurements WHERE alias=? AND status!='sent' ORDER BY timestamp,id LIMIT 1", (device.alias,)).fetchone() for device in self.config.devices]
            for row in sorted((row for row in heads if row), key=lambda row: (row['next_attempt'], row['timestamp'], row['id'])):
                if row['status'] != 'pending' or row['next_attempt'] > now:
                    continue
                if row['attempts'] >= self.config.max_attempts:
                    self.db.execute("UPDATE measurements SET status='blocked_retry',error='retry_limit' WHERE id=?", (row['id'],))
                    continue
                self.db.execute("UPDATE measurements SET status='sending',attempts=attempts+1 WHERE id=?", (row['id'],))
                return dict(self.db.execute('SELECT * FROM measurements WHERE id=?', (row['id'],)).fetchone())
        return None

    def outcome(self, row_id: int, status: str, category: str | None = None, next_attempt: float = 0):
        if status not in ('sent', 'pending', 'blocked_auth', 'blocked_conflict', 'blocked_tariff',
                          'blocked_validation', 'blocked_retry', 'blocked_fixture', 'blocked_clock'):
            raise ValueError('Invalid journal state')
        with self.transaction():
            self.db.execute('UPDATE measurements SET status=?,error=?,next_attempt=?,synced_at=? WHERE id=?',
                            (status, category, next_attempt, time.time() if status == 'sent' else None, row_id))
            if status == 'sent':
                self.db.execute('''INSERT OR IGNORE INTO receipts
 SELECT alias,event_id,digest,timestamp,epoch,energy,synced_at FROM measurements WHERE id=?''', (row_id,))

    def resume(self, alias: str):
        if alias not in {d.alias for d in self.config.devices}:
            raise ConfigurationError('Unknown configured device alias.')
        with self.transaction():
            self.db.execute("UPDATE measurements SET status='pending',attempts=0,next_attempt=0,error='operator_resume' WHERE alias=? AND status LIKE 'blocked_%'", (alias,))

    def metadata(self, key: str, value=None):
        with self.transaction():
            if value is not None:
                self.db.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                (key, json.dumps(value)))
            row = self.db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def health(self):
        with self.lock:
            states = {row[0]: row[1] for row in self.db.execute('SELECT status,count(*) FROM measurements GROUP BY status')}
            devices = []
            now = time.time()
            runtime_row = self.db.execute("SELECT value FROM metadata WHERE key='runtime'").fetchone()
            runtime = json.loads(runtime_row[0]) if runtime_row else {}
            service_alive = 0 <= now-runtime.get('heartbeat', 0) <= 45
            broker_row = self.db.execute("SELECT value FROM metadata WHERE key='broker'").fetchone()
            broker = json.loads(broker_row[0]) if broker_row else {}
            broker_connected = service_alive and broker.get('connected') is True
            for device in self.config.devices:
                row = self.db.execute('SELECT * FROM activity WHERE alias=?', (device.alias,)).fetchone()
                fresh = bool(row and row['last_measurement'] and 0 <= now-row['last_measurement'] < self.config.stale_seconds)
                reported = row['connection'] if row else 'unknown'
                status_fresh = bool(row and row['status_at'] and 0 <= now-row['status_at'] < self.config.stale_seconds)
                connection = reported if status_fresh else 'unknown'
                latest = self.db.execute('SELECT max(synced_at) FROM receipts WHERE alias=?', (device.alias,)).fetchone()[0]
                head = self.db.execute("SELECT status,error FROM measurements WHERE alias=? AND status!='sent' ORDER BY timestamp,id LIMIT 1", (device.alias,)).fetchone()
                devices.append(dict(alias=device.alias, connection=connection, status_fresh=status_fresh,
                                    reported_connection=reported, measurement_fresh=fresh,
                                    online=bool(broker_connected and fresh and connection == 'online'),
                                    last_measurement=row['last_measurement'] if row else None,
                                    last_received=row['last_received'] if row else None, last_synced=latest,
                                    blocked_reason=head['error'] if head and head['status'].startswith('blocked_') else None))
            return dict(mode=self.config.mode, cloud_enabled=self.config.cloud_enabled, service_alive=service_alive,
                        broker_connected=broker_connected, internet='cloud_path_reachable' if self._cloud_reachable(now) else 'not_established', queue_states=states,
                        unsent=sum(count for status, count in states.items() if status != 'sent'), devices=devices,
                        disk_free_bytes=shutil.disk_usage(self.config.database.parent).free)

    def _cloud_reachable(self, now):
        row = self.db.execute("SELECT value FROM metadata WHERE key='cloud'").fetchone()
        state = json.loads(row[0]) if row else {}
        return state.get('transport') == 'reachable' and 0 <= now-state.get('observed_at', 0) <= 90

    def purge(self):
        with self.transaction():
            self.db.execute("DELETE FROM measurements WHERE status='sent' AND synced_at<?",
                            (time.time()-self.config.retention_days*86400,))

    def backup(self, destination: Path):
        if destination.resolve() == self.config.database.resolve() or destination.exists():
            raise ConfigurationError('Choose a new backup file outside the active database.')
        descriptor = os.open(destination, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        os.close(descriptor)
        with self.lock:
            target = sqlite3.connect(destination)
            try:
                self.db.backup(target)
            finally:
                target.close()

    def close(self):
        with self.lock:
            self.db.close()
