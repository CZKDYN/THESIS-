"""Headless commands: run, inspect, safely resume, back up, and provision secrets."""
import argparse
from datetime import datetime, timezone
import getpass
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
from zoneinfo import ZoneInfo

from .config import ConfigurationError, load
from .queue import Journal
from .service import Gateway, JsonLog, instance_lock


def main():
    parser = argparse.ArgumentParser(prog='dormenergy-gateway')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('run', 'health', 'resume', 'approve-reset', 'backup', 'simulate'):
        command = commands.add_parser(name)
        command.add_argument('--config', required=True)
        if name in ('resume', 'approve-reset'):
            command.add_argument('--device', required=True, help='Configured device alias')
        if name == 'approve-reset':
            command.add_argument('--epoch', required=True, help='New physical counter-reset epoch')
        if name == 'backup':
            command.add_argument('--destination', required=True)
        if name == 'simulate':
            command.add_argument('--device', required=True)
            command.add_argument('--username', required=True)
            command.add_argument('--password-file', required=True)
            command.add_argument('--scenario', choices=['normal', 'duplicate', 'delayed', 'out-of-order', 'disconnect', 'reset'], default='normal')
            command.add_argument('--journal', required=True, help='Persistent publisher fixture journal')
    command = commands.add_parser('secret')
    command.add_argument('--destination', required=True)
    command = commands.add_parser('restore-check')
    command.add_argument('--backup', required=True)
    args = parser.parse_args()
    handler = logging.StreamHandler(); handler.setFormatter(JsonLog())
    logger = logging.getLogger('dormenergy.gateway'); logger.setLevel(logging.INFO); logger.addHandler(handler)
    try:
        if args.command == 'secret':
            destination = Path(args.destination)
            if destination.exists():
                raise ConfigurationError('Secret destination already exists; choose a new file for rotation.')
            value = getpass.getpass('Secret value (hidden): ')
            if not value or '\n' in value or '\r' in value:
                raise ConfigurationError('Secret must be a nonempty single line.')
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(destination, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
                target.write(value+'\n')
            print('Secret file created; no value was printed.')
            return
        if args.command == 'restore-check':
            backup = Path(args.backup).resolve()
            with sqlite3.connect(backup.as_uri()+'?mode=ro', uri=True) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ConfigurationError('Backup integrity check failed; do not replace the active journal.')
                count = db.execute("SELECT count(*) FROM measurements WHERE status!='sent'").fetchone()[0]
            print(json.dumps({'integrity': 'ok', 'unsent': count}))
            return
        config = load(args.config)
        if args.command == 'simulate':
            from .development import publish
            publish(config, args)
            return
        if args.command == 'run':
            with instance_lock(config.database):
                gateway = Gateway(config)
                if hasattr(signal, 'SIGTERM'):
                    signal.signal(signal.SIGTERM, lambda *_: gateway.stop.set())
                signal.signal(signal.SIGINT, lambda *_: gateway.stop.set())
                gateway.run()
            return
        journal = Journal(config)
        try:
            if args.command == 'health':
                health = journal.health()
                health.update(broker=journal.metadata('broker'), cloud=journal.metadata('cloud'), clock=journal.metadata('clock'))
                for device in health['devices']:
                    stamp = device['last_measurement']
                    device['measurement_manila'] = datetime.fromtimestamp(stamp, timezone.utc).astimezone(ZoneInfo('Asia/Manila')).isoformat() if stamp else None
                print(json.dumps(health, indent=2))
            elif args.command == 'resume':
                journal.resume(args.device)
                print('Blocked rows retained and queued for a controlled retry; no measurement was changed.')
            elif args.command == 'approve-reset':
                journal.approve_reset(args.device, args.epoch)
                print('New physical meter epoch approved locally; ESP32 must still send reset_confirmed=true.')
            elif args.command == 'backup':
                journal.backup(Path(args.destination))
                print('Consistent SQLite backup created. Credential files require a separate secure backup.')
        finally:
            journal.close()
    except (ConfigurationError, sqlite3.Error, OSError, RuntimeError):
        parser.exit(2, 'Gateway operation failed. Check configuration, journal integrity and the documented recovery steps; secrets were not printed.\n')
