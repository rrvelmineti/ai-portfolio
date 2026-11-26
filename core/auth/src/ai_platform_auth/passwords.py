"""Password policy and Argon2 hashing (pwdlib)."""

from __future__ import annotations

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from ai_platform_auth.errors import WeakPassword

MIN_LENGTH = 12
MAX_LENGTH = 128  # bounds hashing cost per request

_hasher = PasswordHash((Argon2Hasher(),))
# Verified against when the user does not exist, so timing does not reveal that.
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-checks")


def validate_password(password: str) -> None:
    """Length-based policy (NIST SP 800-63B): 12-128 characters, no composition rules."""
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"Password must be at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise WeakPassword(f"Password must be at most {MAX_LENGTH} characters")


def hash_password(password: str) -> str:
    """Validate and hash a password with Argon2id."""
    validate_password(password)
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    """Return ``(ok, new_hash)``; ``new_hash`` is set when parameters should be upgraded."""
    try:
        return _hasher.verify_and_update(password, password_hash)
    except Exception:  # malformed hash in DB: treat as a failed check
        return False, None


def dummy_verify(password: str) -> None:
    """Spend the same time as a real check (used for unknown usernames)."""
    _hasher.verify(password, _DUMMY_HASH)
