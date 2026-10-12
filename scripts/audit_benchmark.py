"""Disposable software-only benchmarks; never use retained/production databases."""
import argparse
from datetime import timedelta
from decimal import Decimal
import json
import math
from pathlib import Path
import secrets
import statistics
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import event, insert, select, text
from sqlalchemy.engine import make_url
from werkzeug.security import generate_password_hash
from flask_migrate import upgrade
from dormenergy import create_app
from dormenergy.extensions import db
from dormenergy.models import Account, Device, Rate, Reading, Transaction, Unit, User, utcnow
from dormenergy_gateway.config import Config, Device as GatewayDevice
from dormenergy_gateway.queue import Journal
from dormenergy_gateway.validation import validate


def distribution(samples):
    ordered=sorted(samples)
    return dict(samples=len(samples),median_ms=round(statistics.median(samples)*1000,3),
                p95_ms=round(ordered[math.ceil(len(ordered)*.95)-1]*1000,3),max_ms=round(max(samples)*1000,3))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--queue-rows',type=int,default=5000)
    args=parser.parse_args();parsed=make_url(args.database)
    if parsed.get_backend_name()!='postgresql' or not (parsed.database or '').endswith('_test'):
        parser.error('An expendable PostgreSQL database ending in _test is required.')
    app=create_app(dict(TESTING=True,SECRET_KEY=secrets.token_urlsafe(32),SQLALCHEMY_DATABASE_URI=args.database,
                       SIMULATION_MODE=False,UTILITY_RATE_LOCKED=False,EMAIL_BACKEND='disabled',SESSION_COOKIE_SECURE=False))
    password=secrets.token_urlsafe(24);tokens={i:secrets.token_urlsafe(32) for i in range(1,4)}
    report=dict(software_fixtures_only=True,rooms=3,seed_readings=9999,platform=sys.platform)
    cpu_start=time.process_time()
    with app.app_context():
        db.drop_all();db.session.execute(text('DROP TABLE IF EXISTS alembic_version'));db.session.commit();upgrade(directory='migrations')
        origin=utcnow()-timedelta(minutes=10)
        try:
            owner=User(name='Benchmark owner',email='owner@benchmark.test',role='admin');owner.set_password(password);db.session.add(owner)
            rate=Rate(id=1,php_per_kwh=Decimal('12.5'),effective_at=origin-timedelta(days=365));db.session.add(rate)
            for i in range(1,4):
                unit=Unit(id=i,name=f'Benchmark room {i}',room=f'Floor {i}',is_demo=False);db.session.add(unit);db.session.flush()
                db.session.add(Account(unit_id=i,balance=Decimal('1000')))
                db.session.add(Device(id=i,identifier=f'BENCH-{i}',unit_id=i,source='hardware',token_hash=generate_password_hash(tokens[i]),
                                      last_energy=Decimal('3.332'),last_timestamp=origin,last_seen=utcnow(),meter_epoch='bench'))
                tenant=User(name=f'Benchmark tenant {i}',email=f'tenant{i}@benchmark.test',role='tenant',unit_id=i);tenant.set_password(password);db.session.add(tenant)
            db.session.flush()
            records=[dict(device_id=i,event_id=f'bench-seed-{j}',timestamp=origin-timedelta(minutes=3332-j),
                          voltage=Decimal('230'),current=Decimal('1'),power=Decimal('230'),energy=Decimal(j)/1000,
                          delta_kwh=Decimal('0.001') if j else Decimal('0'),rate_id=1,source='hardware',meter_epoch='bench',connection_status='unknown')
                     for i in range(1,4) for j in range(3333)]
            db.session.execute(insert(Reading),records);db.session.commit()
            client=app.test_client();login=client.get('/login')
            import re
            csrf=re.search(r'name="csrf-token" content="([^"]+)"',login.get_data(as_text=True)).group(1)
            assert client.post('/login',data=dict(email='owner@benchmark.test',password=password,csrf_token=csrf)).status_code==302
            query_count=[0]
            def count(*unused):query_count[0]+=1
            event.listen(db.engine,'before_cursor_execute',count)
            durations=[];queries=[]
            for _ in range(20):
                query_count[0]=0;started=time.perf_counter();response=client.get('/api/snapshot');durations.append(time.perf_counter()-started)
                assert response.status_code==200 and len(response.json['units'])==3;queries.append(query_count[0])
            report['dashboard']=dict(**distribution(durations),queries_min=min(queries),queries_max=max(queries))
            event.remove(db.engine,'before_cursor_execute',count)
            durations=[]
            for j in range(1,21):
                for i in range(1,4):
                    body=dict(device_id=f'BENCH-{i}',unit_id=i,event_id=f'bench-api-{j}',meter_epoch='bench',timestamp=(origin+timedelta(seconds=j)).isoformat(),
                              voltage='230',current='1',power_w='230',energy_kwh=str(Decimal('3.332')+Decimal(j)/1000),connection_status='unknown',gateway_fixture=True)
                    started=time.perf_counter();response=client.post('/api/iot/readings',json=body,headers={'X-Device-ID':f'BENCH-{i}','Authorization':'Bearer '+tokens[i]});durations.append(time.perf_counter()-started)
                    assert response.status_code==201
            report['ingestion']=dict(**distribution(durations),accepted=60,expected_transactions=60)
            assert len(db.session.scalars(select(Transaction)).all())==60
            report['query_plan']=[row[0] for row in db.session.execute(text("EXPLAIN ANALYZE SELECT id FROM meter_readings WHERE device_id=1 ORDER BY timestamp DESC LIMIT 1"))]
        finally:
            db.session.remove();db.drop_all();db.session.execute(text('DROP TABLE IF EXISTS alembic_version'));db.session.commit();db.engine.dispose()
    with tempfile.TemporaryDirectory(prefix='dormenergy-benchmark-') as directory:
        root=Path(directory);key=root/'test.secret';key.write_text(secrets.token_urlsafe(32));key.chmod(0o600)
        devices=tuple(GatewayDevice(f'room{i}',f'DEV-BENCH-{i}',i,f'room{i}/telemetry',f'room{i}/ack',f'room{i}/status','bench',None) for i in range(1,4))
        config=Config('development',root/'queue.sqlite3',devices,'127.0.0.1',1883,'benchmark','gateway',key,None,False,'',None,1,8,100000,536870912,0,7,90,86400)
        journal=Journal(config);durations=[];start=utcnow()-timedelta(seconds=args.queue_rows+1)
        try:
            for j in range(args.queue_rows):
                device=devices[j%3]
                body=dict(device_id=device.device_id,unit_id=device.unit_id,event_id=f'bench-{j}',meter_epoch='bench',timestamp=(start+timedelta(seconds=j)).isoformat(),
                          voltage='230',current='1',power_w='230',energy_kwh=str(Decimal(j//3)/1000),connection_status='unknown',measurement_origin='development_fixture',clock_synchronized=True)
                measurement=validate(json.dumps(body).encode(),device,config,time.time())
                before=time.perf_counter();journal.enqueue(measurement);durations.append(time.perf_counter()-before)
            report['queue_enqueue']=dict(rows=args.queue_rows,**distribution(durations))
            claims=[]
            for _ in range(100):
                before=time.perf_counter();row=journal.claim();claims.append(time.perf_counter()-before);assert row
                journal.outcome(row['id'],'sent')
            report['queue_claim']=distribution(claims)
            report['unsent_after_claims']=journal.health()['unsent'];assert report['unsent_after_claims']==args.queue_rows-100
        finally:journal.close()
    report['process_cpu_seconds']=round(time.process_time()-cpu_start,3)
    try:
        import resource
        report['peak_process_rss_mib']=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,3)
    except ImportError:report['peak_process_rss_mib']=None
    destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True);destination.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('query_plan',)},indent=2))

if __name__=='__main__':main()
