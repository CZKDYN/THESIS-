"""Transactional outbox: database commit precedes every provider request."""
import base64
import hashlib
import json
import http.client
import secrets
import urllib.error
import urllib.request
from datetime import timedelta
from email.utils import parseaddr
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, render_template
from sqlalchemy import select, update
from ..extensions import db
from ..models import AccountToken, EmailOutbox, EmailRateLimit, Unit, User, utcnow
from .billing import DomainError, aware, lock_unit

MANILA = ZoneInfo('Asia/Manila')
ERRORS = {
    'email_disabled':'Email delivery is not configured.',
    'configuration':'Check the public HTTPS URL, verified sender, API key, and encryption configuration.',
    'provider_temporary':'Temporary provider failure; delivery will be retried.',
    'provider_rejected':'The provider rejected the request. Check the sender configuration.',
    'retry_exhausted':'Automatic retry limit reached.',
    'retry_window_expired':'Safe provider retry window expired.',
    'account_ineligible':'Account or tenancy changed; delivery cancelled.',
    'link_expired':'The account link expired or was replaced.',
    'worker_interrupted':'Delivery worker was interrupted; retrying with the same message key.',
    'encryption':'The saved message cannot be decrypted. Check key rotation.',
}


class DeliveryError(Exception):
    def __init__(self, code, transient=False):
        self.code, self.transient = code, transient


def cipher():
    key = current_app.config['EMAIL_ENCRYPTION_KEY']
    if not key:
        # Separate purpose from session signing; a dedicated stable key is recommended.
        key = base64.urlsafe_b64encode(hashlib.sha256(
            ('DormEnergy/email-outbox/v1:'+current_app.config['SECRET_KEY']).encode()).digest())
    return Fernet(key)


def decrypt_payload(row):
    return json.loads(cipher().decrypt(row.payload_encrypted.encode()).decode())


def provider_message_key(row):
    # Stable across retries/restores and unique across different deployments.
    return 'dormenergy-email-'+hashlib.sha256(row.payload_encrypted.encode()).hexdigest()


def public_base_url():
    value = current_app.config['PUBLIC_BASE_URL'].rstrip('/')
    parsed = urlparse(value)
    if parsed.port is not None and not 1<=parsed.port<=65535:
        raise ValueError('Invalid public URL port')
    local = (current_app.testing or (current_app.config['ALLOW_TEST_EMAIL'] and current_app.config['EMAIL_BACKEND']=='memory'))
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path or (parsed.scheme!='https' and not (local and parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1')))):
        raise ValueError('Invalid public URL')
    return value


def configuration_error():
    backend = current_app.config['EMAIL_BACKEND']
    if backend=='disabled':
        return 'email_disabled'
    try:
        public_base_url()
        sender = current_app.config['EMAIL_FROM']
        address = parseaddr(sender)[1]
        if not address or '@' not in address or '\n' in sender or '\r' in sender:
            return 'configuration'
        cipher()
        if backend=='resend' and not current_app.config['RESEND_API_KEY']:
            return 'configuration'
        if backend=='memory' and not (current_app.testing or current_app.config['ALLOW_TEST_EMAIL']):
            return 'configuration'
        if backend not in ('resend','memory'):
            return 'configuration'
    except (ValueError, TypeError):
        return 'configuration'
    return None


def eligible(user, activation=False):
    if not user or (not user.active and not (activation and user.activation_status=='pending')):
        return False
    if activation and (user.role!='tenant' or user.activation_status!='pending'):
        return False
    if not activation and user.activation_status!='active':
        return False
    if user.role=='tenant':
        return bool(user.unit and not user.unit.is_demo and not user.unit.archived_at and not user.tenancy_ended_at)
    return user.role=='admin'


def queue_email(user, kind, heading, message, details=None, action_url=None, action_label='Open DormEnergy',
                dedupe_key=None, token=None, recipient=None):
    """Only writes an encrypted message. Caller owns commit/rollback."""
    if db.session.info.get('suppress_email') or (user.role=='tenant' and user.unit.is_demo):
        return None
    key = dedupe_key or f'{kind}:{uuid4().hex}'
    existing = db.session.scalar(select(EmailOutbox).where(EmailOutbox.dedupe_key==key))
    if existing:
        return existing
    row = EmailOutbox(user_id=user.id, unit_id=user.unit_id, kind=kind, dedupe_key=key,
                      token_id=token.id if token else None, status='pending', attempts=0)
    error = configuration_error()
    try:
        base = public_base_url()
        context = dict(name=user.name, heading=heading, message=message, details=details or [],
                       action_url=action_url or base+'/dashboard', action_label=action_label,
                       website=base, expiration=aware(token.expires_at).astimezone(MANILA).strftime('%b %d, %Y, %I:%M %p Philippine time') if token else None)
        payload = dict(to=recipient or user.email, subject='DormEnergy · '+heading,
                       html=render_template('emails/message.html', **context),
                       text=render_template('emails/message.txt', **context))
        payload['from']=current_app.config['EMAIL_FROM']
        row.payload_encrypted = cipher().encrypt(json.dumps(payload).encode()).decode()
    except (ValueError, TypeError):
        error = 'configuration'
    if error:
        row.status, row.error_code = 'failed', error
    db.session.add(row)
    db.session.flush()
    return row


def security_email(user, event, recipient=None, key=None):
    if current_app.config['EMAIL_BACKEND']=='disabled':
        return None
    headings = {'welcome':'Welcome to DormEnergy', 'password':'Your password was changed',
                'email':'Your account email was changed', 'temporary':'Your administrator reset your password'}
    messages = {'welcome':'Your administrator account has been created. Use the website to manage your dormitory.',
                'password':'Your password was changed and previous sessions were revoked. If you did not make this change, contact your dormitory administrator or use password recovery.',
                'email':'Your registered email address was changed. If you did not make this change, contact your dormitory administrator.',
                'temporary':'Your administrator reset your sign-in password. Contact them securely for access; no password is included in this email.'}
    return queue_email(user, 'security_email_old' if recipient and recipient!=user.email else 'security_'+event,
                       headings[event], messages[event], recipient=recipient,
                       dedupe_key=key or f'security:{event}:{user.id}:{user.session_version}')


def revoke_links(user, purpose=None, keep=None):
    query = select(AccountToken).where(AccountToken.user_id==user.id, AccountToken.consumed_at.is_(None), AccountToken.revoked_at.is_(None))
    if purpose:
        query = query.where(AccountToken.purpose==purpose)
    if keep:
        query = query.where(AccountToken.id!=keep)
    ids=[]
    for token in db.session.scalars(query).all():
        token.revoked_at=utcnow()
        ids.append(token.id)
    if ids:
        db.session.execute(update(EmailOutbox).where(EmailOutbox.token_id.in_(ids),
            EmailOutbox.status.in_(['pending','sending','failed'])).values(status='cancelled',error_code='link_expired',lease_id=None,leased_until=None))


def cancel_user_mail(user,preserve_security_history=False):
    revoke_links(user)
    query=update(EmailOutbox).where(EmailOutbox.user_id==user.id,EmailOutbox.status.in_(['pending','sending']))
    if preserve_security_history:
        query=query.where(EmailOutbox.kind!='security_email_old')
    db.session.execute(query.values(status='cancelled',error_code='account_ineligible',lease_id=None,leased_until=None))


def rate_limit(key, maximum, seconds, now=None):
    now=aware(now or utcnow())
    start=now.fromtimestamp(int(now.timestamp())//seconds*seconds, tz=now.tzinfo)
    digest=hashlib.sha256(key.encode()).hexdigest()
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert=pg_insert if db.engine.dialect.name=='postgresql' else sqlite_insert
    statement=insert(EmailRateLimit).values(limit_key=digest,window_start=start,hits=1).on_conflict_do_update(
        index_elements=['limit_key','window_start'],set_={'hits':EmailRateLimit.hits+1},where=EmailRateLimit.hits<maximum).returning(EmailRateLimit.id)
    return db.session.scalar(statement) is not None


def lock_user(user_id):
    user=db.session.get(User,user_id)
    if not user:
        raise DomainError('This account link is invalid, expired, or already used.',403)
    if user.role=='tenant':
        lock_unit(user.unit_id)
    return db.session.scalar(select(User).where(User.id==user_id).with_for_update().execution_options(populate_existing=True))


def issue_link(user, purpose, now=None):
    now=aware(now or utcnow())
    user=lock_user(user.id)
    if purpose not in ('activation','reset') or not eligible(user,activation=purpose=='activation'):
        raise DomainError('This account is not eligible for this link.',409)
    latest=db.session.scalar(select(AccountToken).where(AccountToken.user_id==user.id,AccountToken.purpose==purpose).order_by(AccountToken.id.desc()).limit(1))
    if latest and (now-aware(latest.created_at)).total_seconds()<max(1,current_app.config['EMAIL_RESEND_COOLDOWN']):
        raise DomainError('Please wait before requesting another account email.',429)
    if not rate_limit(f'link:{purpose}:{user.id}',5,3600,now):
        raise DomainError('Account email request limit reached. Please try again later.',429)
    revoke_links(user,purpose)
    raw=secrets.token_urlsafe(32)
    duration=timedelta(hours=max(1,min(168,current_app.config['EMAIL_ACTIVATION_HOURS']))) if purpose=='activation' else timedelta(minutes=max(1,min(120,current_app.config['EMAIL_RESET_MINUTES'])))
    token=AccountToken(user_id=user.id,unit_id=user.unit_id,purpose=purpose,token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                       email_snapshot=user.email,session_version=user.session_version,created_at=now,expires_at=now+duration)
    db.session.add(token)
    db.session.flush()
    try:
        action=public_base_url()+('/activate' if purpose=='activation' else '/reset-password')+'#token='+raw
    except ValueError:
        action=None
    return queue_email(user,purpose,'Activate your tenant account' if purpose=='activation' else 'Reset your password',
        'Your administrator invited you to DormEnergy. Choose your own password to activate your account.' if purpose=='activation' else 'Use this single-use link to choose a new password. Ignore this email if you did not request it.',
        details=[('Dormitory unit',user.unit.name),('Room',user.unit.room)] if user.unit else [],
        action_url=action,action_label='Activate account' if purpose=='activation' else 'Reset password',
        dedupe_key=f'account-link:{token.id}',token=token)


def valid_link(raw, purpose, locked=False):
    if not isinstance(raw,str) or len(raw)!=43 or purpose not in ('activation','reset'):
        raise DomainError('This account link is invalid, expired, or already used.',403)
    token=db.session.scalar(select(AccountToken).where(AccountToken.token_hash==hashlib.sha256(raw.encode()).hexdigest(),AccountToken.purpose==purpose))
    if not token:
        raise DomainError('This account link is invalid, expired, or already used.',403)
    user=lock_user(token.user_id) if locked else db.session.get(User,token.user_id)
    if locked:
        token=db.session.scalar(select(AccountToken).where(AccountToken.id==token.id).with_for_update().execution_options(populate_existing=True))
    if (token.consumed_at or token.revoked_at or aware(token.expires_at)<=utcnow()
            or not eligible(user,activation=purpose=='activation') or token.email_snapshot!=user.email
            or token.unit_id!=user.unit_id or token.session_version!=user.session_version):
        raise DomainError('This account link is invalid, expired, or already used.',403)
    return token,user


def invitation_status(user):
    if user.activation_status=='active':
        return 'Activated',None
    token=db.session.scalar(select(AccountToken).where(AccountToken.user_id==user.id,AccountToken.purpose=='activation').order_by(AccountToken.id.desc()).limit(1))
    row=db.session.scalar(select(EmailOutbox).where(EmailOutbox.user_id==user.id,EmailOutbox.kind=='activation').order_by(EmailOutbox.id.desc()).limit(1))
    return ('Expired' if not token or token.revoked_at or aware(token.expires_at)<=utcnow() else 'Pending'),row


def queue_balance_alert(user, unit, account, reading, exhausted=False):
    if not eligible(user) or not user.email_balance_alerts or unit.is_demo or db.session.info.get('suppress_email'):
        return
    if aware(reading.timestamp)<aware(user.created_at) or utcnow()-aware(reading.timestamp)>timedelta(minutes=5):
        return  # Historical imports/replay do not create new mail alerts.
    kind='credit_exhausted' if exhausted else 'low_balance'
    queue_email(user,kind,'Prepaid credit exhausted' if exhausted else 'Your prepaid credit is low',
                'Check your prepaid account and request a top-up through your dormitory administrator.',
                details=[('Room',unit.room),('Current balance',f'PHP {account.balance:,.2f}')],
                dedupe_key=f'{kind}:{reading.id}:{user.id}')


def queue_load_update(load, account):
    user=load.user
    if not eligible(user) or not user.email_load_updates or user.unit_id!=load.unit_id or db.session.info.get('suppress_email'):
        return
    approved=load.status=='approved'
    details=[('Room',load.unit.room),('Reference',load.reference),('Reviewed',aware(load.resolved_at).astimezone(MANILA).strftime('%b %d, %Y, %I:%M %p Philippine time'))]
    if approved:
        details.extend([('Approved amount',f'PHP {load.amount:,.2f}'),('Updated balance',f'PHP {account.balance:,.2f}')])
    queue_email(user,'load_'+load.status,'Your top-up was approved' if approved else 'Your top-up was rejected',
                'Your prepaid credit has been updated.' if approved else 'Open your dashboard or contact your administrator for details.',
                details=details,dedupe_key=f'load:{load.id}:{load.status}:{user.id}')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        return None  # Do not forward provider credentials to a redirect destination.


def _resend_http(request,timeout):
    return urllib.request.build_opener(_NoRedirect()).open(request,timeout=timeout)


def resend_send(payload, message_key):
    """Fixed HTTPS destination and verified TLS; never logs response bodies."""
    body=dict(from_=payload.get('from',current_app.config['EMAIL_FROM']),to=[payload['to']],subject=payload['subject'],html=payload['html'],text=payload['text'])
    body['from']=body.pop('from_')
    request=urllib.request.Request('https://api.resend.com/emails',data=json.dumps(body).encode(),method='POST',
        headers={'Content-Type':'application/json','Authorization':'Bearer '+current_app.config['RESEND_API_KEY'],
                 'Idempotency-Key':message_key,'User-Agent':'DormEnergy/1.0'})
    try:
        with _resend_http(request,timeout=10) as response:
            raw=response.read(16385)
            data=json.loads(raw) if len(raw)<=16384 else None
        if not isinstance(data,dict) or not isinstance(data.get('id'),str) or not data['id'].strip() or len(data['id'])>128:
            raise DeliveryError('provider_temporary',True)
        return data['id']
    except urllib.error.HTTPError as error:
        raise DeliveryError('provider_temporary' if error.code in (408,429) or error.code>=500 else 'provider_rejected',error.code in (408,429) or error.code>=500) from None
    except (urllib.error.URLError,TimeoutError,OSError,ValueError,RecursionError,http.client.HTTPException):
        raise DeliveryError('provider_temporary',True) from None


def delivery_eligible(row, payload):
    user=db.session.get(User,row.user_id)
    if not eligible(user,activation=row.kind=='activation') or user.unit_id!=row.unit_id:
        return False,'account_ineligible'
    if payload['to']!=user.email and row.kind!='security_email_old':
        return False,'account_ineligible'
    if row.kind in ('low_balance','credit_exhausted') and not user.email_balance_alerts:
        return False,'account_ineligible'
    if row.kind.startswith('load_') and not user.email_load_updates:
        return False,'account_ineligible'
    if row.token_id:
        token=db.session.get(AccountToken,row.token_id)
        if not token or token.revoked_at or token.consumed_at or aware(token.expires_at)<=utcnow() or token.session_version!=user.session_version or token.email_snapshot!=user.email:
            return False,'link_expired'
    return True,None


def email_worker_step(now=None):
    now=aware(now or utcnow())
    db.session.execute(update(EmailOutbox).where(EmailOutbox.status=='sending',EmailOutbox.leased_until<now).values(
        status='pending',next_attempt_at=now,lease_id=None,leased_until=None,error_code='worker_interrupted'))
    db.session.commit()
    row=db.session.scalar(select(EmailOutbox).where(EmailOutbox.status=='pending',EmailOutbox.next_attempt_at<=now)
                          .order_by(EmailOutbox.id).with_for_update(skip_locked=True).limit(1))
    if not row:
        db.session.remove()
        return False
    lease=str(uuid4())
    row.status,row.lease_id,row.leased_until='sending',lease,now+timedelta(minutes=2)
    row.attempts+=1
    row_id,attempts,created=row.id,row.attempts,aware(row.created_at)
    db.session.commit()
    status,error,provider_id='sent',None,None
    try:
        row=db.session.get(EmailOutbox,row_id)
        if not row.payload_encrypted:
            raise DeliveryError('configuration')
        payload=decrypt_payload(row)
        message_key=provider_message_key(row)
        allowed,reason=delivery_eligible(row,payload)
        if not allowed:
            status,error='cancelled',reason
        elif now-created>timedelta(hours=22):
            status,error='failed','retry_window_expired'
        elif configuration_error():
            raise DeliveryError(configuration_error())
        else:
            # Release database read transactions before the external network call.
            db.session.rollback()
            if current_app.testing or current_app.config['EMAIL_BACKEND']=='memory':
                transport=current_app.extensions.get('email_test_transport') if current_app.testing else None
                if transport:
                    transport(payload,message_key)
                current_app.extensions.setdefault('test_email_messages',[]).append(payload)
                status,provider_id='test',f'test-{row_id}'
            else:
                provider_id=resend_send(payload,message_key)
    except (InvalidToken,TypeError,KeyError,ValueError):
        status,error='failed','encryption'
    except DeliveryError as failure:
        error=failure.code
        status='pending' if failure.transient and attempts<max(1,min(10,current_app.config['EMAIL_MAX_ATTEMPTS'])) else 'failed'
        if failure.transient and status=='failed':
            error='retry_exhausted'
    row=db.session.scalar(select(EmailOutbox).where(EmailOutbox.id==row_id).with_for_update().execution_options(populate_existing=True))
    if row.lease_id==lease and row.status=='sending':
        row.status,row.error_code,row.provider_message_id=status,error,provider_id
        row.lease_id,row.leased_until=None,None
        if status=='pending':
            row.next_attempt_at=now+timedelta(seconds=(60,300,900,3600)[min(attempts-1,3)])
        if status in ('sent','test'):
            row.sent_at=now
        db.session.commit()
    current_app.logger.info('email_attempt id=%s attempt=%s status=%s code=%s',row_id,attempts,status,error or 'none')
    db.session.remove()
    return True
