"""Azure OpenAI adapter (``openai.AzureOpenAI``). Model ids are deployment names."""

from __future__ import annotations

import openai
from ai_platform_common.config import get_env

from ai_platform_gateway.providers.base import (
    BaseProvider,
    CompletionRequest,
    EmbeddingRequest,
    ProviderCompletion,
    ProviderEmbedding,
)
from ai_platform_gateway.providers.openai import chat_completion, embeddings

DEFAULT_API_VERSION = "2024-10-21"


class AzureOpenAIProvider(BaseProvider):
    """Azure OpenAI chat + embeddings.

    Needs ``AZURE_OPENAI_API_KEY`` and ``AZURE_OPENAI_ENDPOINT``. Deployment names come
    from ``models.yaml`` (``${AZURE_OPENAI_DEPLOYMENT}``, ``${AZURE_OPENAI_EMBED_DEPLOYMENT}``).
    ``AZURE_OPENAI_API_VERSION`` overrides the API version.
    """

    name = "azure_openai"
    required_env = ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT")

    def __init__(self, client: openai.AzureOpenAI | None = None) -> None:
        self._client = client

    def client(self) -> openai.AzureOpenAI:
        """Lazily build the SDK client with SDK retries disabled."""
        if self._client is None:
            self._client = openai.AzureOpenAI(
                api_key=get_env("AZURE_OPENAI_API_KEY"),
                azure_endpoint=get_env("AZURE_OPENAI_ENDPOINT") or "",
                api_version=get_env("AZURE_OPENAI_API_VERSION") or DEFAULT_API_VERSION,
                max_retries=0,
            )
        return self._client

    def complete(self, request: CompletionRequest) -> ProviderCompletion:
        return chat_completion(self.client(), request, self.name, openai)

    def embed(self, request: EmbeddingRequest) -> ProviderEmbedding:
        return embeddings(self.client(), request, self.name, openai)
