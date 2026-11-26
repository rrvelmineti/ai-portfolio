"""The Provider protocol and helpers shared by adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ai_platform_common.config import missing

from ai_platform_gateway.errors import (
    GatewayError,
    GatewayTimeout,
    NotSupported,
    ProviderUnavailable,
    error_from_status,
)
from ai_platform_gateway.types import Message


@dataclass(frozen=True)
class CompletionRequest:
    """What the gateway asks an adapter to do. ``temperature`` is None when dropped."""

    model: str
    messages: list[Message]
    system: str | None
    max_tokens: int
    temperature: float | None
    timeout_s: float
    json_mode: bool = False


@dataclass(frozen=True)
class EmbeddingRequest:
    """Embedding request for an adapter."""

    model: str
    texts: list[str]
    dimensions: int | None
    timeout_s: float


@dataclass(frozen=True)
class ProviderCompletion:
    """Normalised completion returned by an adapter."""

    text: str
    input_tokens: int
    output_tokens: int
    finish_reason: str | None = None


@dataclass(frozen=True)
class ProviderEmbedding:
    """Normalised embedding response. ``tokens`` is None when the provider omits usage."""

    vectors: list[list[float]]
    tokens: int | None = None
    extra: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class Provider(Protocol):
    """Every adapter implements this. Adapters must map SDK errors to gateway errors."""

    name: str

    def missing_env(self, environ: Mapping[str, str] | None = None) -> list[str]:
        """Env vars that must be set before this provider can be used."""
        ...

    def complete(self, request: CompletionRequest) -> ProviderCompletion:
        """Run one chat completion (single attempt; the gateway handles retries)."""
        ...

    def embed(self, request: EmbeddingRequest) -> ProviderEmbedding:
        """Embed texts, or raise ``NotSupported``."""
        ...


class BaseProvider:
    """Convenience base: env-var availability and a default ``embed`` that refuses."""

    name: str = "base"
    required_env: tuple[str, ...] = ()

    def missing_env(self, environ: Mapping[str, str] | None = None) -> list[str]:
        """Env vars from ``required_env`` that are unset."""
        return missing(self.required_env, environ)

    def embed(self, request: EmbeddingRequest) -> ProviderEmbedding:
        """Providers without an embeddings API raise ``NotSupported``."""
        raise NotSupported(f"{self.name} does not support embeddings", provider=self.name)

    def reject_images(self, request: CompletionRequest) -> None:
        """Raise ``NotSupported`` if the request contains images."""
        if any(m.has_images() for m in request.messages):
            raise NotSupported(f"{self.name} adapter does not accept images", provider=self.name)


def map_transport_error(exc: BaseException, provider: str) -> GatewayError | None:
    """Map httpx/httpx2 timeouts and connection errors; None if not a transport error."""
    import httpx
    import httpx2

    if isinstance(exc, httpx.TimeoutException | httpx2.TimeoutException):
        return GatewayTimeout("request timed out", provider=provider)
    if isinstance(exc, httpx.TransportError | httpx2.TransportError):
        return ProviderUnavailable("connection error", provider=provider)
    return None


def map_stainless_error(exc: Exception, provider: str, sdk: object) -> GatewayError:
    """Map errors from the Anthropic / OpenAI / Groq SDKs (they share one shape)."""
    timeout_cls = getattr(sdk, "APITimeoutError")  # noqa: B009
    conn_cls = getattr(sdk, "APIConnectionError")  # noqa: B009
    status_cls = getattr(sdk, "APIStatusError")  # noqa: B009
    if isinstance(exc, timeout_cls):
        return GatewayTimeout("request timed out", provider=provider)
    if isinstance(exc, conn_cls):
        return ProviderUnavailable("connection error", provider=provider)
    if isinstance(exc, status_cls):
        status = int(getattr(exc, "status_code", 0))
        return error_from_status(status, str(exc), provider)
    transport = map_transport_error(exc, provider)
    if transport is not None:
        return transport
    return GatewayError(f"unexpected {type(exc).__name__}", provider=provider)
