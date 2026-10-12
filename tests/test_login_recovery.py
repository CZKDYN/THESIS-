"""A stale sign-in form must recover without accepting or replaying its credentials."""
import re

import pytest
from sqlalchemy import func, select

from conftest import PASSWORD
from dormenergy.extensions import db
from dormenergy.models import AuditLog, Unit, User


def form_token(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)


@pytest.mark.parametrize('failure', ['missing', 'invalid', 'expired', 'key_changed', 'cookie_lost'])
def test_rejected_login_gets_fresh_form_without_authentication(app, client, failure):
    old = form_token(client.get('/login'))
    data = {'email': 'admin@test.local', 'password': PASSWORD, 'csrf_token': old}
    if failure == 'missing':
        data.pop('csrf_token')
    elif failure == 'invalid':
        data['csrf_token'] = 'invalid-token'
    elif failure == 'expired':
        app.config['WTF_CSRF_TIME_LIMIT'] = -1
    elif failure == 'key_changed':
        app.config['SECRET_KEY'] = 'changed-signing-key-for-this-test-only'
    elif failure == 'cookie_lost':
        client.delete_cookie(app.config['SESSION_COOKIE_NAME'])

    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == 'admin@test.local'))
        original_password, original_version = user.password_hash, user.session_version
        original_audits = db.session.scalar(select(func.count(AuditLog.id)))
    rejected = client.post('/login', data=data)
    assert rejected.status_code == 303
    assert rejected.headers['Location'] == '/login'
    assert PASSWORD not in rejected.get_data(as_text=True)
    assert 'admin@test.local' not in rejected.headers['Location']
    assert client.get('/api/snapshot').status_code == 401
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == 'admin@test.local'))
        assert (user.password_hash, user.session_version) == (original_password, original_version)
        assert db.session.scalar(select(func.count(AuditLog.id))) == original_audits

    app.config['WTF_CSRF_TIME_LIMIT'] = 3600
    fresh = client.get(rejected.headers['Location'])
    assert fresh.status_code == 200
    assert fresh.headers['Cache-Control'] == 'no-store'
    assert 'Your sign-in page expired. Please sign in again.' in fresh.get_data(as_text=True)
    assert PASSWORD not in fresh.get_data(as_text=True)
    new = form_token(fresh)
    assert new != old
    accepted = client.post('/login', data={'email': 'admin@test.local', 'password': PASSWORD, 'csrf_token': new})
    assert accepted.status_code == 302
    assert accepted.headers['Location'] == '/dashboard'
    assert client.get('/api/snapshot').json['user']['role'] == 'admin'


def test_other_requests_keep_csrf_rejection_without_mutation(app, client):
    token = form_token(client.get('/login'))
    assert client.post('/login', data={'email': 'admin@test.local', 'password': PASSWORD, 'csrf_token': token}).status_code == 302
    response = client.post('/api/units', json={'name': 'Unauthorized', 'room': 'No room', 'device_id': 'NO-CSRF'})
    assert response.status_code == 400
    assert response.is_json
    assert client.post('/account/preferences', data={'email_balance_alerts': 'off'}).status_code == 400
    with app.app_context():
        assert db.session.scalar(select(func.count(Unit.id))) == 2
        user = db.session.scalar(select(User).where(User.email == 'admin@test.local'))
        assert user.email_balance_alerts and user.email_load_updates
