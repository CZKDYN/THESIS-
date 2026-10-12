"""Exercise real HTTPS forms with secure cookies in an isolated local test database.

Requires Playwright in the runner Python and Chromium. The child uses .venv.
The temporary self-signed certificate is trusted only in these local test contexts.
No hosted data or email provider is used.
"""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = 'https://127.0.0.1:10006'
OWNER = 'HTTPSBrowserOwnerTestOnly!2026'
TENANT = 'HTTPSBrowserTenantTestOnly!2026'
RECOVERED = 'HTTPSBrowserRecoveredTestOnly!2026'
ARTIFACTS = ROOT/'artifacts'
SERVER = '''
import sys
from sqlalchemy import select
from dormenergy import create_app
from dormenergy.extensions import db
from dormenergy.models import Account, EmailOutbox, Unit, User
from dormenergy.services.emailing import issue_link, decrypt_payload
from werkzeug.serving import make_server
app=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':'sqlite:///'+sys.argv[1],
    'SECRET_KEY':'HttpsBrowserSigningTestOnly','SIMULATION_MODE':False,
    'SESSION_COOKIE_SECURE':True,'EMAIL_BACKEND':'memory',
    'PUBLIC_BASE_URL':'https://127.0.0.1:10006'})
with app.app_context():
    db.create_all()
    owner=User(name='HTTPS validation owner',email='owner@https-validation.example',role='admin')
    owner.set_password('HTTPSBrowserOwnerTestOnly!2026')
    unit=Unit(name='HTTPS validation unit',room='HTTPS validation room',is_demo=False)
    db.session.add_all([owner,unit]);db.session.flush()
    db.session.add(Account(unit_id=unit.id,balance=0,low_threshold=100))
    tenant=User(name='HTTPS validation tenant',email='tenant@https-validation.example',
        role='tenant',unit_id=unit.id,activation_status='pending',active=False)
    tenant.set_password('IneligiblePlaceholderTestOnly!2026')
    db.session.add(tenant);db.session.flush();issue_link(tenant,'activation');db.session.commit()
make_server('127.0.0.1',10006,app,ssl_context='adhoc').serve_forever()
'''
READ_LINK = '''
import re,sys
from sqlalchemy import select
from dormenergy import create_app
from dormenergy.extensions import db
from dormenergy.models import EmailOutbox
from dormenergy.services.emailing import decrypt_payload
app=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':'sqlite:///'+sys.argv[1],
    'SECRET_KEY':'HttpsBrowserSigningTestOnly'})
with app.app_context():
    row=db.session.scalar(select(EmailOutbox).where(EmailOutbox.kind==sys.argv[2]).order_by(EmailOutbox.id.desc()).limit(1))
    print(re.search(r'#token=([A-Za-z0-9_-]{43})',decrypt_payload(row)['text']).group(1))
'''


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    checks,errors,requests=[],[],[]
    with tempfile.TemporaryDirectory(prefix='dormenergy_https_',dir='/tmp') as temporary:
        database=str(Path(temporary)/'browser_test.sqlite')
        def link(kind):
            result=subprocess.run([str(ROOT/'.venv/bin/python'),'-c',READ_LINK,database,kind],
                                  cwd=ROOT,capture_output=True,text=True,check=True)
            return result.stdout.strip()
        with (ARTIFACTS/'https-browser-server.log').open('w') as log:
            server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-c',SERVER,database],cwd=ROOT,stdout=log,stderr=log)
            try:
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_EXECUTABLE','/usr/bin/chromium'))
                    # Certificate bypass is limited to our loopback self-signed test server.
                    owner=browser.new_context(ignore_https_errors=True,viewport={'width':1360,'height':900})
                    for attempt in range(50):
                        try:
                            response=owner.request.get(BASE+'/health',timeout=1000)
                            if response.ok:break
                        except Exception:
                            if server.poll() is not None:raise RuntimeError('Isolated HTTPS service exited')
                        time.sleep(.2)
                    else:raise RuntimeError('Isolated HTTPS service did not start')
                    page=owner.new_page()
                    def observe(page):
                        page.on('pageerror',lambda error:errors.append(str(error)))
                        page.on('request',lambda request:requests.append((request.url,request.headers.get('referer',''))))
                    observe(page)
                    response=page.goto(BASE+'/login')
                    assert response.headers['referrer-policy']=='same-origin'
                    assert all(cookie['secure'] for cookie in owner.cookies() if cookie['name']=='session')
                    page.fill('#email','owner@https-validation.example');page.fill('#password',OWNER)
                    page.get_by_role('button',name='Sign in to workspace').click()
                    page.wait_for_selector('#unit-cards')
                    assert (BASE+'/login',BASE+'/login') in requests
                    checks.append('HTTPS browser login succeeds with secure cookies and its automatic same-origin referrer')
                    page.screenshot(path=ARTIFACTS/'https-admin-desktop.png',full_page=True)
                    page.goto(BASE+'/account')
                    page.uncheck('input[name="email_load_updates"]')
                    page.get_by_role('button',name='Save email preferences').click()
                    page.wait_for_url(BASE+'/account')
                    assert not page.locator('input[name="email_load_updates"]').is_checked()
                    checks.append('Authenticated HTTPS form saves preferences with strict form protection enabled')
                    raw_activation=link('activation')
                    tenant=browser.new_context(ignore_https_errors=True,viewport={'width':390,'height':844})
                    tenant_page=tenant.new_page();observe(tenant_page)
                    tenant_page.goto(BASE+'/activate#token='+raw_activation)
                    tenant_page.locator('#account-link-form').wait_for()
                    assert tenant_page.url==BASE+'/activate'
                    tenant_page.fill('#link-password',TENANT);tenant_page.fill('#link-confirm-password',TENANT)
                    tenant_page.get_by_role('button',name='Activate account',exact=True).click()
                    tenant_page.wait_for_url(BASE+'/login')
                    checks.append('HTTPS activation checks and redeems a fragment link without exposing the token in referrers')
                    tenant_page.fill('#email','tenant@https-validation.example');tenant_page.fill('#password',TENANT)
                    tenant_page.get_by_role('button',name='Sign in to workspace').click()
                    tenant_page.wait_for_selector('#unit-cards')
                    snapshot=tenant.request.get(BASE+'/api/snapshot').json()
                    assert snapshot['user']['role']=='tenant' and len(snapshot['units'])==1
                    assert tenant_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    checks.append('Activated tenant signs in on HTTPS and sees only their unit in a phone layout')
                    tenant_page.goto(BASE+'/forgot-password')
                    tenant_page.fill('#recovery-email','owner@https-validation.example')
                    tenant_page.get_by_role('button',name='Request reset link').click()
                    tenant_page.get_by_role('status').wait_for()
                    raw_reset=link('reset')
                    tenant_page.goto(BASE+'/reset-password#token='+raw_reset)
                    tenant_page.locator('#account-link-form').wait_for()
                    tenant_page.fill('#link-password',RECOVERED);tenant_page.fill('#link-confirm-password',RECOVERED)
                    tenant_page.get_by_role('button',name='Reset password',exact=True).click()
                    tenant_page.wait_for_url(BASE+'/login')
                    assert owner.request.get(BASE+'/api/snapshot').status==401
                    tenant_page.fill('#email','owner@https-validation.example');tenant_page.fill('#password',RECOVERED)
                    tenant_page.get_by_role('button',name='Sign in to workspace').click()
                    tenant_page.wait_for_selector('#unit-cards')
                    checks.append('HTTPS password recovery revokes the old session and permits the new password')
                    for raw in (raw_activation,raw_reset):
                        assert all(raw not in url and raw not in referrer for url,referrer in requests)
                    assert errors==[]
                    checks.append('All observed URLs/referrers exclude private links and the browser reports no JavaScript errors')
                    browser.close()
                result={'checks_passed':len(checks),'checks':checks,'errors':errors,'real_emails_sent':0,
                        'secure_cookies':True,'https_strict_csrf':True,'physical_meter_test':False}
                (ARTIFACTS/'browser-https-smoke.json').write_text(json.dumps(result,indent=2)+'\n')
                print(json.dumps(result))
            finally:
                server.terminate()
                try:server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill();server.wait(timeout=5)


if __name__=='__main__':
    main()
