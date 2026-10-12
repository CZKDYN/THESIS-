from datetime import timedelta
import secrets
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select, func, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from .extensions import db
from .models import AuditLog, Unit, User, utcnow
from .services.accounts import change_password, identity, password_value
from .services.billing import DomainError
from .services.emailing import cancel_user_mail, security_email

web = Blueprint('web', __name__)
# A fixed dummy hash keeps unknown-account password checks similarly expensive.
DUMMY_HASH = generate_password_hash('not-an-account-password')


@web.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('web.dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()[:254]
        password = request.form.get('password', '')[:256]
        failures = db.session.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == 'login_failed', AuditLog.detail == email,
            AuditLog.created_at > utcnow() - timedelta(minutes=15)))
        if failures >= 10:
            flash('Too many attempts. Please try again in 15 minutes.', 'error')
            return render_template('login.html'), 429
        user = db.session.scalar(select(User).where(User.email == email))
        valid = check_password_hash(user.password_hash if user else DUMMY_HASH, password)
        if not user or not valid or not user.active or user.activation_status!='active' or (user.role == 'tenant' and (user.unit.archived_at or user.tenancy_ended_at or (not current_app.config['SIMULATION_MODE'] and user.unit.is_demo))):
            db.session.add(AuditLog(action='login_failed', detail=email))
            db.session.commit()
            flash('Email or password is incorrect.', 'error')
            return render_template('login.html'), 401
        session.clear()
        login_user(user)
        session['auth_version'] = user.session_version
        session.permanent = True
        db.session.add(AuditLog(actor_id=user.id, action='login', detail='Successful session authentication'))
        db.session.commit()
        return redirect(url_for('web.password' if user.must_change_password else 'web.dashboard'))
    has_admin = db.session.scalar(select(User.id).where(User.role == 'admin', User.active.is_(True)).limit(1)) is not None
    return render_template('login.html', has_admin=has_admin)


@web.post('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('web.login'))


@web.get('/')
def index():
    return redirect(url_for('web.dashboard' if current_user.is_authenticated else 'web.login'))


@web.get('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')


@web.route('/setup', methods=['GET', 'POST'])
def setup():
    """The owner claims an empty deployment with a hosting-secret token."""
    if request.method == 'POST' and db.engine.dialect.name == 'postgresql':
        db.session.execute(text('SELECT pg_advisory_xact_lock(198742106)'))
    if db.session.scalar(select(User.id).where(User.role == 'admin', User.active.is_(True)).limit(1)):
        db.session.rollback()
        return redirect(url_for('web.login'))
    configured = current_app.config['ADMIN_SETUP_TOKEN']
    if not configured:
        return render_template('account.html', kind='setup', unavailable=True), 503
    if request.method == 'POST':
        try:
            attempts = db.session.scalar(select(func.count(AuditLog.id)).where(
                AuditLog.action == 'setup_failed', AuditLog.created_at > utcnow()-timedelta(minutes=15)))
            if attempts >= 10:
                raise DomainError('Too many setup attempts. Try again in fifteen minutes.', 429)
            if not secrets.compare_digest(request.form.get('setup_token', '').encode(), configured.encode()):
                db.session.add(AuditLog(action='setup_failed', detail='Invalid administrator setup token.'))
                db.session.commit()
                raise DomainError('The setup token is incorrect.', 403)
            name, email = identity(request.form.get('name'), request.form.get('email'))
            value = password_value(request.form.get('new_password'))
            if value != request.form.get('confirm_password'):
                raise DomainError('The new passwords do not match.')
            user = User(name=name, email=email, role='admin')
            user.set_password(value)
            db.session.add(user)
            db.session.flush()
            db.session.add(AuditLog(actor_id=user.id, action='admin_created', detail='First administrator created through owner setup.'))
            security_email(user,'welcome',key=f'admin-welcome:{user.id}')
            db.session.commit()
            session.clear()
            login_user(user)
            session['auth_version'] = user.session_version
            session.permanent = True
            return redirect(url_for('web.dashboard'))
        except (DomainError, IntegrityError) as error:
            db.session.rollback()
            flash(error.message if isinstance(error, DomainError) else 'This email already has an account.', 'error')
            return render_template('account.html', kind='setup'), error.status if isinstance(error, DomainError) else 409
    return render_template('account.html', kind='setup')


@web.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    if request.method == 'POST':
        try:
            db.session.scalar(select(User).where(User.id == current_user.id).with_for_update().execution_options(populate_existing=True))
            if not current_user.check_password(request.form.get('current_password', '')):
                raise DomainError('Your current password is incorrect.', 403)
            old_email=current_user.email
            current_user.name, current_user.email = identity(request.form.get('name'), request.form.get('email'))
            if old_email!=current_user.email:
                from uuid import uuid4
                cancel_user_mail(current_user,preserve_security_history=True)
                current_user.email_verified_at=None
                event=uuid4().hex
                security_email(current_user,'email',recipient=old_email,key=f'email-change:{event}:old')
                security_email(current_user,'email',key=f'email-change:{event}:new')
            db.session.add(AuditLog(actor_id=current_user.id, action='profile_updated', detail='Account name and email updated.'))
            db.session.commit()
            flash('Your account details were saved.', 'success')
            return redirect(url_for('web.account'))
        except (DomainError, IntegrityError) as error:
            db.session.rollback()
            flash(error.message if isinstance(error, DomainError) else 'This email already has an account.', 'error')
            return render_template('account.html', kind='profile'), error.status if isinstance(error, DomainError) else 409
    return render_template('account.html', kind='profile')


@web.route('/account/password', methods=['GET', 'POST'])
@login_required
def password():
    if request.method == 'POST':
        try:
            db.session.scalar(select(User).where(User.id == current_user.id).with_for_update().execution_options(populate_existing=True))
            if not current_user.check_password(request.form.get('current_password', '')):
                raise DomainError('Your current password is incorrect.', 403)
            value = password_value(request.form.get('new_password'))
            if value != request.form.get('confirm_password'):
                raise DomainError('The new passwords do not match.')
            if current_user.check_password(value):
                raise DomainError('Choose a password different from your current password.')
            change_password(current_user, value)
            db.session.add(AuditLog(actor_id=current_user.id, action='password_changed', detail='Password changed; other sessions revoked.'))
            db.session.commit()
            session['auth_version'] = current_user.session_version
            flash('Your password was changed. Other signed-in sessions have been revoked.', 'success')
            return redirect(url_for('web.dashboard'))
        except DomainError as error:
            db.session.rollback()
            flash(error.message, 'error')
            return render_template('account.html', kind='password'), error.status
    return render_template('account.html', kind='password')


@web.post('/account/preferences')
@login_required
def preferences():
    user=db.session.scalar(select(User).where(User.id==current_user.id).with_for_update().execution_options(populate_existing=True))
    user.email_balance_alerts=request.form.get('email_balance_alerts')=='on'
    user.email_load_updates=request.form.get('email_load_updates')=='on'
    db.session.add(AuditLog(actor_id=user.id,action='email_preferences_changed',detail='Nonessential email preferences updated. Security notifications remain enabled.'))
    db.session.commit()
    flash('Email preferences saved. Essential security emails remain enabled.','success')
    return redirect(url_for('web.account'))
