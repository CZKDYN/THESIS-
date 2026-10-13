"""Read-only, scoped history filters; the billing ledger remains authoritative."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload
from ..extensions import db
from ..models import LoadRequest, Transaction, Unit
from .billing import DomainError

MANILA = ZoneInfo('Asia/Manila')


def date_bounds(args):
    values = []
    try:
        for field in ('date_from', 'date_to'):
            raw = args.get(field, '')
            if raw and (len(raw) != 10 or date.fromisoformat(raw).isoformat() != raw):
                raise ValueError()
            values.append(date.fromisoformat(raw) if raw else None)
        start, end = values
        if start and end and start > end:
            raise ValueError()
        return (datetime.combine(start, time.min, MANILA).astimezone(timezone.utc) if start else None,
                datetime.combine(end+timedelta(days=1), time.min, MANILA).astimezone(timezone.utc) if end else None)
    except (ValueError, TypeError, OverflowError):
        raise DomainError('Choose a valid date range in YYYY-MM-DD format. Dates use Philippine time.') from None


def apply_dates(query, column, args):
    start, end = date_bounds(args)
    if start: query = query.where(column >= start)
    if end: query = query.where(column < end)
    return query


def search_text(args):
    value = args.get('q', '').strip()
    if len(value) > 120:
        raise DomainError('Search must be 120 characters or fewer.')
    # Wildcards are literal user input, never arbitrary SQL patterns.
    return '%'+value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')+'%' if value else None


def transaction_query(ids, user, args):
    query = select(Transaction, LoadRequest.reference).outerjoin(LoadRequest, Transaction.load_request_id == LoadRequest.id).join(Unit, Unit.id == Transaction.unit_id)
    query = query.where(Transaction.unit_id.in_(ids)).options(joinedload(Transaction.unit))
    if user.role == 'tenant':
        query = query.where(Transaction.created_at >= user.created_at)
    kind = args.get('kind', '')
    if kind and kind not in ('credit', 'consumption', 'demo_credit'):
        raise DomainError('Choose credit additions or energy deductions.')
    if kind: query = query.where(Transaction.kind == kind)
    query = apply_dates(query, Transaction.created_at, args)
    text = search_text(args)
    if text:
        query = query.where(or_(Transaction.description.ilike(text, escape='\\'),
                                LoadRequest.reference.ilike(text, escape='\\'), Unit.name.ilike(text, escape='\\')))
    return query.order_by(Transaction.created_at.desc(), Transaction.id.desc())


def request_query(ids, user, args):
    query = select(LoadRequest, Transaction.amount).outerjoin(Transaction, Transaction.load_request_id == LoadRequest.id).join(Unit, Unit.id == LoadRequest.unit_id)
    query = query.where(LoadRequest.unit_id.in_(ids)).options(joinedload(LoadRequest.unit), joinedload(LoadRequest.user))
    if user.role == 'tenant': query = query.where(LoadRequest.user_id == user.id)
    status = args.get('status', '')
    if status and status not in ('pending', 'approved', 'rejected'):
        raise DomainError('Choose Pending, Approved, or Rejected requests.')
    if status: query = query.where(LoadRequest.status == status)
    query = apply_dates(query, LoadRequest.created_at, args)
    text = search_text(args)
    if text: query = query.where(or_(LoadRequest.reference.ilike(text, escape='\\'), Unit.name.ilike(text, escape='\\'), Unit.room.ilike(text, escape='\\')))
    return query.order_by(LoadRequest.created_at.desc(), LoadRequest.id.desc())


def paginate(query, args, model):
    try:
        page, size = int(args.get('page', '1')), int(args.get('per_page', '25'))
        anchor = int(args['anchor']) if args.get('anchor') else None
        if not 1 <= page <= 100000 or not 1 <= size <= 100:
            raise ValueError()
        if anchor is not None and not 0 <= anchor <= 2147483647: raise ValueError()
    except (TypeError, ValueError):
        raise DomainError('Use a positive page and a page size of 1–100.') from None
    if anchor is not None: query = query.where(model.id <= anchor)
    scope = query.order_by(None).subquery()
    total, maximum = db.session.execute(select(func.count(), func.max(scope.c.id))).one()
    if anchor is None: anchor = maximum or 0
    query = query.where(model.id <= anchor)
    return db.session.execute(query.limit(size).offset((page-1)*size)).all(), dict(
        page=page, per_page=size, total=total, pages=max(1, (total+size-1)//size), timezone='Asia/Manila', anchor=anchor)
