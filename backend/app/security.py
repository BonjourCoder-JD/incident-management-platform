"""Password hashing, session tokens and login rate limiting."""
import hashlib
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults: Argon2id with the RFC 9106 low-memory parameters.
_hasher = PasswordHasher()

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128  # bounds hashing cost per request
EMAIL_MAX_LENGTH = 254
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# Verified against when the email is unknown, so response time does not reveal which emails exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def burn_password_check(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def password_problem(password: str) -> str | None:
    """Returns a reason the password is unacceptable, or None."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
    if not password.strip():
        return "Password cannot be only whitespace."
    return None


def normalize_email(email: str) -> str:
    return email.strip().lower()


# Control characters and lone surrogates (PostgreSQL text cannot store NUL; none belong in an address).
_EMAIL_FORBIDDEN = re.compile('[' + chr(0) + '-' + chr(0x1f) + chr(0x7f) + '-' + chr(0x9f) + chr(0xd800) + '-' + chr(0xdfff) + ']')


def is_valid_email(email: str) -> bool:
    return len(email) <= EMAIL_MAX_LENGTH and not _EMAIL_FORBIDDEN.search(email) and bool(_EMAIL_PATTERN.match(email))


def new_token() -> str:
    """256 bits of randomness, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # Tokens are high-entropy random values, so a fast hash is appropriate (unlike passwords).
    return hashlib.sha256(token.encode()).hexdigest()

