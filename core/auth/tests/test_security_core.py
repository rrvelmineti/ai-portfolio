"""Settings, password hashing and token tests (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from ai_platform_auth.errors import ConfigError, TokenInvalid, WeakPassword
from ai_platform_auth.models import Role
from ai_platform_auth.passwords import (
    dummy_verify,
    hash_password,
    validate_password,
    verify_password,
)
from ai_platform_auth.settings import AuthSettings
from ai_platform_auth.tokens import (
    create_access_token,
    decode_access_token,
    hash_refresh_token,
    new_refresh_token,
)

SECRET = "test-secret-" + "x" * 40  # noqa: S105 - test-only value
SETTINGS = AuthSettings(jwt_secret=SECRET.encode())
GOOD_PW = "correct horse battery"  # noqa: S105 - synthetic


# ------------------------------------------------------------------ settings
def test_settings_require_jwt_secret() -> None:
    with pytest.raises(ConfigError, match="not set"):
        AuthSettings.from_env({})
    with pytest.raises(ConfigError, match="at least 32 bytes"):
        AuthSettings.from_env({"JWT_SECRET": "short"})
    with pytest.raises(ConfigError, match="at least 32 bytes"):
        AuthSettings(jwt_secret=b"x" * 31)


def test_settings_from_env_and_overrides() -> None:
    s = AuthSettings.from_env(
        {"JWT_SECRET": SECRET, "LOCKOUT_MINUTES": "30", "TRUST_PROXY_HEADERS": "true",
         "TRUSTED_PROXY_CIDRS": "10.0.0.0/8, 192.0.2.0/24"}
    )  # fmt: skip
    assert s.access_token_ttl == timedelta(minutes=15)
    assert s.refresh_token_ttl == timedelta(days=7)
    assert s.max_failed_logins == 5
    assert s.lockout_duration == timedelta(minutes=30)
    assert s.trust_proxy_headers is True
    assert [str(n) for n in s.trusted_proxies] == ["10.0.0.0/8", "192.0.2.0/24"]
    with pytest.raises(ConfigError, match="needs TRUSTED_PROXY_CIDRS"):
        AuthSettings.from_env({"JWT_SECRET": SECRET, "TRUST_PROXY_HEADERS": "true"})
    with pytest.raises(ConfigError, match="TRUSTED_PROXY_CIDRS is invalid"):
        AuthSettings.from_env({"JWT_SECRET": SECRET, "TRUST_PROXY_HEADERS": "true",
                               "TRUSTED_PROXY_CIDRS": "10.0.0.0/33"})  # fmt: skip
    off = AuthSettings.from_env({"JWT_SECRET": SECRET, "TRUSTED_PROXY_CIDRS": "10.0.0.0/8"})
    assert off.trusted_proxies == ()  # ranges alone don't switch trust on
    with pytest.raises(ConfigError, match="integer"):
        AuthSettings.from_env({"JWT_SECRET": SECRET, "MAX_FAILED_LOGINS": "five"})
    with pytest.raises(ConfigError, match="positive"):
        AuthSettings.from_env({"JWT_SECRET": SECRET, "MAX_FAILED_LOGINS": "0"})


def test_settings_repr_hides_secret() -> None:
    assert SECRET not in repr(SETTINGS)
    assert "jwt_secret" not in repr(SETTINGS)


# ------------------------------------------------------------------ passwords
def test_password_policy() -> None:
    with pytest.raises(WeakPassword, match="at least 12"):
        validate_password("elevenchars")
    with pytest.raises(WeakPassword, match="at most 128"):
        validate_password("x" * 129)
    validate_password("x" * 12)


def test_hash_and_verify() -> None:
    h = hash_password(GOOD_PW)
    assert h.startswith("$argon2id$")
    assert GOOD_PW not in h
    assert verify_password(GOOD_PW, h) == (True, None)
    assert verify_password("wrong password!!", h)[0] is False
    assert verify_password(GOOD_PW, "not-a-hash") == (False, None)
    assert hash_password(GOOD_PW) != h  # salted
    dummy_verify(GOOD_PW)


# ------------------------------------------------------------------ access tokens
def test_access_token_roundtrip() -> None:
    uid, tid = uuid.uuid4(), uuid.uuid4()
    token, claims = create_access_token(
        user_id=uid, tenant_id=tid, role=Role.ANALYST, settings=SETTINGS
    )
    decoded = decode_access_token(token, SETTINGS)
    assert decoded == claims
    assert decoded.expires_at - decoded.issued_at == timedelta(minutes=15)
    payload = jwt.decode(token, options={"verify_signature": False})
    assert set(payload) == {"sub", "tenant_id", "role", "jti", "iat", "exp"}
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_access_token_expired() -> None:
    past = datetime.now(UTC) - timedelta(minutes=16)
    token, _ = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=Role.VIEWER,
        settings=SETTINGS, now=past,
    )  # fmt: skip
    with pytest.raises(TokenInvalid, match="expired"):
        decode_access_token(token, SETTINGS)


def test_access_token_tampering_rejected() -> None:
    token, _ = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=Role.VIEWER, settings=SETTINGS
    )
    other = AuthSettings(jwt_secret=b"another-secret-" + b"y" * 40)
    with pytest.raises(TokenInvalid):
        decode_access_token(token, other)
    # "alg": "none" and missing claims are rejected
    unsigned = jwt.encode({"sub": "x"}, key=None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(TokenInvalid):
        decode_access_token(unsigned, SETTINGS)
    missing = jwt.encode({"sub": str(uuid.uuid4()), "exp": 9999999999}, SETTINGS.jwt_secret)
    with pytest.raises(TokenInvalid):
        decode_access_token(missing, SETTINGS)
    bad_role = jwt.encode(
        {"sub": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4()), "role": "god",
         "jti": "j", "iat": 1, "exp": 9999999999},
        SETTINGS.jwt_secret,
    )  # fmt: skip
    with pytest.raises(TokenInvalid):
        decode_access_token(bad_role, SETTINGS)
    with pytest.raises(TokenInvalid):
        decode_access_token("garbage", SETTINGS)


# ------------------------------------------------------------------ refresh tokens
def test_refresh_tokens_random_and_hashed() -> None:
    raw1, h1 = new_refresh_token()
    raw2, h2 = new_refresh_token()
    assert raw1 != raw2 and h1 != h2
    assert len(raw1) >= 43 and len(h1) == 64
    assert hash_refresh_token(raw1) == h1
    assert raw1 not in h1
