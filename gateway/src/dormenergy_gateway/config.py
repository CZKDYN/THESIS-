"""Explicit deployment modes and file-based secrets, independent of the web .env."""
from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


def loopback(host: str) -> bool:
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def secret(path: Path) -> str:
    try:
        if os.name != 'nt' and path.stat().st_mode & 0o077:
            raise ConfigurationError('Secret files must have mode 0600 or stricter.')
        value = path.read_text(encoding='utf-8').strip()
    except OSError:
        raise ConfigurationError('A required secret file cannot be read.') from None
    if not value or len(value) > 1024 or '\n' in value or '\r' in value:
        raise ConfigurationError('A secret file has an invalid value.')
    return value


@dataclass(frozen=True)
class Device:
    alias: str
    device_id: str
    unit_id: int
    topic: str
    ack_topic: str
    status_topic: str
    meter_epoch: str
    token_file: Path | None


@dataclass(frozen=True)
class Config:
    mode: str
    database: Path
    devices: tuple[Device, ...]
    mqtt_host: str
    mqtt_port: int
    mqtt_client_id: str
    mqtt_username: str
    mqtt_password_file: Path
    mqtt_ca_file: Path | None
    cloud_enabled: bool
    cloud_url: str
    cloud_ca_file: Path | None
    timeout: float
    max_attempts: int
    max_pending: int
    max_bytes: int
    min_free_bytes: int
    retention_days: int
    stale_seconds: int
    ntp_grace_seconds: int

    @property
    def profile(self) -> str:
        # Token rotation is allowed; identity/mode changes cannot retarget old data.
        identities = [(d.alias, d.device_id, d.unit_id, d.topic) for d in self.devices]
        return sha256(json.dumps([self.mode, self.cloud_url, identities], sort_keys=True).encode()).hexdigest()


def load(path: str | Path) -> Config:
    path = Path(path).resolve()
    def duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ConfigurationError('Configuration has duplicate keys.')
            result[key] = value
        return result
    try:
        data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=duplicate_keys)
        mode = data.get('mode', 'development')
        if mode not in ('development', 'isolated_test', 'real'):
            raise ConfigurationError('Select development, isolated_test, or real mode.')
        def file(value):
            return (path.parent / value).resolve() if value else None
        mqtt, cloud, storage = data['mqtt'], data.get('cloud', {}), data.get('storage', {})
        host, ca = mqtt['host'], file(mqtt.get('ca_file'))
        if not ca and (mode == 'real' or not loopback(host)):
            raise ConfigurationError('Non-loopback MQTT and real mode require verified TLS.')
        enabled, url = cloud.get('enabled', False), cloud.get('base_url', '').rstrip('/')
        if not isinstance(enabled, bool):
            raise ConfigurationError('cloud.enabled must be a boolean.')
        parsed = urlsplit(url)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ConfigurationError('Cloud port is out of range.')
        if url and (parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path or not parsed.hostname):
            raise ConfigurationError('Use a canonical cloud origin without credentials or a path.')
        if mode == 'development' and enabled:
            raise ConfigurationError('Development mode cannot transmit to a cloud backend.')
        if enabled and mode == 'isolated_test' and not loopback(parsed.hostname or ''):
            raise ConfigurationError('Isolated tests require an explicit loopback backend.')
        if enabled and not (parsed.scheme == 'https' or (mode == 'isolated_test' and parsed.scheme == 'http')):
            raise ConfigurationError('Cloud transmission requires verified HTTPS.')
        devices = tuple(Device(alias=d['alias'], device_id=d['device_id'], unit_id=d['unit_id'],
                               topic=d['topic'], ack_topic=d['ack_topic'], status_topic=d['status_topic'],
                               meter_epoch=d['meter_epoch'], token_file=file(d.get('token_file')))
                        for d in data['devices'])
        if not devices or len(devices) > 100:
            raise ConfigurationError('Configure between one and 100 independently mapped devices.')
        for field in ('alias', 'device_id', 'unit_id', 'topic', 'ack_topic', 'status_topic'):
            if len({getattr(d, field) for d in devices}) != len(devices):
                raise ConfigurationError('Device identities, units, and topics must be unique.')
        topics = [getattr(d, field) for d in devices for field in ('topic', 'ack_topic', 'status_topic')]
        if len(set(topics)) != len(topics) or any(not t or '+' in t or '#' in t for t in topics):
            raise ConfigurationError('Use distinct exact MQTT topics; wildcards are prohibited.')
        for device in devices:
            if not isinstance(device.unit_id, int) or isinstance(device.unit_id, bool) or device.unit_id < 1:
                raise ConfigurationError('Use registered positive unit IDs.')
            if not all(isinstance(v, str) and 1 <= len(v) <= maximum for v, maximum in ((device.alias, 64), (device.device_id, 80), (device.meter_epoch, 64))):
                raise ConfigurationError('Alias/epoch must be 1–64 characters; registered device identity 1–80.')
            if any(char in device.device_id for char in ('\r', '\n', '\x00')):
                raise ConfigurationError('Device identities cannot contain header control characters.')
            if mode == 'real' and device.device_id.startswith('DEV-'):
                raise ConfigurationError('Development identities cannot be used in real mode.')
            if enabled and not device.token_file:
                raise ConfigurationError('Each uploading device requires its own token file.')
        result = Config(mode, file(data['database']), devices, host, int(mqtt.get('port', 8883 if ca else 1883)),
                        mqtt['client_id'], mqtt['username'], file(mqtt['password_file']), ca,
                        enabled, url, file(cloud.get('ca_file')), float(cloud.get('timeout_seconds', 10)),
                        int(cloud.get('max_attempts', 8)), int(storage.get('max_pending', 100000)),
                        int(storage.get('max_bytes', 536870912)), int(storage.get('min_free_bytes', 134217728)),
                        int(storage.get('retention_days', 7)), int(data.get('stale_seconds', 90)),
                        int(data.get('ntp_grace_seconds', 86400)))
        if not result.database or not result.mqtt_client_id or not result.mqtt_username:
            raise ConfigurationError('Database, stable MQTT client ID, and username are required.')
        if not 1 <= result.mqtt_port <= 65535 or not 1 <= result.timeout <= 30 or not 1 <= result.max_attempts <= 20:
            raise ConfigurationError('Port, timeout, or retry limit is out of range.')
        if min(result.max_pending, result.max_bytes, result.retention_days, result.stale_seconds) < 1 or result.min_free_bytes < 0:
            raise ConfigurationError('Storage and monitoring limits must be positive.')
        if not 0 <= result.ntp_grace_seconds <= 86400:
            raise ConfigurationError('NTP holdover must be between zero and 86400 seconds.')
        secret(result.mqtt_password_file)
        if enabled:
            for device in devices:
                secret(device.token_file)
        for certificate in (result.mqtt_ca_file, result.cloud_ca_file):
            if certificate and not certificate.is_file():
                raise ConfigurationError('A configured CA certificate cannot be read.')
        return result
    except ConfigurationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ConfigurationError('Invalid configuration structure or file; consult the template.') from None
