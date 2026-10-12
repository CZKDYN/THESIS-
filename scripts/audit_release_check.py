"""Exercise Render supervision and PostgreSQL backup/restore on disposable local DBs.

Linux only: requires Bash, ps, native pg_dump/pg_restore and two expendable DBs.
No retained database, email provider, external website or credentials are touched.
"""
import argparse
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from dormenergy import create_app
from dormenergy.extensions import db
from dormenergy.models import Account, Device, Rate, Reading, Transaction, Unit, utcnow

ROOT = Path(__file__).resolve().parents[1]


def expendable(raw):
    parsed = make_url(raw)
    host = parsed.query.get('host', parsed.host or '')
    if parsed.get_backend_name() != 'postgresql' or not (parsed.database or '').endswith('_test') or not (host.startswith('/') or host in ('localhost', '127.0.0.1')):
        raise ValueError('Use local disposable PostgreSQL databases ending in _test.')
    return parsed


def application(raw):
    return create_app(dict(TESTING=True, SECRET_KEY=secrets.token_urlsafe(32), SQLALCHEMY_DATABASE_URI=raw,
        SIMULATION_MODE=False, EMAIL_BACKEND='disabled', UTILITY_RATE_LOCKED=False))


def clear(app):
    with app.app_context():
        db.session.remove(); db.drop_all(); db.session.execute(text('DROP TABLE IF EXISTS alembic_version')); db.session.commit(); db.engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True); parser.add_argument('--restore-database', required=True)
    parser.add_argument('--pg-bin', required=True); parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source, destination = expendable(args.database), expendable(args.restore_database)
    if source.database == destination.database: parser.error('Backup source and restore destination must differ.')
    app, restore_app = application(args.database), application(args.restore_database)
    clear(app); clear(restore_app)
    env = dict(os.environ, DATABASE_URL=args.database, SECRET_KEY=secrets.token_urlsafe(32),
        ADMIN_SETUP_TOKEN=secrets.token_urlsafe(32), EMAIL_BACKEND='disabled', UTILITY_RATE_CONFIRMED='false',
        UTILITY_RATE_LOCKED='true', SIMULATION_MODE='true', SESSION_COOKIE_SECURE='true', WEB_CONCURRENCY='1')
    env['PATH'] = str(ROOT/'.venv/bin')+os.pathsep+env['PATH']
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); port = listener.getsockname()[1]
    env['PORT'] = str(port)
    report = dict(software_fixtures_only=True, mode=None, worker_exit_supervised=False, restart=False,
                  schema_drift_check=False, backup_restore=False, real_emails_sent=0)
    parent = None
    with tempfile.TemporaryDirectory(prefix='dormenergy-release-') as temporary:
        root = Path(temporary)
        def start():
            log = open(root/'render-start.log','ab')
            process = subprocess.Popen(['bash','scripts/start-render.sh'],cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)
            log.close()
            deadline = time.monotonic()+20
            while time.monotonic()<deadline:
                if process.poll() is not None: raise AssertionError('Isolated entrypoint exited during startup.')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response: health = json.load(response)
                    assert health['mode']=='hardware'; report['mode']=health['mode']; return process
                except (OSError, urllib.error.URLError): time.sleep(.1)
            raise AssertionError('Isolated entrypoint did not become healthy.')
        try:
            parent = start()
            processes = subprocess.check_output(['ps','-eo','pid,ppid,args'],text=True).splitlines()
            children = [line.split(None,2) for line in processes if len(line.split(None,2))==3 and line.split(None,2)[1]==str(parent.pid)]
            assert sum('gunicorn' in row[2] for row in children)==1
            assert sum('forecast-worker' in row[2] for row in children)==1
            assert sum('email-worker' in row[2] for row in children)==1
            assert not any('simulate' in row[2] for row in children)
            forecast = next(int(row[0]) for row in children if 'forecast-worker' in row[2])
            os.kill(forecast, signal.SIGTERM)
            assert parent.wait(15)!=0; report['worker_exit_supervised']=True
            parent = start(); report['restart']=True
            schema = subprocess.run([str(ROOT/'.venv/bin/python'),'-m','flask','--app','wsgi','db','check'],cwd=ROOT,env=env,capture_output=True)
            assert schema.returncode==0; report['schema_drift_check']=True
            parent.terminate(); parent.wait(15); parent=None
            with app.app_context():
                assert db.session.scalar(select(func.count(Unit.id)))==0
                assert db.session.scalar(select(func.count(Reading.id)))==0
                report['automatic_readings']=0
                unit=Unit(name='Backup fixture',room='Isolated test room',is_demo=False);db.session.add(unit);db.session.flush()
                db.session.add(Account(unit_id=unit.id,balance=Decimal('99')))
                device=Device(identifier='BACKUP-ONLY',unit_id=unit.id,source='hardware');db.session.add(device);db.session.flush()
                rate=Rate(php_per_kwh=Decimal('10'),effective_at=utcnow()-timedelta(days=1));db.session.add(rate);db.session.flush()
                reading=Reading(device_id=device.id,event_id='backup-fixture',timestamp=utcnow(),voltage=230,current=1,power=230,
                    energy=Decimal('1'),delta_kwh=Decimal('.1'),rate_id=rate.id,source='hardware',meter_epoch='test',connection_status='unknown')
                db.session.add(reading);db.session.flush()
                db.session.add(Transaction(unit_id=unit.id,kind='consumption',amount=Decimal('-1'),balance_after=Decimal('99'),reading_id=reading.id,description='Isolated backup fixture'))
                db.session.commit()
                counts={name:db.session.execute(text('SELECT count(*) FROM "'+name+'"')).scalar() for name in db.metadata.tables}
            archive=root/'backup.dump'
            def pg_env(parsed):
                result=dict(os.environ,PGHOST=parsed.query.get('host',parsed.host or ''),PGUSER=parsed.username or '',PGDATABASE=parsed.database)
                if parsed.port: result['PGPORT']=str(parsed.port)
                if parsed.password: result['PGPASSWORD']=parsed.password
                return result
            dumped=subprocess.run([str(Path(args.pg_bin)/'pg_dump'),'-Fc','-f',str(archive)],env=pg_env(source),capture_output=True)
            assert dumped.returncode==0
            restored=subprocess.run([str(Path(args.pg_bin)/'pg_restore'),'--no-owner','--exit-on-error','-d',destination.database,str(archive)],env=pg_env(destination),capture_output=True)
            assert restored.returncode==0
            with restore_app.app_context():
                after={name:db.session.execute(text('SELECT count(*) FROM "'+name+'"')).scalar() for name in db.metadata.tables}
                assert counts==after and db.session.scalar(select(Account.balance))==Decimal('99')
                assert db.session.scalar(select(Transaction.amount))==Decimal('-1')
                assert db.session.execute(text('SELECT version_num FROM alembic_version')).scalar()=='d21ae48f397c'
            report.update(backup_restore=True,preserved_table_counts=counts,preserved_fixture_balance='99.00',schema_head='d21ae48f397c')
        finally:
            if parent and parent.poll() is None:
                parent.terminate()
                try: parent.wait(15)
                except subprocess.TimeoutExpired: os.killpg(parent.pid,signal.SIGKILL);parent.wait(5)
            clear(app); clear(restore_app)
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__': main()
