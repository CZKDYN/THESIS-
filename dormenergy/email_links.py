"""Anonymous account links arrive in fragments, never access-log URL paths."""
import time
from flask import Blueprint, flash, jsonify, render_template, request, session
from flask_login import logout_user
from sqlalchemy import select
from .extensions import db
from .models import AuditLog, User, utcnow
from .services.accounts import password_value
from .services.billing import DomainError
from .services.emailing import eligible, issue_link, rate_limit, revoke_links, security_email, valid_link

email_links=Blueprint('email_links',__name__)
GENERIC='If an eligible account matches this email, a reset email has been requested. Check your inbox and spam folder, or contact your administrator if it does not arrive.'


@email_links.route('/forgot-password',methods=['GET','POST'])
def forgot():
    if request.method=='POST':
        started=time.monotonic()
        email=request.form.get('email','').strip().lower()[:254]
        allowed=rate_limit('reset-ip:'+(request.remote_addr or 'unknown'),30,900)
        allowed=rate_limit('reset-email:'+email,5,3600) and allowed
        if allowed:
            user=db.session.scalar(select(User).where(User.email==email))
            if eligible(user):
                try:
                    issue_link(user,'reset')
                except DomainError:
                    pass  # Same response for an ineligible, throttled, or unknown account.
        db.session.commit()
        # Normalize ordinary known/unknown response timing; provider I/O is asynchronous.
        time.sleep(max(0,.25-(time.monotonic()-started)))
        return render_template('email_account.html',kind='forgot',confirmation=GENERIC)
    return render_template('email_account.html',kind='forgot')


@email_links.post('/api/account-links/check')
def check():
    if not rate_limit('link-check:'+(request.remote_addr or 'unknown'),60,900):
        db.session.commit()
        raise DomainError('Please wait before checking another link.',429)
    db.session.commit()
    try:
        payload=request.get_json(silent=True)
    except RecursionError:
        raise DomainError('Invalid account link.',403) from None
    if not isinstance(payload,dict):
        raise DomainError('Invalid account link.',403)
    token,user=valid_link(payload.get('token'),payload.get('purpose'))
    db.session.commit()
    return jsonify(valid=True,name=user.name,room=user.unit.room if user.unit else None)


def redeem(purpose):
    try:
        payload=request.get_json(silent=True)
    except RecursionError:
        raise DomainError('A valid account link and password are required.') from None
    if not isinstance(payload,dict):
        raise DomainError('A valid account link and password are required.')
    token,user=valid_link(payload.get('token'),purpose,locked=True)
    password=password_value(payload.get('new_password'))
    if password!=payload.get('confirm_password'):
        raise DomainError('The passwords do not match.')
    user.set_password(password)
    user.must_change_password=False
    user.session_version+=1
    user.email_verified_at=utcnow()
    if purpose=='activation':
        user.active=True
        user.activation_status='active'
        user.email_verified_at=utcnow()
    token.consumed_at=utcnow()
    revoke_links(user,keep=token.id)
    if purpose=='reset':
        security_email(user,'password')
    db.session.add(AuditLog(actor_id=user.id,action='account_activated' if purpose=='activation' else 'password_recovered',
        detail='Single-use account link redeemed; previous sessions revoked.'))
    db.session.commit()
    logout_user()
    session.clear()
    flash('Account activated. Sign in with your new password.' if purpose=='activation' else 'Password changed. Sign in again; previous sessions were revoked.','success')
    return jsonify(redirect='/login')


@email_links.route('/activate',methods=['GET','POST'])
def activate():
    if request.method=='POST':
        return redeem('activation')
    return render_template('email_account.html',kind='activation')


@email_links.route('/reset-password',methods=['GET','POST'])
def reset():
    if request.method=='POST':
        return redeem('reset')
    return render_template('email_account.html',kind='reset')


@email_links.errorhandler(DomainError)
def bad_link(error):
    db.session.rollback()
    return jsonify(error=error.message),error.status
