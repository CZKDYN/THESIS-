"""Utility references are source-bound, protected, and never rewrite old charges."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from conftest import login, meter_payload, post, transmit
from dormenergy.extensions import db
from dormenergy.models import Account, AuditLog, Rate, Reading, Transaction, Unit, utcnow
from dormenergy.services.billing import DomainError, aware, charge_for_interval
from dormenergy.services.forecasting import credit_duration
from dormenergy.services.utility_rates import apply_configured_rate, utility_policy


def confirmed(app, **overrides):
    app.config.update(UTILITY_RATE_LOCKED=True, UTILITY_RATE_CONFIRMED=True,
                      UTILITY_PROVIDER='Meralco', UTILITY_RATE_PHP_PER_KWH='14.74',
                      UTILITY_RATE_PERIOD=utcnow().strftime('%Y-%m'),
                      UTILITY_RATE_SOURCE='Explicit test bill fixture; not a production tariff',
                      UTILITY_RATE_EFFECTIVE_AT='', UTILITY_RATE_VALID_UNTIL=(utcnow()+timedelta(days=30)).isoformat())
    app.config.update(overrides)


def test_reference_is_locked_without_activating_unconfirmed_billing(app, client):
    app.config['UTILITY_RATE_LOCKED']=True
    login(client)
    page=client.get('/dashboard').get_data(as_text=True)
    assert 'id="utility-reference-rate"' in page and 'readonly aria-readonly="true"' in page
    assert 'id="rate-form"' not in page
    before=client.get('/api/rates').json
    assert before['policy']['reference_rate']=='14.7400' and not before['policy']['confirmed']
    assert before['active_rate'] is None and client.get('/api/snapshot').json['rate'] is None
    assert post(client,'/api/rates',{'rate':'99','effective_at':utcnow().isoformat()}).status_code==403
    assert client.get('/api/rates').json==before
    with app.app_context():
        assert apply_configured_rate() is None
        assert db.session.scalar(select(func.count(Rate.id)))==1
        assert db.session.get(Rate,1).php_per_kwh==Decimal('12.5')
        assert db.session.get(Rate,1).configuration_key is None
    result=app.test_cli_runner().invoke(args=['apply-utility-rate'])
    assert result.exit_code==0 and 'No new billing rate was activated' in result.output


def test_unconfirmed_meter_request_is_atomic_and_tenant_cannot_change_rate(app, client):
    app.config.update(UTILITY_RATE_LOCKED=True,SIMULATION_MODE=False)
    with app.app_context():
        db.session.get(Unit,1).is_demo=False
        db.session.commit()
    assert transmit(client,meter_payload()).status_code==422
    with app.app_context():
        assert db.session.scalar(select(func.count(Reading.id)))==0
        assert db.session.scalar(select(func.count(Transaction.id)))==0
        assert db.session.get(Account,1).balance==100
    login(client,'one@test.local')
    assert client.get('/api/rates').status_code==403
    assert post(client,'/api/rates',{'rate':'14.74'}).status_code==403
    assert client.get('/api/snapshot').json['utility_rate']['reference_rate']=='14.7400'


def test_confirmed_configuration_is_idempotent_and_preserves_old_records(app, client):
    confirmed(app)
    with app.app_context():
        old_rate=db.session.get(Rate,1).php_per_kwh
        row=apply_configured_rate();row_id=row.id
        assert apply_configured_rate().id==row_id
        assert db.session.scalar(select(func.count(Rate.id)))==2
        assert db.session.get(Rate,1).php_per_kwh==old_rate
        assert row.php_per_kwh==Decimal('14.74') and row.utility_provider=='Meralco'
        assert row.source_reference.startswith('Explicit test') and row.valid_until
        assert db.session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action=='utility_rate_configured'))==1
        assert db.session.get(Account,1).balance==100
    login(client)
    data=client.get('/api/rates').json
    assert data['active_rate']=='14.7400' and data['rates'][0]['owner_confirmed']
    assert data['active_source']==app.config['UTILITY_RATE_SOURCE']
    assert data['active_period']==app.config['UTILITY_RATE_PERIOD']
    assert not data['rates'][1]['owner_confirmed']
    prior_source=data['active_source']
    confirmed(app,UTILITY_RATE_PHP_PER_KWH='15.25',
              UTILITY_RATE_PERIOD=(utcnow()+timedelta(days=31)).strftime('%Y-%m'),
              UTILITY_RATE_SOURCE='Next-period test bill fixture',
              UTILITY_RATE_EFFECTIVE_AT=(utcnow()+timedelta(days=31)).isoformat(),
              UTILITY_RATE_VALID_UNTIL=(utcnow()+timedelta(days=61)).isoformat())
    with app.app_context():apply_configured_rate()
    scheduled=client.get('/api/rates').json
    assert scheduled['policy']['reference_rate']=='15.2500'
    assert scheduled['active_rate']=='14.7400' and scheduled['active_source']==prior_source
    assert post(client,'/api/rates',{'rate':'15'}).status_code==403


def test_meter_charges_use_confirmed_rate_and_expired_intervals_roll_back(app, client):
    confirmed(app, UTILITY_RATE_VALID_UNTIL=(utcnow()+timedelta(seconds=45)).isoformat())
    app.config['SIMULATION_MODE']=False
    with app.app_context():
        db.session.get(Unit,1).is_demo=False
        db.session.commit()
        row=apply_configured_rate()
        start=aware(row.effective_at)+timedelta(seconds=1)
        expired=aware(row.valid_until)+timedelta(seconds=1)
    assert transmit(client,meter_payload('10',timestamp=start,event='utility-baseline')).status_code==201
    assert transmit(client,meter_payload('11',timestamp=start+timedelta(seconds=5),event='utility-one-kwh')).status_code==201
    with app.app_context():
        assert db.session.get(Account,1).balance==Decimal('85.260000')
        assert db.session.scalar(select(Transaction)).amount==Decimal('-14.740000')
    assert transmit(client,meter_payload('12',timestamp=expired,event='utility-expired')).status_code==422
    with app.app_context():
        assert db.session.get(Account,1).balance==Decimal('85.260000')
        assert db.session.scalar(select(func.count(Reading.id)))==2
        assert db.session.scalar(select(func.count(Transaction.id)))==1


@pytest.mark.parametrize('changes', [
    {'UTILITY_RATE_PHP_PER_KWH':'nan'}, {'UTILITY_RATE_PHP_PER_KWH':'-1'},
    {'UTILITY_RATE_PHP_PER_KWH':'14.74111'}, {'UTILITY_RATE_SOURCE':''},
    {'UTILITY_RATE_PERIOD':'October'}, {'UTILITY_RATE_VALID_UNTIL':'2030-01-01'},
])
def test_invalid_confirmation_cannot_activate_a_rate(app, changes):
    confirmed(app, **changes)
    with app.app_context():
        assert utility_policy()['status']=='configuration_required'
        assert apply_configured_rate() is None
        assert db.session.scalar(select(func.count(Rate.id)))==1


def test_backdating_rejected_and_expired_configuration_not_activated(app):
    confirmed(app,UTILITY_RATE_EFFECTIVE_AT=(utcnow()-timedelta(days=1)).isoformat())
    with app.app_context():
        with pytest.raises(DomainError):apply_configured_rate()
        db.session.rollback()
        app.config['UTILITY_RATE_EFFECTIVE_AT']=''
        app.config['UTILITY_RATE_VALID_UNTIL']=(utcnow()-timedelta(seconds=1)).isoformat()
        assert utility_policy()['status']=='expired'
        assert apply_configured_rate() is None
        assert db.session.scalar(select(func.count(Rate.id)))==1


def test_valid_periods_split_billing_and_gaps_are_not_charged(app):
    confirmed(app)
    start=utcnow()-timedelta(hours=2)
    with app.app_context():
        first=Rate(php_per_kwh=10,effective_at=start,valid_until=start+timedelta(minutes=30),
                   utility_provider='Meralco',configuration_key='a'*64)
        second=Rate(php_per_kwh=20,effective_at=start+timedelta(minutes=30),valid_until=start+timedelta(hours=2),
                    utility_provider='Meralco',configuration_key='b'*64)
        db.session.add_all([first,second]);db.session.commit()
        cost,rate=charge_for_interval(Decimal('1'),start,start+timedelta(hours=1))
        assert cost==Decimal('15.000000') and rate.id==second.id
        first.valid_until=start+timedelta(minutes=20);db.session.commit()
        with pytest.raises(DomainError):charge_for_interval(Decimal('1'),start,start+timedelta(hours=1))


def test_credit_projection_stops_at_unknown_rate_boundary(app):
    start=utcnow().replace(minute=0,second=0,microsecond=0)
    curve=[SimpleNamespace(timestamp=start,predicted_kwh=Decimal('1'))]
    rates=[SimpleNamespace(effective_at=start,php_per_kwh=Decimal('10'),valid_until=start+timedelta(minutes=30))]
    assert credit_duration(Decimal('4'),curve,rates,start)==1440
    assert credit_duration(Decimal('6'),curve,rates,start) is None


def test_concurrent_startup_applies_one_utility_rate(app):
    with app.app_context():
        if db.engine.dialect.name!='postgresql':
            pytest.skip('Utility configuration concurrency requires PostgreSQL advisory locks.')
    confirmed(app)
    def apply():
        with app.app_context():
            try:return apply_configured_rate().id
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(lambda _:apply(),range(2)))
    assert ids[0]==ids[1]
    with app.app_context():assert db.session.scalar(select(func.count(Rate.id)))==2
