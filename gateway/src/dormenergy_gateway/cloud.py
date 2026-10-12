"""Credential-scoped HTTPS with no redirects and exact event reconciliation."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import http.client
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from .clock import Clock
from . import __version__
from .config import Config, Device, loopback, secret
from .queue import Journal
from .validation import utc


@dataclass(frozen=True)
class Response:
    status: int
    body: dict


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class Cloud:
    def __init__(self, config: Config):
        self.config = config
        context = ssl.create_default_context(cafile=str(config.cloud_ca_file) if config.cloud_ca_file else None)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                 urllib.request.HTTPSHandler(context=context))

    def request(self, method: str, path: str, device: Device | None = None, payload=None) -> Response:
        if not self.config.cloud_enabled or self.config.mode == 'development':
            raise RuntimeError('Cloud transmission is disabled.')
        host = urllib.parse.urlsplit(self.config.cloud_url).hostname or ''
        if self.config.mode == 'isolated_test' and not loopback(host):
            raise RuntimeError('A test backend must remain loopback.')
        if self.config.mode == 'real' and payload and payload.get('gateway_fixture'):
            raise RuntimeError('Fixture transmission is forbidden in real mode.')
        headers = {'Content-Type': 'application/json', 'User-Agent': 'DormEnergy-Gateway/'+__version__}
        if device:
            headers.update({'X-Device-ID': device.device_id, 'Authorization': 'Bearer '+secret(device.token_file)})
        request = urllib.request.Request(self.config.cloud_url+path, method=method, headers=headers,
                                         data=json.dumps(payload).encode() if payload is not None else None)
        try:
            response = self.opener.open(request, timeout=self.config.timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (OSError, TimeoutError, urllib.error.URLError, http.client.HTTPException):
            return Response(0, {})
        with response:
            try:
                raw = response.read(65537)
                body = json.loads(raw) if len(raw) <= 65536 else {}
                if not isinstance(body, dict):
                    body = {}
            except (OSError, TimeoutError, http.client.HTTPException):
                return Response(0, {})
            except (ValueError, UnicodeError, RecursionError):
                body = {}
            return Response(response.code, body)


def receipt_matches(payload: dict, receipt: dict) -> bool:
    stored = receipt.get('reading')
    if receipt.get('accepted') is not True or not isinstance(stored, dict):
        return False
    if type(stored.get('unit_id')) is not int:
        return False
    try:
        for field in ('device_id', 'unit_id', 'event_id', 'meter_epoch', 'connection_status'):
            if stored[field] != payload[field]:
                return False
        if utc(stored['timestamp']) != utc(payload['timestamp']):
            return False
        return all(Decimal(str(stored[field])) == Decimal(str(payload[field]))
                   for field in ('voltage', 'current', 'power_w', 'energy_kwh'))
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return False


class Synchronizer:
    def __init__(self, config: Config, journal: Journal, clock: Clock, cloud=None):
        self.config, self.journal, self.clock = config, journal, clock
        self.cloud = cloud or Cloud(config)
        self.devices = {d.alias: d for d in config.devices}

    def step(self, now: float | None = None) -> bool:
        if not self.config.cloud_enabled or self.config.mode == 'development':
            return False
        state = self.clock.state()
        self.journal.metadata('clock', dict(trusted=state.trusted, category=state.category))
        if not state.trusted:
            return False
        now = time.time() if now is None else now
        row = self.journal.claim(now)
        if not row:
            return False
        payload, device = json.loads(row['payload']), self.devices[row['alias']]
        if self.config.mode == 'real' and (row['origin'] != 'hardware' or payload.get('gateway_fixture')):
            self.journal.outcome(row['id'], 'blocked_fixture', 'fixture_forbidden')
            return True
        if utc(payload['timestamp']).timestamp() > time.time()+120:
            self.journal.outcome(row['id'], 'blocked_clock', 'timestamp_future')
            return True
        response = self.cloud.request('POST', '/api/iot/readings', device, payload)
        code = response.body.get('error_code')
        category = None
        if response.status == 201 and response.body.get('accepted') is True and type(response.body.get('id')) is int and response.body['id'] > 0:
            status = 'sent'
        elif response.status == 409:
            # Reconcile every conflict; textual messages are never proof of acceptance.
            receipt = self.cloud.request('GET', '/api/iot/readings/receipt?'+urllib.parse.urlencode({'event_id': payload['event_id']}), device)
            if receipt.status == 200 and receipt_matches(payload, receipt.body):
                status, category = 'sent', 'confirmed_duplicate'
            elif receipt.status in (0, 408, 429) or receipt.status >= 500:
                status, category = 'pending', 'receipt_temporary'
            elif receipt.status in (401, 403):
                status, category = 'blocked_auth', 'receipt_authentication_failed'
            else:
                status, category = 'blocked_conflict', 'unresolved_event_conflict'
        elif response.status in (401, 403):
            status, category = 'blocked_auth', 'device_authentication_or_mapping'
        elif response.status == 422:
            status, category = ('blocked_tariff', 'tariff_unavailable') if code == 'tariff_unavailable' else ('blocked_validation', 'measurement_rejected')
        elif response.status in (0, 408, 429) or response.status >= 500 or response.status == 201:
            status, category = 'pending', 'network_or_cloud_temporary'
        else:
            status, category = 'blocked_validation', 'unexpected_cloud_response'
        retry = 0
        if status == 'pending':
            if row['attempts'] >= self.config.max_attempts:
                status, category = 'blocked_retry', 'retry_limit'
            else:
                delay = min(300, 2**min(row['attempts'], 9))
                retry = now+delay+random.uniform(0, delay*.2)
        self.journal.outcome(row['id'], status, category, retry)
        self.journal.metadata('cloud', dict(status='available' if response.status and response.status < 500 else 'unavailable',
                                          transport='reachable' if response.status else 'request_failed', observed_at=time.time()))
        return True

    def probe(self):
        if not self.config.cloud_enabled:
            return
        response = self.cloud.request('GET', '/health')
        self.journal.metadata('cloud', dict(status='available' if response.status == 200 and response.body.get('status') == 'ok' else 'unavailable',
                                          transport='reachable' if response.status else 'request_failed', observed_at=time.time()))
