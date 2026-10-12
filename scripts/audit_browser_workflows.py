"""Run existing mutating browser workflows against fresh disposable loopback apps.

Run with the Playwright-equipped interpreter; the app uses the project .venv.
No retained .env/database, hosting variables or actual email provider are changed.
"""
import json
import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workflow',choices=('all','real','email'),default='all')
    options=parser.parse_args()
    reports = {}
    for kind in (('real','email') if options.workflow=='all' else (options.workflow,)):
        with tempfile.TemporaryDirectory(prefix='dormenergy-browser-audit-') as temporary:
            with socket.socket() as sock: sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            origin=f'http://127.0.0.1:{port}'
            env=dict(os.environ,DATABASE_URL='sqlite:///'+str(Path(temporary)/'browser_test'),SECRET_KEY=secrets.token_urlsafe(32),
                ADMIN_SETUP_TOKEN=secrets.token_urlsafe(32),TEST_OWNER_PASSWORD=secrets.token_urlsafe(24),TEST_TENANT_PASSWORD=secrets.token_urlsafe(24),
                SIMULATION_MODE='false',SESSION_COOKIE_SECURE='false',UTILITY_RATE_LOCKED='false',UTILITY_RATE_CONFIRMED='false',
                EMAIL_BACKEND='memory' if kind=='email' else 'disabled',ALLOW_TEST_EMAIL='true',
                EMAIL_FROM='DormEnergy <no-reply@example.test>',PUBLIC_BASE_URL=origin,DORMENERGY_BASE_URL=origin,
                EMAIL_ENCRYPTION_KEY='',RESEND_API_KEY='',FLASK_DEBUG='0')
            upgraded=subprocess.run([str(ROOT/'.venv/bin/python'),'-m','flask','--app','wsgi','db','upgrade'],cwd=ROOT,env=env,capture_output=True)
            assert upgraded.returncode==0
            artifacts=ROOT/'artifacts/audit';artifacts.mkdir(parents=True,exist_ok=True)
            with open(artifacts/f'legacy-{kind}-browser-server.log','wb') as log:
                server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-m','gunicorn','--bind',f'127.0.0.1:{port}','--workers','1','wsgi:app'],cwd=ROOT,env=env,stdout=log,stderr=log)
                forecaster=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-m','flask','--app','wsgi','forecast-worker','--interval','5'],cwd=ROOT,env=env,stdout=log,stderr=log)
                emailer=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-m','flask','--app','wsgi','email-worker','--interval','5'],cwd=ROOT,env=env,stdout=log,stderr=log)
                try:
                    with sync_playwright() as playwright:
                        request=playwright.request.new_context()
                        for _ in range(50):
                            try:
                                if request.get(origin+'/health',timeout=1000).ok:
                                    assert forecaster.poll() is None, 'Isolated forecasting worker exited.'
                                    assert emailer.poll() is None, 'Isolated email worker exited.'
                                    break
                            except Exception:
                                if server.poll() is not None:raise RuntimeError('Isolated browser service exited.')
                                time.sleep(.1)
                        else:raise RuntimeError('Isolated browser service did not become ready.')
                        request.dispose()
                    result=subprocess.run([sys.executable,str(ROOT/f'scripts/browser_{kind}_smoke.py')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
                    (artifacts/f'legacy-{kind}-browser.log').write_text(result.stdout+result.stderr)
                    assert result.returncode==0, f'Isolated {kind} browser checks failed; inspect the local audit log.'
                    source=ROOT/f'artifacts/browser-{kind}-smoke.json'
                    reports[kind]=json.loads(source.read_text())
                finally:
                    for process in (server,forecaster,emailer):
                        process.terminate()
                        try:process.wait(10)
                        except subprocess.TimeoutExpired:process.kill();process.wait(5)
    report=dict(software_fixtures_only=True,production_readings=0,real_emails_sent=0,workflows=reports)
    (ROOT/'artifacts/audit/browser-workflows.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
