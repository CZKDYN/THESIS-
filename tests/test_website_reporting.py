"""Read-only presentation accuracy, pagination, export and role-scope regressions."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
import io
import pytest
from sqlalchemy import select
from dormenergy.extensions import db
from dormenergy.models import Account, Device, EmailOutbox, ForecastModel, LoadRequest, Notification, Rate, Reading, Transaction, User
from conftest import login, post

NOW = datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc)


def transaction(unit=1, amount='-1.234567', stamp=NOW, description='Recorded fixture', kind='consumption', **fields):
    return Transaction(unit_id=unit,amount=Decimal(amount),balance_after=Decimal('98.765433'),description=description,kind=kind,created_at=stamp,**fields)


def reading(stamp, source='hardware', delta='1', device=1, event='fixture'):
    return Reading(device_id=device,event_id=event,timestamp=stamp,received_at=NOW,voltage=230,current=1,power=230,
                   energy=Decimal('10'),delta_kwh=Decimal(delta),rate_id=1,source=source,meter_epoch='fixture',connection_status='unknown')


def prepare(app, monkeypatch):
    monkeypatch.setattr('dormenergy.api.utcnow',lambda:NOW)
    with app.app_context():
        for user in db.session.scalars(select(User)): user.created_at=NOW-timedelta(days=60)
        db.session.commit()


def test_current_operations_ignore_historical_notifications_and_count_all_pending(app,client,monkeypatch):
    prepare(app,monkeypatch)
    with app.app_context():
        db.session.get(Account,1).balance=Decimal('10')
        db.session.get(Device,1).last_seen=NOW
        db.session.add(reading(NOW-timedelta(hours=2)))
        db.session.add(Notification(unit_id=1,kind='low_balance',message='Old historical message',created_at=NOW-timedelta(days=20)))
        for i in range(105):db.session.add(LoadRequest(user_id=2,unit_id=1,amount=10,status='pending',reference=f'pending-{i}',created_at=NOW-timedelta(minutes=i)))
        db.session.commit()
    login(client)
    data=client.get('/api/snapshot').json
    assert data['summary']['pending']==105 and len(data['requests'])==100
    assert data['operations']['stale_units']==1 and data['operations']['awaiting_readings']==1
    assert data['operations']['low_balance_units']==1
    assert all('Old historical' not in a['detail'] for a in data['actions'])
    assert data['units'][0]['last_received']==NOW.isoformat() and data['units'][0]['reading_at']!=data['units'][0]['last_received']
    assert data['units'][0]['power'] is None
    with app.app_context():
        assert db.session.get(Account,1).balance==Decimal('10')


def test_usage_and_expenses_use_observations_and_ledger_not_current_rate(app,client,monkeypatch):
    prepare(app,monkeypatch)
    with app.app_context():
        db.session.get(Rate,1).php_per_kwh=Decimal('50')
        db.session.add_all([reading(NOW-timedelta(minutes=10),delta='2'),
            reading(NOW-timedelta(days=2),delta='3',event='older'),
            reading(NOW-timedelta(days=15),delta='4',event='month'),
            transaction(amount='-3.500001'),transaction(amount='-4.25',stamp=NOW-timedelta(days=2)),
            transaction(amount='-5',stamp=NOW-timedelta(days=15)),transaction(unit=2,amount='-999'),
            transaction(amount='500',kind='credit',description='Approved fixture')])
        db.session.commit()
    login(client,'one@test.local')
    data=client.get('/api/snapshot').json
    assert Decimal(data['insights']['seven_day_kwh'])==5 and Decimal(data['insights']['thirty_day_kwh'])==9
    assert Decimal(data['insights']['deductions']['seven_days'])==Decimal('7.750001')
    assert Decimal(data['insights']['deductions']['thirty_days'])==Decimal('12.750001')
    assert data['insights']['latest_topup']['amount'].startswith('500')
    assert data['insights']['comparison'] is None


def test_missing_zero_future_and_source_mismatch_are_distinct(app,client,monkeypatch):
    prepare(app,monkeypatch)
    login(client)
    empty=client.get('/api/snapshot').json
    assert empty['insights']['seven_day_kwh'] is None and all(v is None for v in empty['charts']['daily']['values'])
    with app.app_context():
        db.session.add_all([reading(NOW-timedelta(minutes=5),delta='0'),reading(NOW+timedelta(minutes=1),delta='99',event='future'),
                           reading(NOW-timedelta(days=1),delta='88',source='simulation',event='wrong-source')])
        db.session.commit()
    data=client.get('/api/snapshot').json
    assert Decimal(data['summary']['today_kwh'])==0
    assert data['units'][0]['measurement_state']=='clock_ahead'
    assert data['charts']['daily']['values'][-1]==0 and data['charts']['daily']['values'][-2] is None
    assert data['charts']['daily']['observed_units'][-1]==1
    assert data['insights']['seven_day_kwh'] is not None


def test_latest_measurement_never_relabels_a_different_data_source(app,client,monkeypatch):
    prepare(app,monkeypatch)
    with app.app_context():
        db.session.get(Device,1).last_seen=NOW
        db.session.add(reading(NOW-timedelta(seconds=5),source='simulation',event='synthetic-only'))
        db.session.commit()
    login(client)
    data=client.get('/api/snapshot').json['units'][0]
    assert data['source']=='hardware' and data['reading_at'] is None and data['power'] is None
    assert data['measurement_state']=='awaiting_readings'
    with app.app_context():
        db.session.add(reading(NOW-timedelta(hours=2),event='genuine-older'));db.session.commit()
    data=client.get('/api/snapshot').json['units'][0]
    assert data['measurement_state']=='stale' and data['reading_at']==(NOW-timedelta(hours=2)).isoformat()


def test_paginated_transactions_are_bounded_stable_and_exact(app,client):
    with app.app_context():
        db.session.add_all([transaction(stamp=NOW,description=f'Row {i}') for i in range(62)]);db.session.commit()
    login(client)
    first=client.get('/api/transactions?per_page=25').json
    assert len(first['transactions'])==25 and first['pagination']['total']==62
    assert first['transactions'][0]['amount']=='-1.234567'
    with app.app_context():db.session.add(transaction(description='New concurrent record'));db.session.commit()
    second=client.get(f"/api/transactions?per_page=25&page=2&anchor={first['pagination']['anchor']}").json
    assert second['pagination']['total']==62
    assert not {r['id'] for r in first['transactions']} & {r['id'] for r in second['transactions']}
    assert client.get('/api/transactions').json['pagination']['total']==63


def test_history_filters_export_and_philippine_day_boundary_match(app,client):
    boundary=datetime(2026,10,13,16,0,tzinfo=timezone.utc) # Oct 14 00:00 Manila
    with app.app_context():
        db.session.add_all([transaction(stamp=boundary-timedelta(seconds=1),description='Outside'),
            transaction(stamp=boundary,description='Needle recorded'),transaction(stamp=boundary+timedelta(hours=1),kind='credit',amount='20',description='Needle credit')])
        db.session.commit()
    login(client)
    filters='date_from=2026-10-14&date_to=2026-10-14&q=Needle&kind=consumption&unit_id=1'
    data=client.get('/api/transactions?'+filters).json
    assert data['pagination']['total']==1 and data['transactions'][0]['description']=='Needle recorded'
    exported=client.get('/api/transactions/export?'+filters)
    assert exported.status_code==200
    rows=list(csv.reader(io.StringIO(exported.get_data(as_text=True))))
    assert len(rows)==2 and rows[1][5]=='98.765433' and 'Outside' not in exported.get_data(as_text=True)


def test_request_filters_actual_credited_amount_and_review_time(app,client):
    with app.app_context():
        request=LoadRequest(user_id=2,unit_id=1,reference='REF-25',amount=25,status='approved',created_at=NOW,resolved_at=NOW+timedelta(minutes=1),resolved_by=1)
        db.session.add(request);db.session.flush()
        db.session.add(transaction(amount='25',kind='credit',load_request_id=request.id))
        db.session.add(LoadRequest(user_id=2,unit_id=1,reference='PENDING',amount=50,status='pending',created_at=NOW));db.session.commit()
    login(client)
    data=client.get('/api/load-requests?status=approved&q=REF-25').json
    row=data['requests'][0]
    assert data['pagination']['total']==1 and Decimal(row['credited_amount'])==25 and row['reviewed_at']
    pending=client.get('/api/load-requests?status=pending').json['requests'][0]
    assert pending['credited_amount'] is None


@pytest.mark.parametrize('params',['page=0','page=abc','per_page=101','anchor=-1','date_from=2026-13-02',
    'date_from=2026-10-20&date_to=2026-10-01','date_to=9999-12-31','q='+('x'*121)])
def test_invalid_history_filters_fail_cleanly(client,params):
    login(client)
    for path in ('/api/transactions','/api/load-requests'):
        assert client.get(path+'?'+params).status_code==422


def test_foreign_unit_and_prior_tenancy_histories_are_private(app,client):
    with app.app_context():
        tenant=db.session.get(User,2)
        db.session.add_all([transaction(stamp=tenant.created_at-timedelta(hours=1),description='Prior private record'),
            transaction(stamp=tenant.created_at+timedelta(seconds=1),description='Current record'),transaction(unit=2,description='Other unit'),
            LoadRequest(unit_id=1,user_id=3,amount=10,reference='Other tenant request')]);db.session.commit()
    login(client,'one@test.local')
    for endpoint in ('/api/transactions','/api/load-requests','/api/transactions/export'):
        assert client.get(endpoint+'?unit_id=2&include_archived=true&include_demo=true').status_code==403
    data=client.get('/api/transactions').json
    assert [r['description'] for r in data['transactions']]==['Current record']
    assert client.get('/api/load-requests').json['pagination']['total']==0


def test_retraining_and_recent_failed_mail_are_actual_current_tasks(app,client,monkeypatch):
    prepare(app,monkeypatch)
    with app.app_context():
        db.session.add(ForecastModel(unit_id=1,version='old-fixture',source='hardware',model_json='{}',metadata_json={},trained_at=NOW-timedelta(days=8)))
        for age,status,key in [(1,'failed','recent'),(20,'failed','old'),(1,'cancelled','cancelled')]:
            db.session.add(EmailOutbox(user_id=1,kind='security_password',status=status,dedupe_key=key,error_code='configuration',created_at=NOW-timedelta(days=age)))
        db.session.commit()
    login(client)
    # All-unit snapshot counts age from narrow metadata without attempting model inference.
    data=client.get('/api/snapshot').json
    assert data['operations']['retraining_due']==1 and data['operations']['failed_emails_recent']==1
    keys={a['key'] for a in data['actions']}
    assert {'retraining_due','failed_emails_recent'}<=keys


def test_search_wildcards_are_literal_and_sql_bound(app,client):
    with app.app_context():
        db.session.add_all([transaction(description='100% fixture'),transaction(description='Other row')]);db.session.commit()
    login(client)
    assert client.get('/api/transactions?q=%25').json['pagination']['total']==1
    assert client.get('/api/transactions?q=%27%20OR%201%3D1').json['pagination']['total']==0
