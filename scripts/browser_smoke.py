"""Optional real-browser smoke test against a RUNNING SIMULATION development app.

Install Playwright separately and set DEMO_PASSWORD. This creates a PHP 100
sample load request and approves it; use only an isolated development database.
"""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = os.getenv('DORMENERGY_BASE_URL', 'http://127.0.0.1:5000')
PASSWORD = os.environ['DEMO_PASSWORD']
ARTIFACTS = Path('artifacts')
ARTIFACTS.mkdir(exist_ok=True)


def sign_in(page, email):
    page.goto(BASE + '/login')
    page.fill('#email', email)
    page.fill('#password', PASSWORD)
    page.get_by_role('button', name='Sign in to workspace').click()
    page.wait_for_selector('#unit-cards .unit-card')


def main():
    checks, errors = [], []
    with sync_playwright() as playwright:
        launch_options = {'headless': True}
        if os.getenv('CHROMIUM_EXECUTABLE'):
            launch_options['executable_path'] = os.environ['CHROMIUM_EXECUTABLE']
        browser = playwright.chromium.launch(**launch_options)
        admin_context = browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce')
        tenant_context = browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce')
        admin = admin_context.new_page()
        tenant = tenant_context.new_page()
        for page in (admin, tenant):
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        admin.goto(BASE + '/login')
        admin.screenshot(path=ARTIFACTS/'login-desktop.png', full_page=True)
        sign_in(admin, 'admin@dormenergy.test')
        assert admin.locator('#unit-cards .unit-card').count() == 3
        assert admin.locator('#mode-pill').inner_text() == 'Simulation environment'
        admin.screenshot(path=ARTIFACTS/'admin-desktop.png', full_page=True)
        checks.append('Admin dashboard shows three clearly simulated units')
        for view in ['units','consumption','loads','transactions','forecast','devices','notifications','settings']:
            admin.locator(f'.nav-link[data-view="{view}"]').click()
            assert admin.locator(f'#view-{view}').is_visible()
        admin.locator('.nav-link[data-view="overview"]').click()
        admin.locator('#unit-filter').select_option('1')
        admin.wait_for_function("() => document.querySelectorAll('#unit-cards .unit-card').length===1")
        admin.locator('#unit-filter').select_option('')
        admin.wait_for_function("() => document.querySelectorAll('#unit-cards .unit-card').length===3")
        checks.append('All admin navigation and scoped unit filter work')
        sign_in(tenant, 'tenant1@dormenergy.test')
        assert tenant.locator('#unit-cards .unit-card').count() == 1
        assert tenant.locator('.nav-link[data-view="units"]').count() == 0
        assert tenant_context.request.get(BASE+'/api/snapshot?unit_id=2').status == 403
        tenant.screenshot(path=ARTIFACTS/'tenant-desktop.png',full_page=True)
        checks.append('Tenant dashboard and API enforce unit isolation')
        before = tenant_context.request.get(BASE+'/api/snapshot').json()
        balance_before = float(before['summary']['balance'])
        tenant.get_by_role('button',name='Request load',exact=True).click()
        tenant.locator('[data-amount="100"]').click()
        tenant.fill('#load-note','Browser smoke test · simulated credit only')
        tenant.get_by_role('button',name='Send request').click()
        tenant.wait_for_selector('#view-loads',state='visible')
        submitted = tenant_context.request.get(BASE+'/api/snapshot').json()['requests'][0]
        assert submitted['status'] == 'pending'
        admin.reload()
        admin.wait_for_selector('#unit-cards .unit-card')
        admin.locator('.nav-link[data-view="loads"]').click()
        row = admin.locator('#loads-table tr').filter(has_text=submitted['reference'])
        row.get_by_role('button',name='Approve',exact=True).click()
        admin.get_by_role('button',name='Confirm',exact=True).click()
        admin.wait_for_function("() => !document.querySelector('#confirm-dialog').open")
        row.locator('.badge-approved').wait_for()
        tenant.reload()
        tenant.wait_for_selector('#unit-cards .unit-card')
        after = tenant_context.request.get(BASE+'/api/snapshot').json()
        assert float(after['summary']['balance']) > balance_before + 99
        assert next(r for r in after['requests'] if r['id'] == submitted['id'])['status'] == 'approved'
        checks.append('Tenant request → admin confirmation → credit and tenant status update')
        first = after['units'][0]['reading_at']
        tenant.wait_for_timeout(6500)
        latest = tenant_context.request.get(BASE+'/api/snapshot').json()['units'][0]['reading_at']
        assert first != latest
        checks.append('Independent simulator continuously updates persisted readings')
        tenant.set_viewport_size({'width':390,'height':844})
        tenant.screenshot(path=ARTIFACTS/'tenant-mobile.png',full_page=True)
        assert tenant.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        tenant.get_by_role('button',name='Open navigation',exact=True).click()
        tenant.locator('.nav-link[data-view="transactions"]').click()
        assert tenant.locator('#view-transactions').is_visible()
        assert tenant.locator('#mobile-menu').get_attribute('aria-expanded') == 'false'
        checks.append('390px mobile layout, sidebar navigation, and transaction view work')
        admin.set_viewport_size({'width':1024,'height':768})
        admin.locator('.nav-link[data-view="overview"]').click()
        admin.screenshot(path=ARTIFACTS/'admin-tablet.png',full_page=True)
        assert admin.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        checks.append('1024px tablet dashboard has no horizontal page overflow')
        assert not errors, errors
        checks.append('No JavaScript or browser console errors')
        result={'checks_passed':len(checks),'checks':checks,'errors':errors,'sample_request_id':submitted['id']}
        (ARTIFACTS/'browser-smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2))
        browser.close()


if __name__ == '__main__':
    main()
