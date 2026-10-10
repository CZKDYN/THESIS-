"""Relational records; money uses fixed-point decimals, timestamps are UTC."""
from datetime import datetime, timezone
from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash
from .extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class Unit(db.Model):
    __tablename__ = "dormitory_units"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    room = db.Column(db.String(80), nullable=False)
    power_status = db.Column(db.String(24), nullable=False, default="unknown")
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    archived_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    account = db.relationship("Account", uselist=False, back_populates="unit")
    device = db.relationship("Device", uselist=False, back_populates="unit")


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(254), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("dormitory_units.id"), unique=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    unit = db.relationship("Unit")
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'tenant')", name="valid_user_role"),
        CheckConstraint("role != 'tenant' OR unit_id IS NOT NULL", name="tenant_requires_unit"),
    )

    @property
    def is_active(self):
        return self.active

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Device(db.Model):
    __tablename__ = "devices"
    id = db.Column(db.Integer, primary_key=True)
    identifier = db.Column(db.String(80), nullable=False, unique=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("dormitory_units.id"), nullable=False, unique=True)
    token_hash = db.Column(db.String(255))
    source = db.Column(db.String(16), nullable=False, default="hardware")
    last_seen = db.Column(db.DateTime(timezone=True))
    last_energy = db.Column(db.Numeric(16, 6))
    last_timestamp = db.Column(db.DateTime(timezone=True))
    meter_epoch = db.Column(db.String(64))
    gateway_last_seen = db.Column(db.DateTime(timezone=True))
    unit = db.relationship("Unit", back_populates="device")
    __table_args__ = (CheckConstraint("source IN ('simulation', 'hardware')", name="valid_source"),)


class Account(db.Model):
    __tablename__ = "prepaid_accounts"
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("dormitory_units.id"), nullable=False, unique=True)
    balance = db.Column(db.Numeric(16, 6), nullable=False, default=0)
    low_threshold = db.Column(db.Numeric(12, 2), nullable=False, default=100)
    unit = db.relationship("Unit", back_populates="account")


class Rate(db.Model):
    __tablename__ = "electricity_rates"
    id = db.Column(db.Integer, primary_key=True)
    php_per_kwh = db.Column(db.Numeric(10, 4), nullable=False)
    effective_at = db.Column(db.DateTime(timezone=True), nullable=False, unique=True, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    __table_args__ = (CheckConstraint("php_per_kwh > 0", name="positive_rate"),)


class Reading(db.Model):
    __tablename__ = "meter_readings"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False, index=True)
    event_id = db.Column(db.String(64), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    received_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    voltage = db.Column(db.Numeric(10, 3), nullable=False)
    current = db.Column(db.Numeric(10, 4), nullable=False)
    power = db.Column(db.Numeric(12, 3), nullable=False)
    energy = db.Column(db.Numeric(16, 6), nullable=False)
    delta_kwh = db.Column(db.Numeric(16, 6), nullable=False)
    rate_id = db.Column(db.Integer, db.ForeignKey("electricity_rates.id"), nullable=False)
    source = db.Column(db.String(16), nullable=False)
    meter_epoch = db.Column(db.String(64), nullable=False)
    connection_status = db.Column(db.String(24), nullable=False)
    __table_args__ = (
        UniqueConstraint("device_id", "event_id", name="reading_event_unique"),
        UniqueConstraint("device_id", "timestamp", name="reading_time_unique"),
        CheckConstraint("energy >= 0 AND delta_kwh >= 0", name="nonnegative_energy"),
    )


class LoadRequest(db.Model):
    __tablename__ = "load_requests"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("dormitory_units.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    reference = db.Column(db.String(64), nullable=False, unique=True)
    note = db.Column(db.String(300), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at = db.Column(db.DateTime(timezone=True))
    resolved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    unit = db.relationship("Unit")
    user = db.relationship("User", foreign_keys=[user_id])
    __table_args__ = (
        CheckConstraint("amount > 0", name="positive_load"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="valid_load_status"),
    )


class Transaction(db.Model):
    __tablename__ = "prepaid_transactions"
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("dormitory_units.id"), nullable=False, index=True)
    kind = db.Column(db.String(24), nullable=False)
    amount = db.Column(db.Numeric(16, 6), nullable=False)
    balance_after = db.Column(db.Numeric(16, 6), nullable=False)
    reading_id = db.Column(db.Integer, db.ForeignKey("meter_readings.id"), unique=True)
    load_request_id = db.Column(db.Integer, db.ForeignKey("load_requests.id"), unique=True)
    description = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    unit = db.relationship("Unit")


class Forecast(db.Model):
    __tablename__ = "forecasts"
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("dormitory_units.id"), nullable=False, index=True)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False)
    predicted_kwh = db.Column(db.Numeric(16, 6), nullable=False)
    model_version = db.Column(db.String(100), nullable=False)
    source = db.Column(db.String(16), nullable=False)
    generated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("unit_id", "timestamp", "model_version", name="forecast_unique"),)


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("dormitory_units.id"), nullable=False, index=True)
    kind = db.Column(db.String(32), nullable=False)
    message = db.Column(db.String(400), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class DeviceEvent(db.Model):
    __tablename__ = "device_events"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False, index=True)
    kind = db.Column(db.String(32), nullable=False)
    state = db.Column(db.String(32), nullable=False)
    message = db.Column(db.String(400), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(64), nullable=False, index=True)
    detail = db.Column(db.String(400), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
