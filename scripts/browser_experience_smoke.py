"""Real rendered UX/accessibility checks with temporary synthetic fixture data only.

Run with the Playwright interpreter. App/DB/model use .venv in a private local
directory, never retained/production services. Optional AXE_CORE_PATH is a local
verified axe-core file served only through the test browser's loopback route.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=ROOT/os.getenv('EXPERIENCE_ARTIFACT_DIR','artifacts/website')
SERVER=r'''
import math,os,sys
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select
from flask_migrate import upgrade
from werkzeug.serving import make_server
from dormenergy import create_app
from dormenergy.extensions import db
from dormenergy.models import Account,Device,EmailOutbox,LoadRequest,Notification,Rate,Reading,Transaction,Unit,User,utcnow
from dormenergy.services.forecasting import floor_hour,generate_forecasts,train_model
app=create_app(dict(TESTING=True,SECRET_KEY=os.environ['EXPERIENCE_TEST_SECRET'],SQLALCHEMY_DATABASE_URI='sqlite:///'+sys.argv[1],
    SIMULATION_MODE=True,UTILITY_RATE_LOCKED=True,UTILITY_RATE_CONFIRMED=False,EMAIL_BACKEND='disabled',SESSION_COOKIE_SECURE=False))
with app.app_context():
    upgrade(directory='migrations');now=utcnow();end=floor_hour(now)
    owner=User(id=1,name='Experience fixture owner',email='owner@experience.example.test',role='admin',created_at=now-timedelta(days=60));owner.set_password(os.environ['EXPERIENCE_TEST_PASSWORD']);db.session.add(owner)
    for i in range(1,4):
        unit=Unit(id=i,name=f'Fixture unit 0{i}',room=f'Floor {i} · software test only',is_demo=i<3,power_status='unknown')
        db.session.add(unit);db.session.flush();db.session.add(Account(unit_id=i,balance=['420.50','40.25','0'][i-1],low_threshold=100))
        device=Device(id=i,unit_id=i,identifier=f'FIXTURE-METER-{i}',source='simulation' if i<3 else 'hardware',last_seen=now if i<3 else None)
        db.session.add(device)
        tenant=User(id=i+1,name=f'Fixture tenant {i}',email=f'tenant{i}@experience.example.test',role='tenant',unit_id=i,
            created_at=now-timedelta(days=60),active=i<3,activation_status='active' if i<3 else 'pending')
        tenant.set_password(os.environ['EXPERIENCE_TEST_PASSWORD']);db.session.add(tenant)
    rate=Rate(id=1,php_per_kwh=Decimal('12.5'),effective_at=now-timedelta(days=40),is_demo=True);db.session.add(rate);db.session.flush()
    total=Decimal('0')
    for index in range(745):
        stamp=end-timedelta(hours=744-index)
        delta=Decimal(str(round((.18+.07*math.sin(stamp.hour*math.pi/12)**2)*(1.4 if index>=600 else 1),6))) if index else Decimal('0')
        total+=delta
        if index in (200,400,610):continue
        db.session.add(Reading(device_id=1,event_id=f'history-{index}',timestamp=stamp,received_at=now,voltage=230,current=1,power=230,
            energy=total,delta_kwh=delta,rate_id=1,source='simulation',meter_epoch='fixture',connection_status='unknown'))
    db.session.add_all([Reading(device_id=1,event_id='fresh',timestamp=now-timedelta(seconds=10),received_at=now,voltage=230,current=1,power=230,
        energy=total+Decimal('.001'),delta_kwh=Decimal('.001'),rate_id=1,source='simulation',meter_epoch='fixture',connection_status='unknown'),
        Reading(device_id=2,event_id='stale',timestamp=now-timedelta(hours=2),received_at=now,voltage=230,current=1,power=230,
        energy=10,delta_kwh=0,rate_id=1,source='simulation',meter_epoch='fixture',connection_status='unknown')])
    approved=LoadRequest(user_id=2,unit_id=1,amount=500,status='approved',reference='APPROVED-FIXTURE',created_at=now-timedelta(days=7),resolved_at=now-timedelta(days=7),resolved_by=1)
    db.session.add(approved);db.session.flush()
    db.session.add(Transaction(unit_id=1,kind='credit',amount=500,balance_after=500,description='Synthetic opening top-up',load_request_id=approved.id,created_at=now-timedelta(days=7)))
    balance=Decimal('500')
    for i in range(53):
        balance-=Decimal('1.5')
        db.session.add(Transaction(unit_id=1,kind='consumption',amount=Decimal('-1.5'),balance_after=balance,description=f'Synthetic ledger fixture {i}',created_at=now-timedelta(hours=53-i)))
    for i in range(35):
        unit=1 if i<8 else 2
        db.session.add(LoadRequest(user_id=unit+1,unit_id=unit,amount=25,status='pending',reference=f'REQ-{i:03}',note='Software fixture; payment is not real',created_at=now-timedelta(minutes=i)))
    for i in range(27):db.session.add(LoadRequest(user_id=2,unit_id=1,amount=10,status='rejected',reference=f'REJECTED-{i:03}',resolved_at=now-timedelta(days=2),created_at=now-timedelta(days=3)))
    db.session.add_all([Notification(unit_id=1,kind='low_balance',message='Historical fixture message; not a current task',created_at=now-timedelta(days=30)),
        Notification(unit_id=1,kind='load_approved',message='Recorded fixture top-up approved',created_at=now-timedelta(days=7)),
        EmailOutbox(user_id=1,kind='security_password',status='failed',error_code='configuration',dedupe_key='fixture-delivery',created_at=now)])
    db.session.commit();model=train_model(1,now=end);generate_forecasts(1,now=now);model.trained_at=now-timedelta(days=8);db.session.commit()
make_server('127.0.0.1',int(sys.argv[2]),app,threaded=True).serve_forever()
'''


def main():
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    env=dict(os.environ,EXPERIENCE_TEST_PASSWORD=secrets.token_urlsafe(24),EXPERIENCE_TEST_SECRET=secrets.token_urlsafe(32))
    checks=[];errors=[];screens=[];accessibility=[]
    def nav(page,view):
        if page.locator('#mobile-menu').is_visible():page.locator('#mobile-menu').click()
        page.locator(f'#sidebar .nav-link[data-view={view}]').click()
        expect(page.locator('#view-'+view)).to_be_visible()
    def screenshot(page,name,full_page=True):
        page.locator('#toast').evaluate('(node)=>node.hidden=true')
        target=ARTIFACTS/(name+'.png');page.screenshot(path=target,full_page=full_page);screens.append(target.name)
    def scan(page,name):
        if not os.getenv('AXE_CORE_PATH'):return
        page.add_script_tag(url=origin+'/static/validation-only/axe.js')
        report=page.evaluate("async()=>axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}})")
        accessibility.append(dict(view=name,violations=[dict(id=v['id'],impact=v['impact'],help=v['help'],url=v['helpUrl'],nodes=[dict(target=n['target'],summary=n.get('failureSummary')) for n in v['nodes']]) for v in report['violations']]))
    with tempfile.TemporaryDirectory(prefix='dormenergy-experience-') as temporary:
        with open(ARTIFACTS/'fixture-server.log','wb') as log:
            server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-c',SERVER,str(Path(temporary)/'experience_test'),str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                with sync_playwright() as pw:
                    executable=os.getenv('CHROMIUM_EXECUTABLE') or ('/usr/bin/chromium' if Path('/usr/bin/chromium').exists() else None)
                    browser=pw.chromium.launch(headless=True,executable_path=executable)
                    owner=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                    tenant=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
                    for context in (owner,tenant):
                        if os.getenv('AXE_CORE_PATH'):
                            context.route('**/static/validation-only/axe.js',lambda route:route.fulfill(status=200,content_type='application/javascript',body=Path(os.environ['AXE_CORE_PATH']).read_text()))
                    page=owner.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                    for _ in range(100):
                        try:
                            if page.request.get(origin+'/health',timeout=1000).ok:break
                        except Exception:
                            if server.poll() is not None:raise RuntimeError('Isolated UX fixture service exited; inspect fixture log.')
                            time.sleep(.1)
                    else:raise RuntimeError('Isolated UX fixture did not start.')
                    page.goto(origin+'/login');scan(page,'login');page.fill('#email','owner@experience.example.test');page.fill('#password',env['EXPERIENCE_TEST_PASSWORD'])
                    page.get_by_role('button',name='Sign in to workspace').click();page.wait_for_url('**/dashboard')
                    assert page.locator('.workspace-label,.workspace-avatar,.prototype-note').count()==0
                    expect(page.locator('#sidebar .profile')).to_contain_text('Experience fixture owner')
                    expect(page.locator('#sidebar .profile')).to_contain_text('Administrator')
                    assert page.locator('#sidebar .nav-group').count()==3
                    assert 'PUP' not in page.locator('#sidebar').inner_text()
                    page.locator('.system-information summary').click();expect(page.locator('.system-information-panel')).to_contain_text('Development build')
                    expect(page.locator('#system-data-mode')).to_contain_text('Simulation');scan(page,'admin-system-information')
                    page.locator('.system-information summary').click()
                    checks.append('Minimal grouped sidebar removes both components from the DOM and retains authenticated profile, logout and accessible development information')
                    nav(page,'settings');expect(page.locator('#utility-reference-rate')).to_have_value('14.7400')
                    link=page.get_by_role('link',name='View official Meralco rate information',exact=False)
                    assert link.get_attribute('href')=='https://company.meralco.com.ph/news-and-advisories/higher-residential-rates-october-2026'
                    assert link.get_attribute('target')=='_blank' and {'noopener','noreferrer'} <= set(link.get_attribute('rel').split())
                    assert link.locator('use').get_attribute('href')=='#i-external-link'
                    expect(page.locator('#view-settings')).to_contain_text('October 2026 residential-rate advisory')
                    assert page.locator('#utility-reference-rate').get_attribute('readonly') is not None
                    rates=owner.request.get(origin+'/api/rates').json();assert rates['active_rate'] is None
                    scan(page,'admin-rate-reference');screenshot(page,'admin-rates-1440')
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    scan(page,'mobile-rate-reference');screenshot(page,'admin-rates-390')
                    page.set_viewport_size({'width':1440,'height':1000});nav(page,'overview')
                    checks.append('Official monthly advisory link has the expected HTTPS destination, new-tab protection and external indicator; the unverified PHP14.74 reference stays read-only with no active tariff (external availability not tested by this runner)')
                    expect(page.locator('#op-units')).to_have_text('3');expect(page.locator('#op-stale')).to_have_text('1')
                    expect(page.locator('#op-pending')).to_have_text('35');expect(page.locator('#op-activation')).to_have_text('1')
                    assert 'Historical fixture message' not in page.locator('#overview-alerts').inner_text()
                    assert page.locator('.balance-track').count()==0
                    checks.append('Admin operations use current states and complete pending counts; no fixed credit bar or historical task inference')
                    screenshot(page,'admin-overview-1440');scan(page,'admin-overview')
                    nav(page,'transactions');expect(page.locator('#transactions-status')).to_contain_text('54 matching records')
                    assert page.locator('#transactions-table tr').count()==25
                    first=page.locator('#transactions-table').inner_text();page.locator('#transactions-next').click();expect(page.locator('#transactions-page-label')).to_contain_text('Page 2')
                    assert first!=page.locator('#transactions-table').inner_text()
                    page.locator('#transactions-search').fill('opening');expect(page.locator('#transactions-status')).to_contain_text('1 matching records')
                    await_refresh=page.evaluate('async()=>{await refresh();return document.activeElement.id}')
                    assert await_refresh=='transactions-search' and page.locator('#transactions-search').input_value()=='opening'
                    link=page.locator('#export-link').get_attribute('href');assert 'q=opening' in link
                    csv=owner.request.get(origin+link);assert csv.ok and 'Synthetic opening top-up' in csv.text() and 'ledger fixture' not in csv.text()
                    page.locator('#transactions-from').fill('2026-10-20');page.locator('#transactions-to').fill('2026-10-01')
                    expect(page.locator('#transactions-status')).to_contain_text('valid date range');assert page.locator('#export-link').get_attribute('aria-disabled')=='true'
                    page.locator('#transactions-filters button[type=reset]').click();expect(page.locator('#transactions-status')).to_contain_text('54 matching records')
                    checks.append('Transaction pagination, literal search, date validation, exact filters/export and focused-input preservation')
                    screenshot(page,'admin-transactions-1440');scan(page,'transactions')
                    nav(page,'loads');page.locator('#loads-kind').select_option('pending');page.locator('#loads-search').fill('REQ-020')
                    expect(page.locator('#loads-status')).to_contain_text('1 matching records');page.locator('[data-review][data-decision=approved]').click()
                    expect(page.locator('#confirm-dialog')).to_be_visible();assert 'adds prepaid credit once' in page.locator('#confirm-description').inner_text()
                    page.locator('#confirm-action').click();expect(page.locator('#confirm-dialog')).not_to_be_visible()
                    expect(page.locator('#loads-status')).to_contain_text('0 matching records')
                    page.locator('#loads-kind').select_option('approved');expect(page.locator('#loads-table')).to_contain_text('₱25.00 credited')
                    checks.append('Admin approval retains confirmation, actual ledger credit and current review timestamp')
                    scan(page,'load-inbox')
                    page.locator('#unit-filter').select_option('1');page.wait_for_function("() => state && state.units.length===1 && !snapshotBusy")
                    nav(page,'forecast');expect(page.locator('#forecast-caution')).to_contain_text('did not outperform')
                    expect(page.locator('#forecast-age')).to_have_text('Retraining due');assert page.locator('#forecast-recursive-table tr').count()==4
                    screenshot(page,'forecast-1440');scan(page,'forecast')
                    checks.append('Actual synthetic XGBoost artifact renders explicit baseline caution, four recursive horizons, model age and estimates')
                    page.evaluate("()=>{const data=structuredClone(state.forecast);data.model_age={...data.model_age,expired:true};renderForecast(data)}")
                    expect(page.locator('#forecast-next')).to_have_text('Not available');expect(page.locator('#forecast-future-card')).not_to_be_visible()
                    expect(page.locator('#forecast-summary-status')).to_contain_text('expired')
                    page.evaluate('()=>renderForecast(state.forecast)')
                    checks.append('Expired model presentation suppresses current predictions even in an inconsistent available reply')
                    page.locator('#unit-filter').select_option('');page.wait_for_function("() => state && state.units.length===3 && !snapshotBusy")
                    nav(page,'devices');assert 'queue' not in page.locator('#device-cards').inner_text().lower()
                    page.locator('#device-filter').select_option('stale');assert page.locator('#device-cards .device-card').count()==1
                    expect(page.locator('#device-cards')).to_contain_text('Last reading received by backend')
                    page.locator('#device-filter').select_option('');screenshot(page,'devices-1440');scan(page,'devices')
                    checks.append('Fresh/stale/awaiting distinction and separate original/receipt timestamps without invented gateway health')
                    nav(page,'units');page.locator('[data-edit-unit="1"]').click();page.fill('#edit-unit-name','Unsaved fixture name')
                    scan(page,'edit-unit-dialog')
                    await_refresh=page.evaluate('async()=>{await refresh();return document.activeElement.id}')
                    assert page.locator('#edit-unit-name').input_value()=='Unsaved fixture name' and await_refresh=='edit-unit-name'
                    page.keyboard.press('Escape');expect(page.locator('#edit-unit-dialog')).not_to_be_visible()
                    checks.append('Native dialog Escape/focus behavior and unsaved edits survive background updates')
                    nav(page,'overview');previous_balance=page.locator('#stat-balance').inner_text()
                    page.route('**/api/snapshot*',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"Isolated outage"}'))
                    page.evaluate('async()=>await refresh()');expect(page.locator('#connection-alert')).to_be_visible()
                    assert page.locator('#stat-balance').inner_text()==previous_balance and page.locator('#stat-power').inner_text()=='—'
                    page.unroute('**/api/snapshot*');page.locator('#retry-update').click();expect(page.locator('#connection-alert')).not_to_be_visible()
                    page.route('**/api/snapshot*',lambda route:route.fulfill(status=200,content_type='application/json',body='{}'))
                    page.evaluate('async()=>await refresh()');expect(page.locator('#connection-alert')).to_be_visible();assert page.locator('#stat-balance').inner_text()==previous_balance
                    page.unroute('**/api/snapshot*');page.locator('#retry-update').click();expect(page.locator('#connection-alert')).not_to_be_visible()
                    checks.append('503 and malformed success replies preserve recorded balances, mark live status unconfirmed, and Retry restores updates')
                    samples={str(i):owner.request.get(origin+f'/api/snapshot?unit_id={i}').json() for i in (1,2)}
                    page.evaluate("""async samples=>{
                      const original=window.fetch;let active=0,maximum=0,calls=0;
                      window.fetch=(url,options)=>String(url).startsWith('/api/snapshot')?new Promise(resolve=>{
                        active++;maximum=Math.max(maximum,active);calls++;
                        const id=new URL(url,location.origin).searchParams.get('unit_id');
                        setTimeout(()=>{active--;resolve(new Response(JSON.stringify(samples[id]),{status:200,headers:{'Content-Type':'application/json'}}))},180);
                      }):original(url,options);
                      document.querySelector('#unit-filter').value='1';const first=refresh();
                      document.querySelector('#unit-filter').value='2';refresh();await first;
                      while(snapshotBusy || refreshQueued)await new Promise(resolve=>setTimeout(resolve,30));
                      window.fetch=original;window.experienceRace={maximum,calls,unit:state.units[0].id};
                    }""",samples)
                    assert page.evaluate('experienceRace')==dict(maximum=1,calls=2,unit=2)
                    page.locator('#unit-filter').select_option('');page.wait_for_function('()=>state.units.length===3 && !snapshotBusy')
                    assert page.evaluate("[null,undefined,'',false,'not numeric'].every(value=>num(value)==='—') && num(0)==='0.00' && money('0.000001')==='₱0.000001'")
                    checks.append('Serialized polling discards an older unit-filter reply; unknown numeric values and precise small ledger amounts remain distinct from zero')
                    for width in (360,390,768,1024,1440):
                        page.set_viewport_size({'width':width,'height':900});nav(page,'overview')
                        assert page.locator('#sidebar .brand-icon').evaluate('(node)=>node.getBoundingClientRect().width>=31')
                        assert page.locator('#sidebar .brand small').evaluate('(node)=>node.getBoundingClientRect().height<=parseFloat(getComputedStyle(node).lineHeight)+1')
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overview overflow at {width}'
                        nav(page,'transactions');assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),f'Transaction overflow at {width}'
                        screenshot(page,f'admin-history-{width}');nav(page,'overview');screenshot(page,f'admin-overview-{width}')
                    page.set_viewport_size({'width':360,'height':800});page.locator('#mobile-menu').click()
                    scan(page,'mobile-navigation')
                    screenshot(page,'admin-navigation-360',full_page=False)
                    assert page.locator('.workspace').evaluate('(el)=>el.inert') and page.locator('#mobile-menu').get_attribute('aria-expanded')=='true'
                    page.keyboard.press('Shift+Tab');assert page.evaluate("document.activeElement.closest('#sidebar')!==null")
                    page.keyboard.press('Escape');expect(page.locator('#mobile-menu')).to_be_focused();assert not page.locator('.workspace').evaluate('(el)=>el.inert')
                    page.locator('#mobile-menu').click();page.get_by_role('button',name='Consumption',exact=True).click()
                    expect(page.locator('#page-title')).to_be_focused();expect(page.locator('#detail-chart-summary')).to_contain_text('Gaps mean unavailable')
                    scan(page,'mobile-consumption')
                    checks.append('Five requested viewport widths, keyboard drawer focus/Escape/inert and accessible chart summaries')
                    for width in (360,768,1440):
                        page.set_viewport_size({'width':width,'height':600})
                        if width==360:page.locator('#mobile-menu').click()
                        expect(page.locator('#sidebar .profile button[aria-label="Sign out"]')).to_be_in_viewport()
                        bounds=page.locator('#sidebar .profile').bounding_box();assert bounds['y']>=0 and bounds['y']+bounds['height']<=601
                        assert page.locator('.sidebar-navigation').evaluate('(node)=>node.scrollHeight>node.clientHeight')
                        if width==360:page.keyboard.press('Escape')
                    page.set_viewport_size({'width':1440,'height':1000})
                    checks.append('Short desktop/tablet/phone viewports scroll navigation independently while profile and logout stay visible')
                    tenant_page=tenant.new_page();tenant_page.on('pageerror',lambda error:errors.append(str(error)))
                    tenant_page.goto(origin+'/login');tenant_page.fill('#email','tenant1@experience.example.test');tenant_page.fill('#password',env['EXPERIENCE_TEST_PASSWORD'])
                    tenant_page.get_by_role('button',name='Sign in to workspace').click();tenant_page.wait_for_url('**/dashboard')
                    assert tenant_page.locator('.workspace-label,.workspace-avatar,.prototype-note').count()==0
                    expect(tenant_page.locator('#sidebar .profile')).to_contain_text('Fixture tenant 1')
                    expect(tenant_page.locator('#sidebar .profile')).to_contain_text('Tenant')
                    expect(tenant_page.locator('#unit-count')).to_have_text('1');expect(tenant_page.locator('#latest-topup')).to_contain_text('₱500.00')
                    assert tenant_page.locator('[data-device-token]').count()==0
                    for path in ('/api/transactions','/api/load-requests','/api/transactions/export'):
                        assert tenant.request.get(origin+path+'?unit_id=2').status==403
                    checks.append('Tenant unit/financial history scope, actual top-up and threshold display; no administrator credential controls')
                    tenant_page.get_by_role('button',name='Request load',exact=True).click();tenant_page.locator('[data-amount="100"]').click();tenant_page.fill('#load-note','Retained fixture request note')
                    scan(tenant_page,'mobile-load-dialog')
                    tenant_page.fill('#load-amount','-1');tenant_page.locator('#load-form button[type=submit]').click()
                    assert tenant_page.locator('#load-amount').get_attribute('aria-invalid')=='true'
                    tenant_page.fill('#load-amount','100');assert tenant_page.locator('#load-amount').get_attribute('aria-invalid') is None
                    tenant_page.route('**/api/load-requests',lambda route:route.fulfill(status=422,content_type='application/json',body='{"error":"Fixture validation response: review the request."}'))
                    tenant_page.locator('#load-form button[type=submit]').click()
                    expect(tenant_page.locator('#load-form .form-error')).to_be_focused()
                    assert tenant_page.locator('#load-form .form-error').get_attribute('id') in tenant_page.locator('#load-amount').get_attribute('aria-describedby').split()
                    assert tenant_page.locator('#load-note').input_value()=='Retained fixture request note'
                    tenant_page.unroute('**/api/load-requests')
                    tenant_page.evaluate('async()=>await refresh()');assert tenant_page.locator('#load-note').input_value()=='Retained fixture request note'
                    tenant_page.locator('#load-form button[type=submit]').click();expect(tenant_page.locator('#load-dialog')).not_to_be_visible()
                    expect(tenant_page.locator('#loads-table')).to_contain_text('Retained fixture request note')
                    current=tenant.request.get(origin+'/api/snapshot').json()['summary']['balance'];assert current=='420.500000'
                    checks.append('Tenant native/server validation associates errors and preserves edits; quick amounts/submission feedback work and pending requests never add credit')
                    nav(tenant_page,'overview');screenshot(tenant_page,'tenant-overview-390');scan(tenant_page,'tenant-overview')
                    nav(tenant_page,'notifications');tenant_page.locator('#notification-category').select_option('loads');expect(tenant_page.locator('#notifications-list')).not_to_contain_text('Historical fixture')
                    scan(tenant_page,'mobile-notifications')
                    checks.append('Categorized historical notifications without fabricated unread state or unresolved-task inference')
                    for width in (360,390,768,1024,1440):
                        tenant_page.set_viewport_size({'width':width,'height':900});nav(tenant_page,'overview');assert tenant_page.evaluate('document.documentElement.scrollWidth <= innerWidth');screenshot(tenant_page,f'tenant-overview-{width}')
                    tenant_page.set_viewport_size({'width':360,'height':800});tenant_page.goto(origin+'/account');scan(tenant_page,'mobile-account')
                    tenant_page.goto(origin+'/forgot-password');scan(tenant_page,'mobile-recovery')
                    tenant_page.goto(origin+'/activate');scan(tenant_page,'mobile-invalid-activation')
                    for session,context in ((page,owner),(tenant_page,tenant)):
                        session.goto(origin+'/dashboard');session.wait_for_function('()=>typeof state!=="undefined" && state && !snapshotBusy')
                        if session.locator('#mobile-menu').is_visible():session.locator('#mobile-menu').click()
                        session.locator('#sidebar .profile button[aria-label="Sign out"]').click();session.wait_for_url('**/login')
                        assert context.request.get(origin+'/api/snapshot').status==401
                    checks.append('Administrator and tenant profile logout controls submit the existing CSRF-protected form and revoke browser access')
                    assert not errors,errors
                    browser.close()
                report=dict(checks_passed=len(checks),checks=checks,errors=errors,accessibility=accessibility,screenshots=screens,
                            synthetic_fixture_data=True,production_readings=0,real_emails_sent=0,viewport_widths=[360,390,768,1024,1440])
                (ARTIFACTS/'browser-experience.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
                assert not any(result['violations'] for result in accessibility), 'Accessibility violations recorded; inspect browser-experience.json.'
            finally:
                server.terminate()
                try:server.wait(5)
                except subprocess.TimeoutExpired:server.kill();server.wait(5)


if __name__=='__main__':main()
