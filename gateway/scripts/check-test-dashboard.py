"""Optional Chromium rendering check, accepts disposable fixture credentials on stdin."""
import ipaddress
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright

options = json.load(sys.stdin)
origin = options['origin']; parsed = urlsplit(origin)
if parsed.scheme != 'http' or not ipaddress.ip_address(parsed.hostname).is_loopback or parsed.username or parsed.password or parsed.path:
    raise RuntimeError('Browser tests require a plain HTTP loopback isolated fixture server.')
artifacts = Path(options['artifacts']); artifacts.mkdir(parents=True, exist_ok=True)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=os.getenv('GATEWAY_CHROMIUM_PATH') or None, headless=True)
    try:
        for index, email in enumerate(['owner@example.test', 'tenant1@example.test', 'tenant2@example.test', 'tenant3@example.test']):
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            def boundary(route):
                if urlsplit(route.request.url).netloc == parsed.netloc: route.continue_()
                else: route.abort()
            context.route('**/*', boundary)
            page = context.new_page(); errors = []
            page.on('pageerror', lambda error: errors.append(type(error).__name__))
            page.goto(origin+'/login')
            page.locator('[name=email]').fill(email); page.locator('[name=password]').fill(options['password'])
            page.get_by_role('button', name='Sign in to workspace').click()
            page.wait_for_url('**/dashboard')
            expect(page.locator('#unit-count')).to_have_text('3' if index == 0 else '1')
            assert 'Live workspace' in page.locator('#mode-pill').inner_text()
            assert ('2,700' if index == 0 else '900') in page.locator('#stat-balance').inner_text()
            assert page.locator('.unit-card').count() == (3 if index == 0 else 1)
            for view in ('consumption', 'transactions', 'devices', 'forecast'):
                page.locator(f'.nav-link[data-view={view}]').click()
                assert page.locator('#breadcrumb-title').inner_text()
            assert not errors, 'Browser script errors occurred.'
            page.screenshot(path=str(artifacts/f'gateway-dashboard-{index}.png'), full_page=True)
            context.close()
    finally:
        browser.close()
print('Chromium rendered the administrator and three tenant dashboards with isolated meter data and no script errors.')
