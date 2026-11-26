"""Model gateway: the only way products call AI models.

    from ai_platform_gateway import complete, embed

    result = complete("Summarise this claim note ...", model="default")
    vectors = embed(["chunk one", "chunk two"]).vectors

See ``platform/gateway/README.md`` for aliases, fallback and adding providers.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ai_platform_gateway.errors import (
    AllProvidersFailed,
    AuthError,
    GatewayError,
    GatewayTimeout,
    InvalidRequest,
    ModelNotFound,
    NoProvidersConfigured,
    NotSupported,
    ProviderUnavailable,
    RateLimited,
    SchemaValidationError,
)
from ai_platform_gateway.gateway import Gateway, get_gateway, set_gateway
from ai_platform_gateway.hooks import CallRecord, on_call_complete
from ai_platform_gateway.types import (
    AttemptRecord,
    CompletionResult,
    EmbeddingResult,
    ImagePart,
    Message,
    TextPart,
)

__all__ = [
    "AllProvidersFailed",
    "AttemptRecord",
    "AuthError",
    "CallRecord",
    "CompletionResult",
    "EmbeddingResult",
    "Gateway",
    "GatewayError",
    "GatewayTimeout",
    "ImagePart",
    "InvalidRequest",
    "Message",
    "ModelNotFound",
    "NoProvidersConfigured",
    "NotSupported",
    "ProviderUnavailable",
    "RateLimited",
    "SchemaValidationError",
    "TextPart",
    "complete",
    "embed",
    "get_gateway",
    "on_call_complete",
    "set_gateway",
]


def complete(
    prompt: str | list[Message],
    model: str = "default",
    *,
    system: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    response_schema: type[BaseModel] | None = None,
    timeout_s: float = 30,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> CompletionResult:
    """Call a chat model by alias (see ``models.yaml``) with retries and fallback."""
    return get_gateway().complete(
        prompt,
        model,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        response_schema=response_schema,
        timeout_s=timeout_s,
        metadata=metadata,
        request_id=request_id,
    )


def embed(
    texts: str | list[str],
    model: str = "default-embed",
    *,
    timeout_s: float = 30,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> EmbeddingResult:
    """Embed text(s) by alias with retries and fallback."""
    return get_gateway().embed(
        texts, model, timeout_s=timeout_s, metadata=metadata, request_id=request_id
    )
