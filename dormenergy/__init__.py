import logging
import os
from datetime import timedelta
from pathlib import Path
from flask import Flask, jsonify, render_template, request
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
    )
    if test_config:
        app.config.update(test_config)
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
            return db.session.get(User, int(user_id))
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
    csrf.exempt(hardware)  # Uses a scoped bearer token, never cookie authentication.
    register_commands(app)

    @app.get('/health')
    def health():
        db.session.execute(text('SELECT 1'))
        return jsonify(status='ok', mode='simulation' if app.config['SIMULATION_MODE'] else 'hardware')

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        if request.path.startswith('/api/'):
            return jsonify(error='Session expired. Reload the page and try again.'), 400
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
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; form-action 'self'"
        if request.path.startswith('/api/') or request.path == '/dashboard':
            response.headers['Cache-Control'] = 'no-store'
        return response

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    return app
