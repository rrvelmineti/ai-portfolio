"""Gateway exception hierarchy. Every provider SDK error is mapped into one of these.

Routing policy per error type:

* ``RateLimited`` / ``ProviderUnavailable`` (incl. ``GatewayTimeout``): retried with
  backoff, then fail over to the next provider.
* ``AuthError`` / ``ModelNotFound`` / ``NotSupported``: fail over immediately.
* ``InvalidRequest``: raised immediately; another provider will not fix a bad request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_platform_gateway.types import AttemptRecord


class GatewayError(Exception):
    """Base class for all gateway errors."""

    def __init__(
        self, message: str, *, provider: str | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.status_code = status_code

    def describe(self) -> str:
        """Short, log-safe description (never includes provider-supplied text)."""
        status = f" (status={self.status_code})" if self.status_code is not None else ""
        return f"{type(self).__name__}{status}"


class RateLimited(GatewayError):
    """HTTP 429 / throttling. Retryable."""


class ProviderUnavailable(GatewayError):
    """5xx, connection failure or similar transient fault. Retryable."""


class GatewayTimeout(ProviderUnavailable):
    """The call exceeded its timeout. Retryable."""


class AuthError(GatewayError):
    """401/403 or missing/invalid credentials. Not retried; skip this provider."""


class ModelNotFound(GatewayError):
    """404: the model id is unknown to this provider. Not retried; skip this provider."""


class NotSupported(GatewayError):
    """The provider cannot do this (e.g. embeddings, image input). Skip this provider."""


class InvalidRequest(GatewayError):
    """Malformed request or bad parameters (400/422). Raised immediately."""


class SchemaValidationError(GatewayError):
    """The model's output did not validate against ``response_schema`` after one retry."""

    def __init__(self, message: str, *, errors: str, raw_text: str) -> None:
        super().__init__(message)
        self.errors = errors
        self.raw_text = raw_text


class NoProvidersConfigured(GatewayError):
    """No provider in the alias's chain has its environment variables set."""

    def __init__(self, alias: str, missing_env: dict[str, list[str]]) -> None:
        needed = "; ".join(f"{p}: {', '.join(v)}" for p, v in missing_env.items())
        super().__init__(
            f"No provider is configured for alias {alias!r}. Set the env vars for at "
            f"least one of: {needed}"
        )
        self.alias = alias
        self.missing_env = missing_env


class AllProvidersFailed(GatewayError):
    """Every configured provider in the chain failed; ``attempts`` says why."""

    def __init__(self, alias: str, attempts: list[AttemptRecord]) -> None:
        summary = "; ".join(
            f"{a.provider}/{a.model}: {a.outcome} {a.reason or ''}".strip() for a in attempts
        )
        super().__init__(f"All providers failed for alias {alias!r}: {summary}")
        self.alias = alias
        self.attempts = attempts


class GuardrailError(GatewayError):
    """A guardrail could not run (fail closed): the call was blocked, nothing was sent."""


class GuardrailBlocked(GatewayError):
    """A guardrail blocked the call (PII in ``pii="block"`` mode, injection, canary leak...).

    ``reasons`` are short, log-safe codes; ``score`` is the risk score when relevant.
    """

    def __init__(self, message: str, *, reasons: list[str], score: float | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons
        self.score = score


class PolicyViolation(GatewayError):
    """A product topic policy matched; ``refusal`` is the message to show the user."""

    def __init__(self, topic: str, refusal: str) -> None:
        super().__init__(f"blocked topic: {topic}")
        self.topic = topic
        self.refusal = refusal


RETRYABLE_ERRORS: tuple[type[GatewayError], ...] = (RateLimited, ProviderUnavailable)


def _class_for_status(status: int) -> type[GatewayError]:
    if status == 429:
        return RateLimited
    if status in (401, 403):
        return AuthError
    if status == 404:
        return ModelNotFound
    if status == 408:
        return GatewayTimeout
    if status == 409 or status >= 500:
        return ProviderUnavailable
    if 400 <= status < 500:
        return InvalidRequest
    return GatewayError


def error_from_status(status: int, message: str, provider: str) -> GatewayError:
    """Map an HTTP status code to a gateway error."""
    return _class_for_status(status)(message, provider=provider, status_code=status)
