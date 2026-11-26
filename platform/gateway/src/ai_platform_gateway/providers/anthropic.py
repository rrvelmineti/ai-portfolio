"""Anthropic (Claude) adapter using the official ``anthropic`` SDK."""

from __future__ import annotations

from typing import Any

import anthropic
from ai_platform_common.config import get_env

from ai_platform_gateway.errors import GatewayError
from ai_platform_gateway.providers.base import (
    BaseProvider,
    CompletionRequest,
    ProviderCompletion,
    map_stainless_error,
)
from ai_platform_gateway.types import ImagePart, Message


def to_anthropic_message(message: Message) -> dict[str, Any]:
    """Convert a gateway message to the Messages API shape."""
    if isinstance(message.content, str):
        return {"role": message.role, "content": message.content}
    blocks: list[dict[str, Any]] = []
    for part in message.parts():
        if isinstance(part, ImagePart):
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": part.media_type, "data": part.data},
                }
            )
        else:
            blocks.append({"type": "text", "text": part.text})
    return {"role": message.role, "content": blocks}


class AnthropicProvider(BaseProvider):
    """Claude via the Anthropic API. No embeddings API (raises ``NotSupported``)."""

    name = "anthropic"
    required_env = ("ANTHROPIC_API_KEY",)

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client

    def client(self) -> anthropic.Anthropic:
        """Lazily build the SDK client; SDK-level retries are off (the gateway retries)."""
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=get_env("ANTHROPIC_API_KEY"), max_retries=0)
        return self._client

    def complete(self, request: CompletionRequest) -> ProviderCompletion:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [to_anthropic_message(m) for m in request.messages],
            "timeout": request.timeout_s,
        }
        if request.system:
            kwargs["system"] = request.system
        if request.temperature is not None:
            # Only reached when models.yaml says the model accepts it.
            kwargs["extra_body"] = {"temperature": request.temperature}
        try:
            resp = self.client().messages.create(**kwargs)
        except GatewayError:
            raise
        except Exception as exc:
            raise map_stainless_error(exc, self.name, anthropic) from exc
        text = "".join(block.text for block in resp.content if block.type == "text")
        return ProviderCompletion(
            text=text,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            finish_reason=resp.stop_reason,
        )
