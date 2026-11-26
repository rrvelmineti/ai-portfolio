"""Groq adapter using the official ``groq`` SDK (Chat Completions shape)."""

from __future__ import annotations

import groq
from ai_platform_common.config import get_env

from ai_platform_gateway.providers.base import (
    BaseProvider,
    CompletionRequest,
    ProviderCompletion,
)
from ai_platform_gateway.providers.openai import chat_completion


class GroqProvider(BaseProvider):
    """Groq chat. No embeddings; image input raises ``NotSupported``."""

    name = "groq"
    required_env = ("GROQ_API_KEY",)

    def __init__(self, client: groq.Groq | None = None) -> None:
        self._client = client

    def client(self) -> groq.Groq:
        """Lazily build the SDK client with SDK retries disabled."""
        if self._client is None:
            self._client = groq.Groq(api_key=get_env("GROQ_API_KEY"), max_retries=0)
        return self._client

    def complete(self, request: CompletionRequest) -> ProviderCompletion:
        self.reject_images(request)
        return chat_completion(self.client(), request, self.name, groq)
