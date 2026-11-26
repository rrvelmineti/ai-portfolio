"""Access tokens (JWT HS256) and opaque refresh tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

from ai_platform_auth.errors import TokenInvalid
from ai_platform_auth.models import Role
from ai_platform_auth.settings import AuthSettings

REQUIRED_CLAIMS = ["sub", "tenant_id", "role", "jti", "iat", "exp"]


@dataclass(frozen=True)
class AccessClaims:
    """Validated access-token claims."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: Role
    jti: str
    issued_at: datetime
    expires_at: datetime


def create_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: Role,
    settings: AuthSettings,
    now: datetime | None = None,
) -> tuple[str, AccessClaims]:
    """Sign a short-lived access token. Returns ``(token, claims)``."""
    issued = (now or datetime.now(UTC)).replace(microsecond=0)
    claims = AccessClaims(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        jti=uuid.uuid4().hex,
        issued_at=issued,
        expires_at=issued + settings.access_token_ttl,
    )
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role.value,
        "jti": claims.jti,
        "iat": int(issued.timestamp()),
        "exp": int(claims.expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, claims


def decode_access_token(token: str, settings: AuthSettings) -> AccessClaims:
    """Verify signature, algorithm, expiry and required claims; raise ``TokenInvalid``."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": REQUIRED_CLAIMS},
        )
        return AccessClaims(
            user_id=uuid.UUID(str(payload["sub"])),
            tenant_id=uuid.UUID(str(payload["tenant_id"])),
            role=Role(payload["role"]),
            jti=str(payload["jti"]),
            issued_at=datetime.fromtimestamp(int(payload["iat"]), UTC),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), UTC),
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenInvalid("Token expired") from exc
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise TokenInvalid() from exc


def new_refresh_token() -> tuple[str, str]:
    """Return ``(raw_token, token_hash)``. Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    """SHA-256 is enough here: the token is 256 bits of randomness, not a password."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
