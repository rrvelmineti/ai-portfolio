"""Google Gemini adapter using the official ``google-genai`` SDK."""

from __future__ import annotations

from typing import Any

from ai_platform_common.config import get_env
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from ai_platform_gateway.errors import GatewayError, error_from_status
from ai_platform_gateway.providers.base import (
    BaseProvider,
    CompletionRequest,
    EmbeddingRequest,
    ProviderCompletion,
    ProviderEmbedding,
    map_transport_error,
)
from ai_platform_gateway.types import ImagePart, Message


def map_gemini_error(exc: Exception, provider: str = "gemini") -> GatewayError:
    """Map google-genai and transport errors to gateway errors."""
    if isinstance(exc, genai_errors.APIError):
        return error_from_status(int(exc.code or 0), str(exc), provider)
    transport = map_transport_error(exc, provider)
    if transport is not None:
        return transport
    return GatewayError(f"unexpected {type(exc).__name__}", provider=provider)


def to_gemini_content(message: Message) -> genai_types.Content:
    """Convert a gateway message; Gemini calls the assistant role ``model``."""
    parts: list[genai_types.Part] = []
    for part in message.parts():
        if isinstance(part, ImagePart):
            parts.append(
                genai_types.Part.from_bytes(data=part.raw_bytes(), mime_type=part.media_type)
            )
        else:
            parts.append(genai_types.Part.from_text(text=part.text))
    return genai_types.Content(role="model" if message.role == "assistant" else "user", parts=parts)


def _http_options(timeout_s: float) -> genai_types.HttpOptions:
    return genai_types.HttpOptions(timeout=int(timeout_s * 1000))


class GeminiProvider(BaseProvider):
    """Gemini chat + embeddings (``GEMINI_API_KEY``)."""

    name = "gemini"
    required_env = ("GEMINI_API_KEY",)

    def __init__(self, client: genai.Client | None = None) -> None:
        self._client = client

    def client(self) -> genai.Client:
        """Lazily build the SDK client; SDK retries limited to one attempt."""
        if self._client is None:
            self._client = genai.Client(
                api_key=get_env("GEMINI_API_KEY"),
                http_options=genai_types.HttpOptions(
                    retry_options=genai_types.HttpRetryOptions(attempts=1)
                ),
            )
        return self._client

    def complete(self, request: CompletionRequest) -> ProviderCompletion:
        config: dict[str, Any] = {
            "max_output_tokens": request.max_tokens,
            "http_options": _http_options(request.timeout_s),
        }
        if request.system:
            config["system_instruction"] = request.system
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.json_mode:
            config["response_mime_type"] = "application/json"
        try:
            resp = self.client().models.generate_content(
                model=request.model,
                contents=[to_gemini_content(m) for m in request.messages],
                config=genai_types.GenerateContentConfig(**config),
            )
        except GatewayError:
            raise
        except Exception as exc:
            raise map_gemini_error(exc, self.name) from exc
        usage = resp.usage_metadata
        output = 0
        if usage is not None:
            output = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        finish = None
        if resp.candidates and resp.candidates[0].finish_reason is not None:
            finish = str(resp.candidates[0].finish_reason.value)
        return ProviderCompletion(
            text=resp.text or "",
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=output,
            finish_reason=finish,
        )

    def embed(self, request: EmbeddingRequest) -> ProviderEmbedding:
        config: dict[str, Any] = {"http_options": _http_options(request.timeout_s)}
        if request.dimensions:
            config["output_dimensionality"] = request.dimensions
        try:
            resp = self.client().models.embed_content(
                model=request.model,
                contents=list(request.texts),
                config=genai_types.EmbedContentConfig(**config),
            )
        except GatewayError:
            raise
        except Exception as exc:
            raise map_gemini_error(exc, self.name) from exc
        vectors = [list(e.values or []) for e in (resp.embeddings or [])]
        tokens: int | None = None
        usage = getattr(resp, "usage_metadata", None)
        if usage is not None and getattr(usage, "prompt_token_count", None) is not None:
            tokens = int(usage.prompt_token_count)
        return ProviderEmbedding(vectors=vectors, tokens=tokens)
