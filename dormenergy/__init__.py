import logging
import os
from datetime import timedelta
from pathlib import Path
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError
from sqlalchemy import text
from dotenv import load_dotenv
from .extensions import csrf, db, login_manager, migrate
from .models import User


def normalize_database_url(url):
    """Managed hosts supply PostgreSQL URLs without a Python driver qualifier."""
    for prefix in ('postgres://', 'postgresql://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


def create_app(test_config=None):
    load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=False)
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv('SECRET_KEY'),
        SQLALCHEMY_DATABASE_URI=normalize_database_url(os.getenv('DATABASE_URL', '')),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SIMULATION_MODE=os.getenv('SIMULATION_MODE', 'false').lower() == 'true',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=32 * 1024,
        LOW_BALANCE_THRESHOLD=os.getenv('LOW_BALANCE_THRESHOLD', '100.00'),
        ADMIN_SETUP_TOKEN=os.getenv('ADMIN_SETUP_TOKEN', ''),
        EMAIL_BACKEND=os.getenv('EMAIL_BACKEND', 'disabled').lower(),
        RESEND_API_KEY=os.getenv('RESEND_API_KEY', ''),
        EMAIL_FROM=os.getenv('EMAIL_FROM', ''),
        PUBLIC_BASE_URL=os.getenv('PUBLIC_BASE_URL', ''),
        EMAIL_ENCRYPTION_KEY=os.getenv('EMAIL_ENCRYPTION_KEY', ''),
        ALLOW_TEST_EMAIL=os.getenv('ALLOW_TEST_EMAIL', 'false').lower() == 'true',
        EMAIL_ACTIVATION_HOURS=int(os.getenv('EMAIL_ACTIVATION_HOURS', '24')),
        EMAIL_RESET_MINUTES=int(os.getenv('EMAIL_RESET_MINUTES', '30')),
        EMAIL_RESEND_COOLDOWN=int(os.getenv('EMAIL_RESEND_COOLDOWN', '60')),
        EMAIL_MAX_ATTEMPTS=int(os.getenv('EMAIL_MAX_ATTEMPTS', '6')),
        EMAIL_CREDIT_EXHAUSTION=os.getenv('EMAIL_CREDIT_EXHAUSTION', 'false').lower() == 'true',
    )
    if test_config:
        app.config.update(test_config)
        if test_config.get('TESTING'):
            # Tests never inherit a production email provider or sender credentials.
            app.config.update(EMAIL_BACKEND='memory', PUBLIC_BASE_URL='http://localhost:5000',
                              EMAIL_FROM='DormEnergy <no-reply@example.test>', EMAIL_ENCRYPTION_KEY='')
            for key in ('EMAIL_BACKEND','PUBLIC_BASE_URL','EMAIL_FROM','EMAIL_ENCRYPTION_KEY'):
                if key in test_config:
                    app.config[key] = test_config[key]
    if not app.config['SECRET_KEY'] or app.config['SECRET_KEY'].startswith('replace-'):
        raise RuntimeError('Set a random SECRET_KEY in your environment or .env before starting.')
    if not app.config['SQLALCHEMY_DATABASE_URI']:
        raise RuntimeError('DATABASE_URL is required. See README for PostgreSQL setup.')
    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'web.login'
    login_manager.session_protection = 'strong'

    @login_manager.user_loader
    def load_user(user_id):
        try:
            user = db.session.get(User, int(user_id))
            if not user or not user.active or user.activation_status != 'active' or session.get('auth_version') != user.session_version or (user.role == 'tenant' and (user.unit.archived_at or user.tenancy_ended_at or (not app.config['SIMULATION_MODE'] and user.unit.is_demo))):
                return None
            return user
        except (ValueError, TypeError):
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith('/api/'):
            return jsonify(error='Sign in to continue.'), 401
        from flask import redirect, url_for
        return redirect(url_for('web.login'))

    from .web import web
    from .api import api
    from .hardware import hardware
    from .commands import register_commands
    app.register_blueprint(web)
    app.register_blueprint(api, url_prefix='/api')
    app.register_blueprint(hardware, url_prefix='/api/iot')
    from .email_links import email_links
    app.register_blueprint(email_links)
    csrf.exempt(hardware)  # Uses a scoped bearer token, never cookie authentication.
    register_commands(app)

    @app.before_request
    def require_password_change():
        from flask_login import current_user
        if current_user.is_authenticated and current_user.must_change_password and request.endpoint not in ('web.password', 'web.logout', 'static') and not (request.endpoint or '').startswith('email_links.'):
            if request.path.startswith('/api/'):
                return jsonify(error='Change your temporary password before using your account.', password_change_required=True), 403
            return redirect(url_for('web.password'))

    @app.get('/health')
    def health():
        db.session.execute(text('SELECT 1'))
        return jsonify(status='ok', mode='simulation' if app.config['SIMULATION_MODE'] else 'hardware')

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        reasons = {
            'The CSRF token is missing.': 'token_missing',
            'The CSRF session token is missing.': 'session_missing',
            'The CSRF token has expired.': 'token_expired',
            'The CSRF token is invalid.': 'token_invalid',
            'The CSRF tokens do not match.': 'token_mismatch',
            'The referrer header is missing.': 'referrer_missing',
            'The referrer does not match the host.': 'referrer_mismatch',
        }
        app.logger.warning('form_rejected endpoint=%s reason=%s', request.endpoint or 'unknown',
                           reasons.get(error.description, 'validation_failed'))
        if request.path.startswith('/api/'):
            return jsonify(error='Session expired. Reload the page and try again.'), 400
        if request.endpoint == 'web.login' and request.method == 'POST':
            # Reject the old submission and request a fresh GET; never replay credentials.
            session.pop('csrf_token', None)
            message = ('Your sign-in form could not be verified. Reload the page and try again.'
                       if reasons.get(error.description, '').startswith('referrer_')
                       else 'Your sign-in page expired. Please sign in again.')
            flash(message, 'error')
            return redirect(url_for('web.login'), code=303)
        return render_template('error.html', code=400, message='Your form expired. Please reload and try again.'), 400

    @app.errorhandler(403)
    @app.errorhandler(404)
    def client_error(error):
        if request.path.startswith('/api/'):
            return jsonify(error='You cannot access this resource.'), error.code
        return render_template('error.html', code=error.code, message='This page is unavailable for your account.'), error.code

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        app.logger.exception('Request failed')
        if request.path.startswith('/api/'):
            return jsonify(error='The request could not be completed. Please try again.'), 500
        return render_template('error.html', code=500, message='We could not complete your request. Please try again.'), 500

    @app.after_request
    def secure_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        # HTTPS form protection needs same-origin referrers. External requests get none;
        # URL fragments containing account-link secrets are never included by browsers.
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; form-action 'self'"
        if request.path.startswith('/api/') or request.path in ('/dashboard', '/login', '/setup', '/account', '/account/password', '/activate', '/reset-password', '/forgot-password'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    return app
