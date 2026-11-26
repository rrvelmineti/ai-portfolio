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
    GuardrailBlocked,
    GuardrailError,
    InvalidRequest,
    ModelNotFound,
    NoProvidersConfigured,
    NotSupported,
    PolicyViolation,
    ProviderUnavailable,
    RateLimited,
    SchemaValidationError,
)
from ai_platform_gateway.gateway import Gateway, get_gateway, set_gateway
from ai_platform_gateway.guards import Guard, InjectionMode, PassthroughGuard, PiiMode, set_guard
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
    "Guard",
    "GuardrailBlocked",
    "GuardrailError",
    "ImagePart",
    "InvalidRequest",
    "Message",
    "ModelNotFound",
    "NoProvidersConfigured",
    "NotSupported",
    "PassthroughGuard",
    "PolicyViolation",
    "ProviderUnavailable",
    "RateLimited",
    "SchemaValidationError",
    "TextPart",
    "complete",
    "embed",
    "get_gateway",
    "on_call_complete",
    "set_gateway",
    "set_guard",
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
    pii: PiiMode = "mask",
    pii_reason: str | None = None,
    injection: InjectionMode = "block",
) -> CompletionResult:
    """Call a chat model by alias (see ``models.yaml``) with retries and fallback.

    Guardrails run first and fail closed: ``pii`` is ``"mask"`` (default), ``"block"`` or
    ``"off"`` (requires ``pii_reason``); ``injection`` is ``"block"``, ``"flag"`` or ``"off"``.
    """
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
        pii=pii,
        pii_reason=pii_reason,
        injection=injection,
    )


def embed(
    texts: str | list[str],
    model: str = "default-embed",
    *,
    timeout_s: float = 30,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
    pii: PiiMode = "mask",
    pii_reason: str | None = None,
) -> EmbeddingResult:
    """Embed text(s) by alias with retries and fallback (PII-masked unless ``pii="off"``)."""
    return get_gateway().embed(
        texts, model, timeout_s=timeout_s, metadata=metadata, request_id=request_id,
        pii=pii, pii_reason=pii_reason,
    )  # fmt: skip
