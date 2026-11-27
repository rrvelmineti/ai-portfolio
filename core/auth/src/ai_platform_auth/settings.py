"""Auth settings, read from the environment. There is no default JWT secret anywhere."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

from ai_platform_common.config import get_bool, get_env
from ai_platform_common.context import ProxyNetworks, parse_proxy_cidrs

from ai_platform_auth.errors import ConfigError

MIN_SECRET_BYTES = 32


def _int(name: str, default: int, environ: Mapping[str, str] | None) -> int:
    raw = get_env(name, environ)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class AuthSettings:
    """Validated auth configuration. ``repr`` never shows the secret."""

    jwt_secret: bytes = field(repr=False)
    access_token_ttl: timedelta = timedelta(minutes=15)
    refresh_token_ttl: timedelta = timedelta(days=7)
    max_failed_logins: int = 5
    lockout_duration: timedelta = timedelta(minutes=15)
    login_rate_limit: int = 10
    login_rate_window_s: int = 60
    trust_proxy_headers: bool = False
    trusted_proxy_cidrs: tuple[str, ...] = ()
    jwt_algorithm: str = "HS256"

    def __post_init__(self) -> None:
        if len(self.jwt_secret) < MIN_SECRET_BYTES:
            raise ConfigError(f"JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")
        if self.trust_proxy_headers and not self.trusted_proxy_cidrs:
            raise ConfigError(
                "TRUST_PROXY_HEADERS=true needs TRUSTED_PROXY_CIDRS (the proxy's address "
                "ranges); otherwise any visitor could fake X-Forwarded-For"
            )
        try:
            parse_proxy_cidrs(self.trusted_proxy_cidrs)
        except ValueError as exc:
            raise ConfigError(f"TRUSTED_PROXY_CIDRS is invalid: {exc}") from exc

    @property
    def trusted_proxies(self) -> ProxyNetworks:
        """Proxy networks whose X-Forwarded-For is honoured (empty unless enabled)."""
        return parse_proxy_cidrs(self.trusted_proxy_cidrs) if self.trust_proxy_headers else ()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthSettings:
        """Build settings from env vars; raises ``ConfigError`` if JWT_SECRET is unusable."""
        secret = get_env("JWT_SECRET", environ)
        if secret is None:
            raise ConfigError("JWT_SECRET is not set; refusing to start")
        return cls(
            jwt_secret=secret.encode("utf-8"),
            access_token_ttl=timedelta(minutes=_int("ACCESS_TOKEN_TTL_MINUTES", 15, environ)),
            refresh_token_ttl=timedelta(days=_int("REFRESH_TOKEN_TTL_DAYS", 7, environ)),
            max_failed_logins=_int("MAX_FAILED_LOGINS", 5, environ),
            lockout_duration=timedelta(minutes=_int("LOCKOUT_MINUTES", 15, environ)),
            login_rate_limit=_int("LOGIN_RATE_LIMIT", 10, environ),
            login_rate_window_s=_int("LOGIN_RATE_WINDOW_SECONDS", 60, environ),
            trust_proxy_headers=get_bool("TRUST_PROXY_HEADERS", False, environ),
            trusted_proxy_cidrs=tuple(
                (get_env("TRUSTED_PROXY_CIDRS", environ) or "").replace(",", " ").split()
            ),
        )
