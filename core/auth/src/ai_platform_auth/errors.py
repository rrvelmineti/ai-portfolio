"""Auth exceptions. The HTTP layer maps them to status codes; messages are client-safe."""

from __future__ import annotations


class AuthError(Exception):
    """Base class for auth errors."""


class ConfigError(AuthError):
    """Auth is misconfigured (e.g. JWT_SECRET missing or too short). Refuse to start."""


class InvalidCredentials(AuthError):
    """Login failed. One message for every cause (unknown user, wrong password, locked,
    inactive) so the response never reveals which one applied."""

    def __init__(self) -> None:
        super().__init__("Invalid credentials")


class TokenInvalid(AuthError):
    """An access or refresh token is malformed, expired, revoked or reused."""

    def __init__(self, message: str = "Invalid or expired token") -> None:
        super().__init__(message)


class PermissionDenied(AuthError):
    """Authenticated but not allowed."""

    def __init__(self, message: str = "Not allowed") -> None:
        super().__init__(message)


class NotFound(AuthError):
    """Resource not found (also used for other tenants' resources, so existence never leaks)."""

    def __init__(self, message: str = "Not found") -> None:
        super().__init__(message)


class Conflict(AuthError):
    """Uniqueness conflict, e.g. a username that already exists in the tenant."""


class WeakPassword(AuthError):
    """Password does not meet the policy."""


class RateLimitExceeded(AuthError):
    """Too many attempts; ``retry_after`` is in seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many attempts, try again later")
        self.retry_after = retry_after
