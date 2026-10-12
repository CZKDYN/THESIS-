"""Hosting-controlled utility references; never invent a current national tariff."""
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from flask import current_app
from sqlalchemy import false, select, text

from ..extensions import db
from ..models import AuditLog, Rate, utcnow
from .billing import DomainError, aware, decimal_value


def utility_policy():
    config = current_app.config
    result = dict(locked=config['UTILITY_RATE_LOCKED'], provider=config['UTILITY_PROVIDER'].strip(),
                  service_area=config['UTILITY_SERVICE_AREA'].strip(), reference_rate=None,
                  confirmed=False, period=None, source=None, effective_at=None, valid_until=None,
                  status='reference', message='Reference supplied by the owner; confirm the applicable utility bill before billing.')
    try:
        rate = decimal_value(config['UTILITY_RATE_PHP_PER_KWH'], 'Utility rate', Decimal('1000'))
        if rate <= 0 or rate != rate.quantize(Decimal('0.0001')):
            raise ValueError()
        result['reference_rate'] = str(rate.quantize(Decimal('0.0001')))
        if not 1 <= len(result['provider']) <= 120 or len(result['service_area']) > 120:
            raise ValueError()
        if not config['UTILITY_RATE_CONFIRMED']:
            return result
        period = config['UTILITY_RATE_PERIOD'].strip()
        source = config['UTILITY_RATE_SOURCE'].strip()
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period) or not 1 <= len(source) <= 240:
            raise ValueError()
        def timestamp(value):
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError()
            return parsed.astimezone(timezone.utc)
        until = timestamp(config['UTILITY_RATE_VALID_UNTIL'])
        effective = timestamp(config['UTILITY_RATE_EFFECTIVE_AT']) if config['UTILITY_RATE_EFFECTIVE_AT'] else None
        if effective and until <= effective:
            raise ValueError()
        result.update(confirmed=True, period=period, source=source,
                      effective_at=effective.isoformat() if effective else None, valid_until=until.isoformat(),
                      status='expired' if until <= utcnow() else 'confirmed',
                      message='Owner-confirmed utility source; rates apply only during their recorded validity period.')
        if result['status']=='expired':
            result['message']='The configured utility period has expired. Confirm the next applicable period before further billing.'
    except (DomainError, ValueError, TypeError, AttributeError):
        result.update(status='configuration_required', confirmed=False,
                      message='Utility rate is locked. Check the rate, source, billing period and timezone-aware validity dates.')
    return result


def billing_rates():
    """Filter billing/forecast inputs consistently, retaining eligible prior periods."""
    query = select(Rate)
    if not current_app.config['SIMULATION_MODE']:
        query = query.where(Rate.is_demo.is_(False))
    if current_app.config['UTILITY_RATE_LOCKED']:
        policy = utility_policy()
        if not policy['confirmed']:
            return query.where(false())
        query = query.where(Rate.utility_provider == policy['provider'], Rate.configuration_key.is_not(None),
                            Rate.valid_until.is_not(None))
    return query


def rate_covers(rate, start, end=None):
    until = getattr(rate, 'valid_until', None)
    return rate is not None and (until is None or (
        aware(until) > aware(start) and (end is None or aware(end) <= aware(until))))


def apply_configured_rate():
    """Explicit startup/CLI step after migration; never updates historical rows."""
    policy = utility_policy()
    if not policy['locked'] or not policy['confirmed']:
        return None
    key = hashlib.sha256(json.dumps({name: policy[name] for name in (
        'provider', 'reference_rate', 'period', 'source', 'effective_at', 'valid_until')}, sort_keys=True).encode()).hexdigest()
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text('SELECT pg_advisory_xact_lock(198742107)'))
    existing = db.session.scalar(select(Rate).where(Rate.configuration_key == key))
    if existing:
        db.session.commit()
        return existing
    now = utcnow()
    effective = datetime.fromisoformat(policy['effective_at']) if policy['effective_at'] else now
    if effective < now and policy['effective_at']:
        raise DomainError('A new utility rate cannot be backdated. Leave its effective date blank to apply now.')
    until = datetime.fromisoformat(policy['valid_until'])
    if until <= max(effective, now):
        return None  # Never create a new expired period or backfill old charges.
    row = Rate(php_per_kwh=Decimal(policy['reference_rate']), effective_at=effective, valid_until=until,
               utility_provider=policy['provider'], billing_period=policy['period'],
               source_reference=policy['source'], configuration_key=key)
    db.session.add(row)
    db.session.add(AuditLog(action='utility_rate_configured',
        detail=f"{policy['provider']}: PHP {policy['reference_rate']}/kWh; period {policy['period']}; owner-confirmed source; historical charges retained."))
    db.session.commit()
    return row
