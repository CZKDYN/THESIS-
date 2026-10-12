import hashlib
import json
import re
import threading
import urllib.error
from datetime import timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from dormenergy.extensions import db
from dormenergy.models import Account, AccountToken, EmailOutbox, LoadRequest, Unit, User, utcnow
from dormenergy.services.billing import DomainError, ingest_reading, resolve_load
from dormenergy.services.emailing import (DeliveryError, cancel_user_mail, configuration_error, decrypt_payload,
    email_worker_step, issue_link, provider_message_key, queue_email, rate_limit, resend_send, valid_link)
from conftest import PASSWORD, csrf_token, login, meter_payload, post, transmit


def configure(app):
    app.config.update(SIMULATION_MODE=False,EMAIL_BACKEND='memory',PUBLIC_BASE_URL='https://dormenergy.example',
                      EMAIL_FROM='DormEnergy <no-reply@example.test>')
    with app.app_context():
        db.session.get(Unit,1).is_demo=False
        db.session.scalar(select(User).where(User.email=='one@test.local')).created_at=utcnow()-timedelta(days=1)
        db.session.commit()


def raw_link(row):
    return re.search(r'#token=([A-Za-z0-9_-]{43})',decrypt_payload(row)['text']).group(1)


def invite(app, client, address='invited@example.com'):
    configure(app)
    login(client)
    unit=post(client,'/api/units',dict(name='Invited unit',room='Room 400',device_id='INVITE-METER')).json['id']
    response=post(client,f'/api/units/{unit}/tenant',dict(name='Invited tenant',email=address))
    assert response.status_code==201
    with app.app_context():
        user=db.session.scalar(select(User).where(User.email==address))
        row=db.session.scalar(select(EmailOutbox).where(EmailOutbox.user_id==user.id))
        return unit,user.id,row.id,raw_link(row)


def link_post(client,path,raw,new='TenantPrivateEmailPassword!2026'):
    token=re.search(r'name="csrf-token" content="([^"]+)"',client.get(path).get_data(as_text=True)).group(1)
    return client.post(path,json=dict(token=raw,new_password=new,confirm_password=new),headers={'X-CSRFToken':token})


def test_pending_invitation_encrypted_message_activation_and_single_use(app,client):
    unit,user_id,row_id,raw=invite(app,client)
    tenant=app.test_client()
    assert login(tenant,'invited@example.com',PASSWORD).status_code==401
    with app.app_context():
        user=db.session.get(User,user_id)
        row=db.session.get(EmailOutbox,row_id)
        token=db.session.scalar(select(AccountToken).where(AccountToken.user_id==user_id))
        assert not user.active and user.activation_status=='pending'
        assert token.token_hash==hashlib.sha256(raw.encode()).hexdigest() and raw not in row.payload_encrypted
        assert PASSWORD not in decrypt_payload(row)['text'] and 'Room 400' in decrypt_payload(row)['html']
        assert row.status=='pending' and row.sent_at is None
        assert email_worker_step()
        assert db.session.get(EmailOutbox,row_id).status=='test'
        assert len(app.extensions['test_email_messages'])==1
    assert link_post(tenant,'/activate',raw).status_code==200
    assert link_post(tenant,'/activate',raw).status_code==403
    assert login(tenant,'invited@example.com','TenantPrivateEmailPassword!2026').status_code==302
    assert tenant.get('/api/snapshot').json['units'][0]['id']==unit
    assert tenant.get('/api/snapshot?unit_id=1').status_code==403
    with app.app_context():
        assert db.session.get(User,user_id).email_verified_at and db.session.get(User,user_id).activation_status=='active'


def test_invalid_expired_wrong_purpose_and_wrong_unit_links(app,client):
    unit,user_id,row_id,raw=invite(app,client)
    tenant=app.test_client()
    assert link_post(tenant,'/activate','x'*43).status_code==403
    assert link_post(tenant,'/reset-password',raw).status_code==403
    with app.app_context():
        token=db.session.scalar(select(AccountToken).where(AccountToken.user_id==user_id))
        token.unit_id=1
        db.session.commit()
    assert link_post(tenant,'/activate',raw).status_code==403
    with app.app_context():
        token=db.session.scalar(select(AccountToken).where(AccountToken.user_id==user_id))
        token.unit_id=unit
        token.expires_at=utcnow()-timedelta(seconds=1)
        db.session.commit()
    assert link_post(tenant,'/activate',raw).status_code==403
    with app.app_context():
        email_worker_step()
        assert db.session.get(EmailOutbox,row_id).status=='cancelled'
        assert not db.session.get(User,user_id).active


def test_safe_invitation_resend_invalidates_old_link_and_limits_requests(app,client):
    unit,user_id,row_id,raw=invite(app,client)
    assert post(client,f'/api/units/{unit}/tenant/invitation',{}).status_code==429
    with app.app_context():
        db.session.scalar(select(AccountToken).where(AccountToken.user_id==user_id)).created_at=utcnow()-timedelta(seconds=61)
        db.session.commit()
    assert post(client,f'/api/units/{unit}/tenant/invitation',{}).status_code==200
    with app.app_context():
        old=db.session.get(EmailOutbox,row_id)
        assert old.status=='cancelled'
        new=db.session.scalar(select(EmailOutbox).where(EmailOutbox.user_id==user_id).order_by(EmailOutbox.id.desc()).limit(1))
        replacement=raw_link(new)
        assert replacement!=raw
    tenant=app.test_client()
    assert link_post(tenant,'/activate',raw).status_code==403
    assert link_post(tenant,'/activate',replacement).status_code==200
    assert post(client,f'/api/units/{unit}/tenant/invitation',{}).status_code==409


def test_missing_provider_is_explicit_and_account_does_not_become_active(app,client):
    configure(app)
    app.config['EMAIL_BACKEND']='disabled'
    login(client)
    unit=post(client,'/api/units',dict(name='Pending room',room='North wing',device_id='PENDING-METER')).json['id']
    response=post(client,f'/api/units/{unit}/tenant',dict(name='Pending tenant',email='pending@example.com'))
    assert response.status_code==201 and response.json['email_delivery']=='failed'
    assert 'not sent' in response.json['message']
    data=client.get('/api/snapshot').json['units'][-1]
    assert data['invitation_status']=='Pending' and data['email_delivery']=='failed'
    with app.app_context():
        assert not db.session.scalar(select(User).where(User.email=='pending@example.com')).active
        assert not email_worker_step()


def test_generic_password_recovery_revokes_sessions_and_used_tokens(app,client):
    configure(app)
    other=app.test_client()
    login(other)
    token=csrf_token(client)
    existing=client.post('/forgot-password',data={'email':'admin@test.local','csrf_token':token})
    unknown=client.post('/forgot-password',data={'email':'unknown@example.com','csrf_token':token})
    # CSRF fields can vary; the public confirmation and status are identical.
    assert existing.status_code==unknown.status_code==200
    message='If an eligible account matches this email'
    assert message in existing.get_data(as_text=True) and message in unknown.get_data(as_text=True)
    with app.app_context():
        row=db.session.scalar(select(EmailOutbox).where(EmailOutbox.kind=='reset'))
        raw=raw_link(row)
        assert db.session.scalar(select(func.count(EmailOutbox.id)))==1
    assert link_post(client,'/reset-password',raw,'RecoveredAdministrator!2026').status_code==200
    assert other.get('/api/snapshot').status_code==401
    assert link_post(client,'/reset-password',raw).status_code==403
    assert login(client,'admin@test.local','RecoveredAdministrator!2026').status_code==302
    with app.app_context():
        assert db.session.scalar(select(EmailOutbox).where(EmailOutbox.kind=='security_password')) is not None


def test_recovery_throttling_expiration_and_no_admin_activation(app,client):
    configure(app)
    token=csrf_token(client)
    for _ in range(7):
        assert client.post('/forgot-password',data={'email':'admin@test.local','csrf_token':token}).status_code==200
    with app.app_context():
        assert db.session.scalar(select(func.count(AccountToken.id)))==1
        user=db.session.get(User,1)
        with pytest.raises(DomainError):issue_link(user,'activation')
        row=db.session.scalar(select(EmailOutbox).where(EmailOutbox.kind=='reset'))
        raw=raw_link(row)
        db.session.get(AccountToken,row.token_id).expires_at=utcnow()-timedelta(seconds=1)
        db.session.commit()
    assert link_post(client,'/reset-password',raw).status_code==403
    assert client.get('/setup').status_code==302  # Existing admin still protects setup.


def test_low_balance_crossings_rearm_after_topup_without_per_reading_duplicates(app,client):
    configure(app)
    now=utcnow()
    assert transmit(client,meter_payload(timestamp=now-timedelta(seconds=20))).status_code==201
    assert transmit(client,meter_payload(energy='17',timestamp=now-timedelta(seconds=10),event='cross')).status_code==201
    assert transmit(client,meter_payload(energy='17.1',timestamp=now-timedelta(seconds=5),event='below')).status_code==201
    with app.app_context():
        assert db.session.scalar(select(func.count(EmailOutbox.id)).where(EmailOutbox.kind=='low_balance'))==1
        tenant=db.session.scalar(select(User).where(User.email=='one@test.local'))
        load=LoadRequest(user_id=tenant.id,unit_id=1,amount=50,reference='alert-rearm')
        db.session.add(load);db.session.commit()
        resolve_load(load.id,'approved',db.session.get(User,1));db.session.commit()
    assert transmit(client,meter_payload(energy='21.1',timestamp=now,event='cross-again')).status_code==201
    assert transmit(client,meter_payload(energy='21.1',timestamp=now,event='cross-again')).status_code==409
    with app.app_context():
        assert db.session.scalar(select(func.count(EmailOutbox.id)).where(EmailOutbox.kind=='low_balance'))==2


def test_topup_emails_after_commit_and_rollback_leave_no_email_or_credit(app):
    configure(app)
    with app.app_context():
        tenant=db.session.scalar(select(User).where(User.email=='one@test.local'))
        load=LoadRequest(user_id=tenant.id,unit_id=1,amount=50,reference='rollback-email')
        tenant_id=tenant.id
        db.session.add(load);db.session.commit()
        resolve_load(load.id,'approved',db.session.get(User,1))
        if db.engine.dialect.name=='postgresql':
            with app.app_context():assert not email_worker_step()  # Another DB session cannot see uncommitted outbox rows.
        db.session.rollback()
        assert db.session.get(Account,1).balance==100
        assert db.session.scalar(select(func.count(EmailOutbox.id)))==0
        resolve_load(load.id,'approved',db.session.get(User,1));db.session.commit()
        assert email_worker_step()
        message=app.extensions['test_email_messages'][0]
        assert 'PHP 50.00' in message['text'] and 'PHP 150.00' in message['text'] and 'rollback-email' in message['text']
        second=LoadRequest(user_id=tenant_id,unit_id=1,amount=25,reference='rejected-email')
        db.session.add(second);db.session.commit()
        resolve_load(second.id,'rejected',db.session.get(User,1));db.session.commit()
        assert email_worker_step()
        assert 'rejected' in app.extensions['test_email_messages'][-1]['subject']


def test_provider_failures_retry_with_same_key_and_no_false_sent_state(app):
    configure(app)
    calls=[]
    def fail_once(payload,key):
        calls.append(key)
        if len(calls)==1:raise DeliveryError('provider_temporary',True)
    app.extensions['email_test_transport']=fail_once
    with app.app_context():
        row=queue_email(db.session.get(User,1),'security_password','Password changed','Security notice',dedupe_key='provider-retry')
        key=provider_message_key(row)
        row_id=row.id;db.session.commit()
        assert email_worker_step()
        row=db.session.get(EmailOutbox,row_id)
        assert row.status=='pending' and row.attempts==1 and row.sent_at is None
        retry_at=row.next_attempt_at
        assert not email_worker_step()
        assert email_worker_step(now=retry_at)
        assert db.session.get(EmailOutbox,row_id).status=='test'
        assert calls==[key]*2


def test_permanent_provider_failure_and_interrupted_worker_recovery(app):
    configure(app)
    app.extensions['email_test_transport']=lambda payload,key:(_ for _ in ()).throw(DeliveryError('provider_rejected'))
    with app.app_context():
        row=queue_email(db.session.get(User,1),'security_password','Changed','Notice',dedupe_key='rejected')
        row_id=row.id;db.session.commit()
        email_worker_step()
        assert db.session.get(EmailOutbox,row_id).status=='failed'
        row=queue_email(db.session.get(User,1),'security_password','Changed','Notice',dedupe_key='interrupted')
        row.status='sending';row.lease_id='old-lease';row.leased_until=utcnow()-timedelta(seconds=1);row.attempts=1
        next_id=row.id;db.session.commit()
        app.extensions.pop('email_test_transport')
        email_worker_step()
        assert db.session.get(EmailOutbox,next_id).status=='test'
        assert db.session.get(EmailOutbox,next_id).attempts==2


def test_former_tenant_mail_and_links_cannot_reach_replacement(app,client):
    unit,user_id,row_id,raw=invite(app,client)
    assert post(client,f'/api/units/{unit}/tenant/end',{}).status_code==200
    assert post(client,f'/api/units/{unit}/tenant',dict(name='Replacement',email='replacement@example.com',password=PASSWORD)).status_code==201
    tenant=app.test_client()
    assert link_post(tenant,'/activate',raw).status_code in (403,409)
    with app.app_context():
        assert db.session.get(EmailOutbox,row_id).status=='cancelled'
        assert not email_worker_step()
        assert not app.extensions.get('test_email_messages')
        assert db.session.get(User,user_id).tenancy_ended_at


def test_preferences_suppress_nonessential_emails_but_not_security(app,client):
    configure(app)
    login(client,'one@test.local')
    token=re.search(r'name="csrf-token" content="([^"]+)"',client.get('/account').get_data(as_text=True)).group(1)
    assert client.post('/account/preferences',data={'csrf_token':token}).status_code==302
    with app.app_context():
        tenant=db.session.scalar(select(User).where(User.email=='one@test.local'))
        assert not tenant.email_balance_alerts and not tenant.email_load_updates
        from dormenergy.services.accounts import change_password
        change_password(tenant,'EmailPreferenceOwnPassword!2026');db.session.commit()
        assert db.session.scalar(select(EmailOutbox).where(EmailOutbox.kind=='security_password')) is not None
    now=utcnow()
    assert transmit(client,meter_payload(timestamp=now-timedelta(seconds=20))).status_code==201
    assert transmit(client,meter_payload(energy='17',timestamp=now-timedelta(seconds=10),event='opt-out-cross')).status_code==201
    with app.app_context():assert db.session.scalar(select(func.count(EmailOutbox.id)).where(EmailOutbox.kind=='low_balance'))==0


def test_migrations_unit_creation_and_old_reading_import_do_not_send_mail(app,client):
    configure(app)
    with app.app_context():assert db.session.scalar(select(func.count(EmailOutbox.id)))==0
    login(client)
    assert post(client,'/api/units',dict(name='No initialization email',room='Room',device_id='NO-MAIL')).status_code==201
    with app.app_context():
        assert db.session.scalar(select(func.count(EmailOutbox.id)))==0
        user=db.session.scalar(select(User).where(User.email=='one@test.local'))
        user.created_at=utcnow()-timedelta(days=2);db.session.commit()
        ingest_reading(1,meter_payload(timestamp=utcnow()-timedelta(days=1)),'hardware');db.session.commit()
        ingest_reading(1,meter_payload(energy='17',timestamp=utcnow()-timedelta(hours=23),event='historical-cross'),'hardware');db.session.commit()
        assert db.session.scalar(select(func.count(EmailOutbox.id)))==0


def test_https_origin_and_email_api_never_expose_private_links(app,client):
    unit,user_id,row_id,raw=invite(app,client)
    data=client.get('/api/email-status').json
    assert raw not in json.dumps(data) and 'payload_encrypted' not in json.dumps(data)
    assert client.get('/activate').headers['Referrer-Policy']=='same-origin'
    assert raw not in client.get('/activate').get_data(as_text=True)
    app.config['PUBLIC_BASE_URL']='http://untrusted.example'
    with app.app_context():assert configuration_error()=='configuration'


def test_resend_adapter_shapes_request_and_sanitizes_provider_errors(app,monkeypatch):
    app.config.update(RESEND_API_KEY='TestOnlyFakeProviderKey',EMAIL_FROM='DormEnergy <no-reply@example.test>')
    captured=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return b'{"id":"mock-provider-message"}'
    def transport(request,timeout):
        captured.append(request)
        assert timeout==10
        return Response()
    monkeypatch.setattr('dormenergy.services.emailing._resend_http',transport)
    with app.app_context():
        assert resend_send(dict(to='tenant@example.test',subject='Notice',html='<p>Notice</p>',text='Notice'),'stable-key')=='mock-provider-message'
        assert captured[0].full_url=='https://api.resend.com/emails'
        assert captured[0].get_header('Idempotency-key')=='stable-key'
        assert json.loads(captured[0].data)['text']=='Notice'
        def reject(request,timeout):raise urllib.error.HTTPError(request.full_url,503,'sensitive provider response',{},None)
        monkeypatch.setattr('dormenergy.services.emailing._resend_http',reject)
        with pytest.raises(DeliveryError) as result:resend_send(dict(to='tenant@example.test',subject='Notice',html='Notice',text='Notice'),'stable-key')
        assert result.value.code=='provider_temporary' and result.value.transient


def test_concurrent_workers_claim_one_message_without_duplicates(app):
    configure(app)
    with app.app_context():
        if db.engine.dialect.name!='postgresql':pytest.skip('Worker row locks require PostgreSQL.')
        row=queue_email(db.session.get(User,1),'security_password','Changed','Notice',dedupe_key='one-worker-claim')
        key=provider_message_key(row)
        row_id=row.id;db.session.commit()
    entered,release=threading.Event(),threading.Event()
    calls=[]
    def hold(payload,key):
        calls.append(key);entered.set();assert release.wait(5)
    app.extensions['email_test_transport']=hold
    def step():
        with app.app_context():return email_worker_step()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(step)
        assert entered.wait(5)
        assert not pool.submit(step).result(timeout=5)
        release.set();assert first.result(timeout=5)
    assert calls==[key]


def test_concurrent_token_redemption_succeeds_once(app,client):
    unit,user_id,row_id,raw=invite(app,client)
    with app.app_context():
        if db.engine.dialect.name!='postgresql':pytest.skip('Token row locks require PostgreSQL.')
    def redeem():
        local=app.test_client()
        return link_post(local,'/activate',raw).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(lambda _:redeem(),range(2)))
    assert sorted(outcomes)==[200,403]


def test_admin_welcome_and_email_change_security_notices_preserve_setup_protection(app,client):
    configure(app)
    app.config['ADMIN_SETUP_TOKEN']='AdministratorSetupTestOnlyToken'
    with app.app_context():
        db.session.delete(db.session.get(User,1));db.session.commit()
    token=csrf_token(client)
    assert client.post('/setup',data=dict(csrf_token=token,setup_token=app.config['ADMIN_SETUP_TOKEN'],name='New owner',
        email='owner@example.com',new_password=PASSWORD,confirm_password=PASSWORD)).status_code==302
    with app.app_context():
        row=db.session.scalar(select(EmailOutbox).where(EmailOutbox.kind=='security_welcome'))
        assert row and app.config['ADMIN_SETUP_TOKEN'] not in decrypt_payload(row)['text'] and PASSWORD not in decrypt_payload(row)['text']
    token=re.search(r'name="csrf-token" content="([^"]+)"',client.get('/account').get_data(as_text=True)).group(1)
    assert client.post('/account',data=dict(csrf_token=token,name='Owner',email='changed@example.com',current_password=PASSWORD)).status_code==302
    with app.app_context():
        rows=db.session.scalars(select(EmailOutbox).where(EmailOutbox.kind.in_(['security_email','security_email_old']))).all()
        assert sorted(decrypt_payload(row)['to'] for row in rows)==['changed@example.com','owner@example.com']
    token=re.search(r'name="csrf-token" content="([^"]+)"',client.get('/account').get_data(as_text=True)).group(1)
    assert client.post('/account',data=dict(csrf_token=token,name='Owner',email='another@example.com',current_password=PASSWORD)).status_code==302
    with app.app_context():
        historical=db.session.scalars(select(EmailOutbox).where(EmailOutbox.kind=='security_email_old')).all()
        assert len(historical)==2 and all(row.status=='pending' for row in historical)
    assert client.get('/setup').status_code==302


def test_mocked_provider_acceptance_is_sent_but_not_inbox_delivery(app,monkeypatch):
    configure(app)
    app.testing=False
    app.config.update(EMAIL_BACKEND='resend',RESEND_API_KEY='MockOnlyNeverRealProviderKey')
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return b'{"id":"mock-provider-accepted"}'
    monkeypatch.setattr('dormenergy.services.emailing._resend_http',lambda request,timeout:Response())
    with app.app_context():
        row=queue_email(db.session.get(User,1),'security_password','Password changed','Notice',dedupe_key='provider-accepted')
        row_id=row.id;db.session.commit()
        email_worker_step()
        row=db.session.get(EmailOutbox,row_id)
        assert row.status=='sent' and row.sent_at and row.provider_message_id=='mock-provider-accepted'
    app.testing=True


def test_retry_exhaustion_and_safe_idempotency_window_are_terminal(app):
    configure(app)
    app.config['EMAIL_MAX_ATTEMPTS']=1
    app.extensions['email_test_transport']=lambda payload,key:(_ for _ in ()).throw(DeliveryError('provider_temporary',True))
    with app.app_context():
        row=queue_email(db.session.get(User,1),'security_password','Changed','Notice',dedupe_key='max-retries')
        row_id=row.id;db.session.commit();email_worker_step()
        assert db.session.get(EmailOutbox,row_id).error_code=='retry_exhausted'
        row=queue_email(db.session.get(User,1),'security_password','Changed','Notice',dedupe_key='old-retry')
        row.created_at=utcnow()-timedelta(hours=23)
        old_id=row.id;db.session.commit();email_worker_step()
        assert db.session.get(EmailOutbox,old_id).error_code=='retry_window_expired'


def test_provider_transport_does_not_follow_redirects_or_forward_credentials():
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import urllib.request
    from dormenergy.services.emailing import _resend_http
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header('Location',f'http://localhost:{self.server.server_port}/collector')
            self.end_headers()
        def do_GET(self):
            received.append(self.headers.get('Authorization'))
            self.send_response(200);self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/redirect',data=b'{}',
            headers={'Authorization':'Bearer LocalRedirectTestOnly'})
        with pytest.raises(urllib.error.HTTPError) as error:
            _resend_http(request,timeout=2)
        assert error.value.code==302 and received==[]
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
