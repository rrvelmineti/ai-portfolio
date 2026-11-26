"""Authentication, roles, tenants and audit log for ai-portfolio FastAPI products."""

from ai_platform_auth.api import Auth, auth_error_response
from ai_platform_auth.audit import audit
from ai_platform_auth.errors import (
    AuthError,
    ConfigError,
    Conflict,
    InvalidCredentials,
    NotFound,
    PermissionDenied,
    RateLimitExceeded,
    TokenInvalid,
    WeakPassword,
)
from ai_platform_auth.models import Role, Tenant, User
from ai_platform_auth.ratelimit import InMemoryRateLimiter, RateLimiter
from ai_platform_auth.scope import TenantScope
from ai_platform_auth.settings import AuthSettings

__all__ = [
    "Auth",
    "AuthError",
    "AuthSettings",
    "ConfigError",
    "Conflict",
    "InMemoryRateLimiter",
    "InvalidCredentials",
    "NotFound",
    "PermissionDenied",
    "RateLimitExceeded",
    "RateLimiter",
    "Role",
    "Tenant",
    "TenantScope",
    "TokenInvalid",
    "User",
    "WeakPassword",
    "audit",
    "auth_error_response",
]
