"""OpenAI adapter, plus the Chat Completions helpers reused by Azure OpenAI and Groq."""

from __future__ import annotations

from typing import Any

import groq
import openai
from ai_platform_common.config import get_env

from ai_platform_gateway.errors import GatewayError
from ai_platform_gateway.providers.base import (
    BaseProvider,
    CompletionRequest,
    EmbeddingRequest,
    ProviderCompletion,
    ProviderEmbedding,
    map_stainless_error,
)
from ai_platform_gateway.types import ImagePart, Message


def to_chat_message(message: Message) -> dict[str, Any]:
    """Convert a gateway message to the Chat Completions shape."""
    if isinstance(message.content, str):
        return {"role": message.role, "content": message.content}
    parts: list[dict[str, Any]] = []
    for part in message.parts():
        if isinstance(part, ImagePart):
            url = f"data:{part.media_type};base64,{part.data}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        else:
            parts.append({"type": "text", "text": part.text})
    return {"role": message.role, "content": parts}


def chat_completion(
    client: openai.OpenAI | groq.Groq, request: CompletionRequest, provider: str, sdk: object
) -> ProviderCompletion:
    """Run one Chat Completions call and normalise the response."""
    messages = [to_chat_message(m) for m in request.messages]
    if request.system:
        messages.insert(0, {"role": "system", "content": request.system})
    kwargs: dict[str, Any] = {
        "model": request.model,
        "messages": messages,
        "max_completion_tokens": request.max_tokens,
        "timeout": request.timeout_s,
    }
    if request.temperature is not None:
        kwargs["temperature"] = request.temperature
    if request.json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client.chat.completions.create(**kwargs)
    except GatewayError:
        raise
    except Exception as exc:
        raise map_stainless_error(exc, provider, sdk) from exc
    choice = resp.choices[0]
    usage = resp.usage
    return ProviderCompletion(
        text=choice.message.content or "",
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
        finish_reason=choice.finish_reason,
    )


def embeddings(
    client: openai.OpenAI, request: EmbeddingRequest, provider: str, sdk: object
) -> ProviderEmbedding:
    """Run one embeddings call and normalise the response."""
    kwargs: dict[str, Any] = {
        "model": request.model,
        "input": request.texts,
        "timeout": request.timeout_s,
    }
    if request.dimensions:
        kwargs["dimensions"] = request.dimensions
    try:
        resp = client.embeddings.create(**kwargs)
    except GatewayError:
        raise
    except Exception as exc:
        raise map_stainless_error(exc, provider, sdk) from exc
    ordered = sorted(resp.data, key=lambda d: d.index)
    return ProviderEmbedding(
        vectors=[list(d.embedding) for d in ordered],
        tokens=resp.usage.prompt_tokens if resp.usage else None,
    )


class OpenAIProvider(BaseProvider):
    """OpenAI chat + embeddings."""

    name = "openai"
    required_env = ("OPENAI_API_KEY",)

    def __init__(self, client: openai.OpenAI | None = None) -> None:
        self._client = client

    def client(self) -> openai.OpenAI:
        """Lazily build the SDK client with SDK retries disabled."""
        if self._client is None:
            self._client = openai.OpenAI(api_key=get_env("OPENAI_API_KEY"), max_retries=0)
        return self._client

    def complete(self, request: CompletionRequest) -> ProviderCompletion:
        return chat_completion(self.client(), request, self.name, openai)

    def embed(self, request: EmbeddingRequest) -> ProviderEmbedding:
        return embeddings(self.client(), request, self.name, openai)
