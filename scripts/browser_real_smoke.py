"""Mutating browser checks on a fresh isolated LOCAL real-mode test database.

Requires ADMIN_SETUP_TOKEN, TEST_OWNER_PASSWORD and TEST_TENANT_PASSWORD.
Generated API readings validate software; they are not physical measurements.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

BASE = os.getenv('DORMENERGY_BASE_URL', 'http://127.0.0.1:10003')
if urlparse(BASE).hostname not in ('127.0.0.1', 'localhost'):
    raise SystemExit('Run only against an isolated local test deployment.')
OWNER = os.environ['TEST_OWNER_PASSWORD']
TEMPORARY = os.environ['TEST_TENANT_PASSWORD']
PRIVATE = 'BrowserTenantPrivateOnly!2026'
RESET = 'BrowserTenantResetOnly!2026'
ARTIFACTS = Path('artifacts')
ARTIFACTS.mkdir(exist_ok=True)


def sign_in(page, email, password):
    page.context.clear_cookies()
    page.goto(BASE+'/login')
    page.fill('#email', email)
    page.fill('#password', password)
    page.get_by_role('button', name='Sign in to workspace').click()


def own_password(page, current, new):
    page.wait_for_selector('#current-password')
    page.fill('#current-password', current)
    page.fill('#new-password', new)
    page.fill('#confirm-password', new)
    page.get_by_role('button', name='Change password', exact=True).click()
    page.wait_for_selector('#unit-cards .unit-card')


def confirm(page):
    page.locator('#confirm-action').click()
    page.wait_for_function('() => !document.querySelector("#confirm-dialog").open')


def main():
    checks, errors = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_EXECUTABLE', '/usr/bin/chromium'))
        admin_context = browser.new_context(viewport={'width':1440,'height':1050}, reduced_motion='reduce')
        tenant_context = browser.new_context(viewport={'width':390,'height':844}, reduced_motion='reduce')
        admin, tenant = admin_context.new_page(), tenant_context.new_page()
        for page in (admin, tenant):
            page.on('pageerror', lambda error:errors.append(str(error)))
            page.on('console', lambda message:errors.append(message.text) if message.type=='error' else None)
        admin.goto(BASE+'/setup')
        admin.fill('#setup-token',os.environ['ADMIN_SETUP_TOKEN'])
        admin.fill('#account-name','Validation owner')
        admin.fill('#account-email','owner@validation.example')
        admin.fill('#new-password',OWNER)
        admin.fill('#confirm-password',OWNER)
        admin.get_by_role('button',name='Create administrator').click()
        admin.wait_for_function('() => typeof state!=="undefined" && state && state.units.length===0')
        assert admin.locator('#mode-pill').inner_text()=='Live workspace'
        assert admin.locator('#stat-power').inner_text()=='—'
        assert admin.locator('#stat-energy').inner_text()=='—'
        assert admin_context.request.get(BASE+'/health').json()['mode']=='hardware'
        admin.screenshot(path=ARTIFACTS/'real-empty-workspace.png',full_page=True)
        checks.append('Owner setup and an empty real workspace with no fabricated measurements')
        for view in ('units','consumption','loads','transactions','forecast','devices','notifications','settings'):
            admin.locator(f'.nav-link[data-view="{view}"]').click()
            assert admin.locator(f'#view-{view}').is_visible()
        admin.locator('.nav-link[data-view="units"]').click()
        for index in (1,2):
            admin.locator('#view-units [data-open="unit-dialog"]').click()
            admin.fill('#unit-name',f'Validation unit {index}')
            admin.fill('#unit-room',f'Validation room {index}')
            admin.fill('#device-id',f'VALIDATION-METER-{index}')
            admin.locator('#unit-form').get_by_role('button',name='Register unit',exact=True).click()
            admin.wait_for_function('() => !document.querySelector("#unit-dialog").open')
            admin.locator(f'[data-edit-unit="{index}"]').wait_for()
        admin.locator('[data-edit-unit="1"]').click()
        admin.fill('#edit-unit-name','Validation room A')
        admin.fill('#edit-unit-room','First floor')
        admin.fill('#edit-unit-threshold','75.50')
        admin.get_by_role('button',name='Save unit',exact=True).click()
        admin.wait_for_function('() => !document.querySelector("#edit-unit-dialog").open')
        admin.wait_for_function('() => state.units[0].name==="Validation room A"')
        checks.append('All navigation, unit registration, and unit/threshold editing')
        admin.locator('[data-assign="1"]').click()
        admin.fill('#tenant-name','Validation tenant')
        admin.fill('#tenant-email','tenant@validation.example')
        admin.locator('#tenant-registration-method').select_option('temporary_password')
        admin.fill('#tenant-password',TEMPORARY)
        admin.get_by_role('button',name='Create tenant account').click()
        admin.wait_for_function('() => !document.querySelector("#tenant-dialog").open')
        admin.locator('[data-manage-tenant="1"]').wait_for()
        sign_in(tenant,'tenant@validation.example',TEMPORARY)
        own_password(tenant,TEMPORARY,PRIVATE)
        assert tenant_context.request.get(BASE+'/api/snapshot?unit_id=2').status==403
        assert tenant_context.request.get(BASE+'/api/snapshot').json()['units'][0]['source']=='hardware'
        checks.append('Tenant creation, mandatory private password, and unit isolation')
        admin.locator('.nav-link[data-view="settings"]').click()
        admin.fill('#rate','12.50')
        admin.get_by_role('button',name='Review rate change').click()
        confirm(admin)
        admin.wait_for_function('() => state.rate!==null')
        tenant.get_by_role('button',name='Request load',exact=True).click()
        tenant.fill('#load-amount','100')
        tenant.fill('#load-note','Isolated software validation credit')
        tenant.get_by_role('button',name='Send request').click()
        tenant.wait_for_function('() => !document.querySelector("#load-dialog").open')
        admin.reload()
        admin.wait_for_function('() => state && state.requests.length===1')
        admin.locator('.nav-link[data-view="loads"]').click()
        admin.get_by_role('button',name='Approve',exact=True).click()
        confirm(admin)
        admin.wait_for_function('() => state.requests[0].status==="approved"')
        assert float(admin_context.request.get(BASE+'/api/snapshot?unit_id=1').json()['units'][0]['balance'])==100
        checks.append('Actual tariff configuration and tenant request/credit approval')
        admin.locator('.nav-link[data-view="devices"]').click()
        admin.locator('[data-device-token="1"]').click()
        admin.fill('#device-admin-password',OWNER)
        admin.get_by_role('button',name='Apply credential change').click()
        admin.locator('#device-secret-dialog').wait_for(state='visible')
        token=admin.locator('#device-secret-value').input_value()
        assert token and admin.locator('#device-endpoint').input_value()==BASE+'/api/iot/readings'
        admin.locator('#device-secret-dialog').get_by_role('button',name='Done').click()
        assert admin.locator('#device-secret-value').input_value()==''
        first=datetime.now(timezone.utc)
        payload=dict(device_id='VALIDATION-METER-1',unit_id=1,meter_epoch='test-only',event_id='validation-baseline',
            timestamp=first.isoformat(),voltage='230',current='1',power_w='230',energy_kwh='10',connection_status='connected')
        headers={'X-Device-ID':'VALIDATION-METER-1','Authorization':'Bearer '+token}
        assert admin_context.request.post(BASE+'/api/iot/readings',data=payload,headers=headers).status==201
        admin.wait_for_timeout(20000)
        payload.update(timestamp=datetime.now(timezone.utc).isoformat(),energy_kwh='10.01',event_id='validation-increment')
        assert admin_context.request.post(BASE+'/api/iot/readings',data=payload,headers=headers).status==201
        snapshot=admin_context.request.get(BASE+'/api/snapshot?unit_id=1').json()
        assert snapshot['units'][0]['online'] and float(snapshot['units'][0]['balance'])==99.875
        export=admin_context.request.get(BASE+'/api/transactions/export?unit_id=1')
        assert export.status==200 and 'consumption' in export.text()
        checks.append('One-time gateway credentials, authenticated reading ingestion, decimal billing, and CSV export')
        admin.locator('#unit-filter').select_option('1')
        admin.wait_for_function('() => state.units.length===1')
        admin.locator('.nav-link[data-view="forecast"]').click()
        admin.get_by_role('button',name='Train XGBoost',exact=True).click()
        admin.wait_for_function('() => state.forecast.job && state.forecast.job.status==="failed"',timeout=60000)
        assert '30 days' in admin.locator('#forecast-job').inner_text()
        assert admin.locator('#forecast-errors').inner_text()=='Not evaluated'
        assert not admin.locator('#forecast-future-card').is_visible()
        checks.append('XGBoost rejects insufficient real history without inventing predictions or metrics')
        tenant.goto('about:blank')
        admin.locator('.nav-link[data-view="units"]').click()
        admin.locator('[data-manage-tenant="1"]').click()
        admin.fill('#reset-tenant-password',RESET)
        admin.get_by_role('button',name='Reset password',exact=True).click()
        admin.wait_for_function('() => !document.querySelector("#manage-tenant-dialog").open')
        assert tenant_context.request.get(BASE+'/api/snapshot').status==401
        sign_in(tenant,'tenant@validation.example',RESET)
        own_password(tenant,RESET,PRIVATE)
        tenant.goto('about:blank')
        admin.locator('[data-manage-tenant="1"]').click()
        admin.get_by_role('button',name='End tenancy',exact=True).click()
        confirm(admin)
        admin.locator('[data-assign="1"]').wait_for()
        admin.locator('[data-assign="1"]').click()
        admin.fill('#tenant-name','Next validation tenant')
        admin.fill('#tenant-email','next@validation.example')
        admin.locator('#tenant-registration-method').select_option('temporary_password')
        admin.fill('#tenant-password',TEMPORARY)
        admin.get_by_role('button',name='Create tenant account').click()
        admin.wait_for_function('() => !document.querySelector("#tenant-dialog").open')
        sign_in(tenant,'next@validation.example',TEMPORARY)
        own_password(tenant,TEMPORARY,PRIVATE)
        assert tenant_context.request.get(BASE+'/api/snapshot').json()['requests']==[]
        checks.append('Administrator reset, session revocation, tenant turnover, and previous-request privacy')
        tenant.goto('about:blank')
        admin.locator('[data-remove="1"]').click()
        confirm(admin)
        admin.locator('#unit-status').select_option('archived')
        admin.locator('[data-restore="1"]').wait_for()
        admin.locator('[data-restore="1"]').click()
        confirm(admin)
        admin.locator('#unit-status').select_option('active')
        admin.locator('[data-remove="1"]').wait_for()
        sign_in(tenant,'next@validation.example',PRIVATE)
        tenant.wait_for_selector('#unit-cards .unit-card')
        admin.locator('.nav-link[data-view="devices"]').click()
        admin.locator('[data-device-token="1"]').click()
        admin.locator('#device-token-action').select_option('revoke')
        admin.fill('#device-admin-password',OWNER)
        admin.get_by_role('button',name='Apply credential change').click()
        admin.wait_for_function('() => !document.querySelector("#device-token-dialog").open')
        assert admin_context.request.post(BASE+'/api/iot/readings',data=payload,headers=headers).status==401
        checks.append('Unit archive/restore and gateway credential revocation')
        admin.locator('.nav-link[data-view="overview"]').click()
        admin.screenshot(path=ARTIFACTS/'real-admin-desktop.png',full_page=True)
        admin.set_viewport_size({'width':390,'height':844})
        assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth')
        admin.get_by_role('button',name='Open navigation',exact=True).click()
        admin.locator('.nav-link[data-view="units"]').click()
        assert admin.locator('[data-edit-unit="1"]').is_visible()
        admin.screenshot(path=ARTIFACTS/'real-units-mobile.png',full_page=True)
        tenant.screenshot(path=ARTIFACTS/'real-tenant-mobile.png',full_page=True)
        assert tenant.evaluate('document.documentElement.scrollWidth<=innerWidth')
        admin.goto(BASE+'/account')
        admin.fill('#account-name','Updated validation owner')
        admin.fill('#account-email','updated-owner@validation.example')
        admin.fill('#current-password',OWNER)
        admin.get_by_role('button',name='Save account',exact=True).click()
        admin.wait_for_url(BASE+'/account')
        assert admin.locator('#account-email').input_value()=='updated-owner@validation.example'
        assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth')
        checks.append('Mobile admin/tenant layouts and account profile editing')
        assert not errors, errors
        checks.append('No JavaScript or browser console errors')
        result=dict(checks_passed=len(checks),checks=checks,errors=errors,physical_meter_test=False)
        (ARTIFACTS/'browser-real-smoke.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
        browser.close()


if __name__=='__main__':
    main()
