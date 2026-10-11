"""Account validation and session revocation; no email transport is required."""
import re
from .billing import DomainError


def identity(name, email):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise DomainError('Enter a name of 1–100 characters.')
    if not isinstance(email, str) or len(email.strip()) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email.strip()):
        raise DomainError('Enter a valid email address.')
    return name.strip(), email.strip().lower()


def password_value(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise DomainError('Use a password of 12–128 characters.')
    return value


def change_password(user, value, temporary=False):
    user.set_password(password_value(value))
    user.must_change_password = temporary
    user.session_version += 1
