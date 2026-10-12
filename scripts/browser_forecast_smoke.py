"""Synthetic forecast UI/resource check on temporary loopback SQLite, never production."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT/'artifacts/audit'
SERVER = '''
import json,math,os,resource,sys,time
from datetime import timedelta
from decimal import Decimal
from flask_migrate import upgrade
from dormenergy import create_app
from dormenergy.extensions import db
from dormenergy.models import Account,Device,Rate,Reading,Unit,User,utcnow
from dormenergy.services.forecasting import floor_hour,generate_forecasts,train_model
from werkzeug.serving import make_server
app=create_app(dict(TESTING=True,SECRET_KEY=os.environ['AUDIT_BROWSER_SECRET'],SQLALCHEMY_DATABASE_URI='sqlite:///'+sys.argv[1],
    SIMULATION_MODE=True,EMAIL_BACKEND='disabled',UTILITY_RATE_LOCKED=False,SESSION_COOKIE_SECURE=False))
with app.app_context():
    upgrade(directory='migrations')
    unit=Unit(name='Synthetic forecast fixture',room='Temporary software test',is_demo=True)
    owner=User(name='Test owner',email='owner@forecast.example.test',role='admin');owner.set_password(os.environ['AUDIT_BROWSER_PASSWORD'])
    db.session.add_all([unit,owner]);db.session.flush()
    db.session.add(Account(unit_id=unit.id,balance=Decimal('100')))
    device=Device(identifier='SYNTHETIC-FORECAST-TEST',unit_id=unit.id,source='simulation');db.session.add(device);db.session.flush()
    end=floor_hour(utcnow());rate=Rate(php_per_kwh=Decimal('12.5'),effective_at=end-timedelta(days=32));db.session.add(rate);db.session.flush()
    total=Decimal('0')
    for index in range(745):
        timestamp=end-timedelta(hours=744-index)
        # Deliberately irregular occupancy and three missing observations; software fixtures only.
        delta=Decimal(str(round((.18+.07*math.sin(timestamp.hour*math.pi/12)**2)*(1.4 if index>=600 else 1),6))) if index else Decimal('0')
        total+=delta
        if index in (200,400,610):continue
        db.session.add(Reading(device_id=device.id,event_id=f'fixture-hour-{index}',timestamp=timestamp,voltage=230,current=1,power=230,
            energy=total,delta_kwh=delta,rate_id=rate.id,source='simulation',meter_epoch='fixture',connection_status='unknown'))
    db.session.commit()
    before=time.perf_counter();cpu=time.process_time();model=train_model(unit.id,now=end)
    report=dict(software_fixtures_only=True,source='simulation',generated_hourly_intervals=744,omitted_observations=3,
        train_wall_seconds=round(time.perf_counter()-before,3),train_cpu_seconds=round(time.process_time()-cpu,3),
        peak_process_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,3),model_json_bytes=len(model.model_json.encode()),
        coverage=model.metadata_json['coverage'],test=model.metadata_json['test'],baselines=model.metadata_json['baselines'],
        recursive_backtest=model.metadata_json['recursive_backtest'])
    before=time.perf_counter();generate_forecasts(unit.id,now=end)
    report['forecast_wall_seconds']=round(time.perf_counter()-before,3)
    open(sys.argv[3],'w').write(json.dumps(report,indent=2))
make_server('127.0.0.1',int(sys.argv[2]),app,threaded=True).serve_forever()
'''


def main():
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    with socket.socket() as socket_: socket_.bind(('127.0.0.1',0));port=socket_.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    env=dict(os.environ,AUDIT_BROWSER_PASSWORD=secrets.token_urlsafe(24),AUDIT_BROWSER_SECRET=secrets.token_urlsafe(32))
    with tempfile.TemporaryDirectory(prefix='dormenergy-forecast-ui-') as temporary:
        with open(ARTIFACTS/'forecast-browser-server.log','wb') as log:
            server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-c',SERVER,str(Path(temporary)/'test.sqlite'),str(port),str(ARTIFACTS/'forecast-benchmark.json')],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_EXECUTABLE','/usr/bin/chromium'))
                    page=browser.new_page(viewport={'width':1360,'height':900});errors=[]
                    page.on('pageerror',lambda error:errors.append(type(error).__name__))
                    for _ in range(100):
                        try:
                            if page.request.get(origin+'/health',timeout=1000).ok:break
                        except Exception:
                            if server.poll() is not None:raise RuntimeError('Isolated forecast service exited.')
                            time.sleep(.1)
                    else:raise RuntimeError('Isolated forecast service did not become ready.')
                    page.goto(origin+'/login');page.fill('#email','owner@forecast.example.test');page.fill('#password',env['AUDIT_BROWSER_PASSWORD'])
                    page.get_by_role('button',name='Sign in to workspace').click();page.wait_for_url('**/dashboard')
                    page.locator('#unit-filter').select_option('1')
                    page.locator('.nav-link[data-view=forecast]').click()
                    expect(page.locator('#forecast-baselines')).to_contain_text('24h: endpoint MAE')
                    expect(page.locator('#forecast-baselines')).to_contain_text('No calibrated confidence intervals')
                    expect(page.locator('#forecast-generated')).to_contain_text('Synthetic demo')
                    assert page.locator('#forecast-chart').is_visible() and page.locator('#forecast-test-chart').is_visible()
                    page.screenshot(path=ARTIFACTS/'forecast-desktop.png',full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert not errors
                    page.screenshot(path=ARTIFACTS/'forecast-mobile.png',full_page=True)
                    browser.close()
                report=dict(checks_passed=4,checks=['Recursive multi-hour metrics and baselines rendered','Synthetic source and uncertainty limits explicit','Both forecast charts rendered','Phone layout and JavaScript error checks passed'],errors=errors,real_emails_sent=0,production_readings=0)
                (ARTIFACTS/'forecast-browser.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
            finally:
                server.terminate()
                try:server.wait(5)
                except subprocess.TimeoutExpired:server.kill();server.wait(5)


if __name__=='__main__':main()
