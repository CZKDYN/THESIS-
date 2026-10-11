import math
import os
import secrets
import time
from datetime import timedelta
from decimal import Decimal
import click
from flask import current_app
from sqlalchemy import select
from werkzeug.security import generate_password_hash
from .extensions import db
from .models import Account, AuditLog, Device, ForecastJob, ForecastModel, LoadRequest, Notification, Rate, Transaction, Unit, User, utcnow
from .services.billing import DomainError, ingest_reading, lock_unit
from .services.simulation import simulation_step
from .services.forecasting import forecast_worker_step, generate_forecasts, train_model


def register_commands(app):
    @app.cli.command('train-forecast')
    @click.option('--unit-id', required=True, type=int)
    def train_forecast(unit_id):
        """Train/evaluate a per-unit XGBoost model and persist it in the database."""
        import json
        try:
            model = train_model(unit_id)
            try:
                generate_forecasts(unit_id)
            except DomainError as error:
                db.session.rollback()
                click.echo(error.message)
            click.echo(json.dumps({'version': model.version, 'source': model.source,
                                  'test': model.metadata_json['test']}, indent=2))
        except DomainError as error:
            db.session.rollback()
            raise click.ClickException(error.message) from None

    @app.cli.command('forecast-worker')
    @click.option('--interval', default=10.0, type=click.FloatRange(min=5, max=300))
    @click.option('--once', is_flag=True)
    @click.option('--demo-models', is_flag=True, help='Queue initial models only for existing synthetic demo units.')
    def forecast_worker(interval, once, demo_models):
        """Process training jobs and refresh stored hourly predictions."""
        if demo_models and current_app.config['SIMULATION_MODE']:
            admin = db.session.scalar(select(User).where(User.role == 'admin', User.active.is_(True)).order_by(User.id).limit(1))
            if admin:
                for unit in db.session.scalars(select(Unit).where(Unit.is_demo.is_(True), Unit.archived_at.is_(None))).all():
                    if unit.device and unit.device.source == 'simulation':
                        lock_unit(unit.id)
                        if not db.session.scalar(select(ForecastModel.id).where(ForecastModel.unit_id == unit.id).limit(1)) and not db.session.scalar(select(ForecastJob.id).where(ForecastJob.unit_id == unit.id).limit(1)):
                            db.session.add(ForecastJob(unit_id=unit.id, requested_by=admin.id))
                        db.session.commit()
        click.echo('XGBoost forecasting worker running. Synthetic models are demonstration results only.')
        while True:
            try:
                forecast_worker_step()
            except Exception:
                db.session.rollback()
                current_app.logger.exception('Forecast worker step failed')
                if once:
                    raise
            if once:
                break
            time.sleep(interval)

    @app.cli.command('seed-demo')
    @click.option('--password', envvar='DEMO_PASSWORD', help='Development-only password; use DEMO_PASSWORD or a prompt.')
    def seed_demo(password):
        """Create three simulated units and clearly labeled development accounts."""
        if not current_app.config['SIMULATION_MODE']:
            raise click.ClickException('Enable SIMULATION_MODE=true before creating demo data.')
        if db.session.scalar(select(Unit.id).where(Unit.is_demo.is_(True))):
            click.echo('Demo units already exist; existing data was preserved.')
            return
        if db.session.scalar(select(Unit.id).limit(1)) or db.session.scalar(select(User.id).limit(1)):
            raise click.ClickException('Seed only into an empty database; existing user data was preserved.')
        password = password or click.prompt('Development demo password (12+ characters)', hide_input=True, confirmation_prompt=True)
        if not 12 <= len(password) <= 128:
            raise click.ClickException('Use a password of 12–128 characters.')
        now = utcnow()
        start = now.replace(minute=0, second=0, microsecond=0) - timedelta(days=7)
        db.session.add(Rate(php_per_kwh=Decimal('12.85'), effective_at=start - timedelta(days=1)))
        admin = User(name='Alex Rivera', email='admin@dormenergy.test', role='admin')
        admin.set_password(password)
        db.session.add(admin)
        db.session.flush()
        names = ['Jamie Santos', 'Sam Reyes', 'Charlie Cruz']
        for index in range(1, 4):
            unit = Unit(name=f'Unit 0{index}', room=f'Room {200+index} · East wing', is_demo=True, power_status='connected')
            db.session.add(unit)
            db.session.flush()
            initial = [Decimal('800'), Decimal('650'), Decimal('380')][index-1]
            account = Account(unit_id=unit.id, balance=initial, low_threshold=Decimal(current_app.config['LOW_BALANCE_THRESHOLD']))
            device = Device(identifier=f'DEMO-ESP32-0{index}', unit_id=unit.id, source='simulation')
            user = User(name=names[index-1], email=f'tenant{index}@dormenergy.test', role='tenant', unit_id=unit.id)
            user.set_password(password)
            db.session.add_all([account, device, user])
            db.session.flush()
            db.session.add(Transaction(unit_id=unit.id, kind='demo_credit', amount=initial, balance_after=initial, description='Opening sample credit · simulation only', created_at=start))
            energy = Decimal('0')
            for offset in range(169):
                timestamp = start + timedelta(hours=offset)
                hour = (timestamp.hour + 8) % 24
                watts = 105 + index * 23 + (175 if 18 <= hour <= 23 else 65) + 45 * math.sin(offset * 1.13)
                if offset:
                    energy += Decimal(str(round(watts / 1000, 6)))
                ingest_reading(device.id, dict(device_id=device.identifier, unit_id=unit.id,
                    event_id=f'seed-{index}-{offset}', timestamp=timestamp.isoformat(), meter_epoch='demo-1',
                    voltage='230.4', current=str(round(watts / 230.4, 4)), power_w=str(round(watts, 3)),
                    energy_kwh=str(energy), connection_status='connected'), 'simulation')
            db.session.add(Notification(unit_id=unit.id, kind='welcome', message='Welcome to DormEnergy. These readings and balances are simulated for the thesis prototype.'))
            if index == 2:
                db.session.add(LoadRequest(user_id=user.id, unit_id=unit.id, amount=Decimal('500'), reference='DEMO-LOAD-002', note='Sample request for administrator review.'))
        db.session.add(AuditLog(actor_id=admin.id, action='demo_seeded', detail='Seven days of synthetic hourly observations; no experimental results.'))
        db.session.commit()
        simulation_step()
        click.echo('Created admin@dormenergy.test and tenant1–3@dormenergy.test using your supplied demo password. All data are simulated.')

    @app.cli.command('simulate')
    @click.option('--interval', default=5.0, type=click.FloatRange(min=1, max=120))
    @click.option('--once', is_flag=True)
    def simulate(interval, once):
        """Run a separate simulator process; no background worker hidden in Flask."""
        if not current_app.config['SIMULATION_MODE']:
            raise click.ClickException('Simulation is disabled. Set SIMULATION_MODE=true for development.')
        click.echo('SIMULATION ONLY — synthetic meter updates; Ctrl+C to stop.')
        while True:
            try:
                count = simulation_step()
                current_app.logger.info('simulation_transmission accepted=%s', count)
            except Exception:
                db.session.rollback()
                current_app.logger.exception('Simulation step failed')
                if once:
                    raise
            if once:
                break
            time.sleep(interval)

    @app.cli.command('provision-device')
    @click.argument('identifier')
    def provision_device(identifier):
        """Provision a real device token once; store it securely on the gateway."""
        device = db.session.scalar(select(Device).where(Device.identifier == identifier))
        if not device or device.source != 'hardware':
            raise click.ClickException('Register a hardware device first. Demo devices cannot become hardware devices.')
        try:
            lock_unit(device.unit_id)
        except DomainError as error:
            db.session.rollback()
            raise click.ClickException(error.message) from None
        token = secrets.token_urlsafe(40)
        device.token_hash = generate_password_hash(token)
        db.session.add(AuditLog(action='device_token_rotated', detail=f'Device {device.id} token rotated'))
        db.session.commit()
        click.echo('One-time device token. Store securely; do not commit or paste it into chat:')
        click.echo(token)
