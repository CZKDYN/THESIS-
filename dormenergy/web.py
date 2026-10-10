from datetime import timedelta
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select, func
from werkzeug.security import check_password_hash, generate_password_hash
from .extensions import db
from .models import AuditLog, Unit, User, utcnow

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
        if not user or not valid or not user.active or (user.role == 'tenant' and user.unit.archived_at):
            db.session.add(AuditLog(action='login_failed', detail=email))
            db.session.commit()
            flash('Email or password is incorrect.', 'error')
            return render_template('login.html'), 401
        session.clear()
        login_user(user)
        session.permanent = True
        db.session.add(AuditLog(actor_id=user.id, action='login', detail='Successful session authentication'))
        db.session.commit()
        return redirect(url_for('web.dashboard'))
    return render_template('login.html')


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
