"""Email UI checks on a fresh LOCAL test database; memory backend only.

Test links are decrypted locally in the test process, never served by a debug API.
No real email is sent. Required env: DATABASE_URL, SECRET_KEY, ADMIN_SETUP_TOKEN,
TEST_OWNER_PASSWORD, TEST_TENANT_PASSWORD; use EMAIL_BACKEND=memory.
"""
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

BASE=os.getenv('DORMENERGY_BASE_URL','http://127.0.0.1:10004')
if urlparse(BASE).hostname not in ('localhost','127.0.0.1') or os.getenv('EMAIL_BACKEND')!='memory':
    raise SystemExit('Use a local memory-backend test deployment only.')
OWNER=os.environ['TEST_OWNER_PASSWORD']
TENANT=os.environ['TEST_TENANT_PASSWORD']
NEW_OWNER='RecoveredBrowserOwnerOnly!2026'
ARTIFACTS=Path('artifacts')
ARTIFACTS.mkdir(exist_ok=True)
HELPER='''
import re,sys
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.engine import make_url
from dormenergy import create_app
from dormenergy.extensions import db
from dormenergy.models import AccountToken,EmailOutbox,User,utcnow
from dormenergy.services.emailing import decrypt_payload
app=create_app()
assert make_url(app.config['SQLALCHEMY_DATABASE_URI']).database.endswith('_test')
with app.app_context():
    user=db.session.scalar(select(User).where(User.email==sys.argv[1]))
    row=db.session.scalar(select(EmailOutbox).where(EmailOutbox.user_id==user.id,EmailOutbox.kind==sys.argv[2]).order_by(EmailOutbox.id.desc()).limit(1))
    if len(sys.argv)>3:
        token=db.session.get(AccountToken,row.token_id)
        token.created_at=utcnow()-timedelta(seconds=61)
        token.expires_at=utcnow()-timedelta(seconds=1)
        db.session.commit()
    else:
        print(re.search(r'#token=([A-Za-z0-9_-]{43})',decrypt_payload(row)['text']).group(1))
'''


def link(address,kind,expire=False):
    arguments=['.venv/bin/python','-c',HELPER,address,kind]
    if expire:arguments.append('expire')
    result=subprocess.run(arguments,capture_output=True,text=True,check=True)
    return result.stdout.strip()


def sign_in(page,address,password):
    page.context.clear_cookies()
    page.goto(BASE+'/login')
    page.fill('#email',address);page.fill('#password',password)
    page.get_by_role('button',name='Sign in to workspace').click()
    page.wait_for_selector('#unit-cards')


def main():
    checks,errors,requests=[],[],[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_EXECUTABLE','/usr/bin/chromium'))
        owner_context=browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce')
        tenant_context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
        owner,tenant=owner_context.new_page(),tenant_context.new_page()
        for page in (owner,tenant):
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:requests.append(request.url))
        owner.goto(BASE+'/setup')
        owner.fill('#setup-token',os.environ['ADMIN_SETUP_TOKEN'])
        owner.fill('#account-name','Email validation owner')
        owner.fill('#account-email','owner@email-validation.example')
        owner.fill('#new-password',OWNER);owner.fill('#confirm-password',OWNER)
        owner.get_by_role('button',name='Create administrator').click()
        owner.wait_for_function('() => typeof state!=="undefined" && state')
        owner.locator('.nav-link[data-view="units"]').click()
        owner.locator('#view-units [data-open="unit-dialog"]').click()
        owner.fill('#unit-name','Email validation unit');owner.fill('#unit-room','Email validation room')
        owner.fill('#device-id','EMAIL-VALIDATION-METER')
        owner.locator('#unit-form').get_by_role('button',name='Register unit',exact=True).click()
        owner.wait_for_function('() => !document.querySelector("#unit-dialog").open')
        owner.locator('[data-assign="1"]').wait_for();owner.locator('[data-assign="1"]').click()
        assert owner.locator('#tenant-registration-method').input_value()=='email'
        assert owner.locator('#tenant-password').is_disabled()
        owner.fill('#tenant-name','Email validation tenant');owner.fill('#tenant-email','tenant@email-validation.example')
        owner.get_by_role('button',name='Create tenant account').click()
        owner.wait_for_function('() => !document.querySelector("#tenant-dialog").open')
        owner.locator('[data-resend-invitation="1"]').wait_for()
        owner.wait_for_function('() => state.units[0].email_delivery==="test"',timeout=30000)
        assert 'Pending' in owner.locator('.invitation-state').inner_text()
        owner.screenshot(path=ARTIFACTS/'email-invitation-desktop.png',full_page=True)
        checks.append('Email-first tenant registration is pending, with explicit test-only delivery status')
        raw=link('tenant@email-validation.example','activation')
        link('tenant@email-validation.example','activation',expire=True)
        owner.reload();owner.wait_for_function('() => state && state.units[0].invitation_status==="Expired"')
        owner.locator('.nav-link[data-view="units"]').click()
        owner.locator('[data-resend-invitation="1"]').click();owner.locator('#confirm-action').click()
        owner.wait_for_function('() => !document.querySelector("#confirm-dialog").open')
        owner.wait_for_function('() => state.units[0].email_delivery==="test" && state.units[0].invitation_status==="Pending"',timeout=30000)
        replacement=link('tenant@email-validation.example','activation')
        assert replacement!=raw
        checks.append('Expired invitation state and safe resending through the administrator UI')
        tenant.goto(BASE+'/activate#token='+replacement)
        tenant.wait_for_selector('#account-link-form',state='visible')
        assert tenant.url==BASE+'/activate'
        assert tenant.evaluate('document.documentElement.scrollWidth<=innerWidth')
        tenant.screenshot(path=ARTIFACTS/'email-activation-mobile.png',full_page=True)
        tenant.fill('#link-password',TENANT);tenant.fill('#link-confirm-password',TENANT)
        tenant.get_by_role('button',name='Activate account',exact=True).click()
        tenant.wait_for_url(BASE+'/login')
        sign_in(tenant,'tenant@email-validation.example',TENANT)
        tenant.wait_for_function('() => state && state.units.length===1')
        assert tenant_context.request.get(BASE+'/api/snapshot?unit_id=2').status==403
        assert not any(replacement in url for url in requests)
        checks.append('Responsive activation, private password, unit isolation, and secrets excluded from request URLs')
        owner.reload();owner.wait_for_function('() => state && state.units[0].invitation_status==="Activated"')
        owner.locator('.nav-link[data-view="units"]').click()
        assert owner.locator('[data-resend-invitation="1"]').count()==0
        owner.locator('.nav-link[data-view="notifications"]').click()
        owner.locator('#email-delivery-table tr').first.wait_for()
        assert 'Test only' in owner.locator('#email-delivery-table').inner_text()
        assert 'does not prove inbox delivery' in owner.locator('.email-status-note').inner_text()
        owner.screenshot(path=ARTIFACTS/'email-delivery-desktop.png',full_page=True)
        checks.append('Activated tenant status and non-sensitive delivery monitoring')
        tenant.goto(BASE+'/account')
        tenant.locator('input[name="email_balance_alerts"]').uncheck()
        tenant.locator('input[name="email_load_updates"]').uncheck()
        tenant.get_by_role('button',name='Save email preferences').click()
        tenant.wait_for_url(BASE+'/account')
        assert not tenant.locator('input[name="email_balance_alerts"]').is_checked()
        assert 'Essential account security' in tenant.locator('.account-preferences').inner_text()
        assert tenant.evaluate('document.documentElement.scrollWidth<=innerWidth')
        tenant.screenshot(path=ARTIFACTS/'email-preferences-mobile.png',full_page=True)
        checks.append('Mobile notification preferences with essential security emails retained')
        recovery_context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
        recovery=recovery_context.new_page()
        recovery.on('pageerror',lambda error:errors.append(str(error)))
        recovery.goto(BASE+'/login');recovery.get_by_role('link',name='Forgot Password?').click()
        recovery.fill('#recovery-email','owner@email-validation.example')
        recovery.get_by_role('button',name='Request reset link').click()
        assert 'If an eligible account matches' in recovery.locator('.account-notice').inner_text()
        recovery.screenshot(path=ARTIFACTS/'email-recovery-mobile.png',full_page=True)
        reset=link('owner@email-validation.example','reset')
        owner.goto('about:blank')  # Stop polling while verifying deliberate session revocation.
        recovery.goto(BASE+'/reset-password#token='+reset)
        recovery.wait_for_selector('#account-link-form',state='visible')
        recovery.fill('#link-password',NEW_OWNER);recovery.fill('#link-confirm-password',NEW_OWNER)
        recovery.get_by_role('button',name='Reset password',exact=True).click()
        recovery.wait_for_url(BASE+'/login')
        assert owner_context.request.get(BASE+'/api/snapshot').status==401
        sign_in(owner,'owner@email-validation.example',NEW_OWNER)
        owner.wait_for_function('() => state && state.user.role==="admin"')
        checks.append('Forgot-password link, generic confirmation, administrator recovery, and session revocation')
        assert not errors,errors
        checks.append('No JavaScript page errors in the new onboarding and recovery flows')
        result=dict(checks_passed=len(checks),checks=checks,errors=errors,backend='memory',real_emails_sent=0)
        (ARTIFACTS/'browser-email-smoke.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
        browser.close()


if __name__=='__main__':main()
