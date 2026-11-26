"""Scripted fake providers for unit tests (no network, no SDKs)."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ai_platform_gateway.errors import NotSupported
from ai_platform_gateway.gateway import Gateway
from ai_platform_gateway.providers.base import (
    BaseProvider,
    CompletionRequest,
    EmbeddingRequest,
    ProviderCompletion,
    ProviderEmbedding,
)

Step = ProviderCompletion | ProviderEmbedding | BaseException | str


class FakeProvider(BaseProvider):
    """Returns/raises the scripted steps in order; repeats the last step when exhausted."""

    def __init__(
        self,
        name: str,
        steps: list[Step] | None = None,
        *,
        required_env: tuple[str, ...] = (),
        embed_steps: list[Step] | None = None,
        supports_embed: bool = True,
    ) -> None:
        self.name = name
        self.required_env = required_env
        self.steps = list(steps or ["ok"])
        self.embed_steps = list(embed_steps or [])
        self.supports_embed = supports_embed
        self.requests: list[CompletionRequest] = []
        self.embed_requests: list[EmbeddingRequest] = []

    @staticmethod
    def _next(steps: list[Step]) -> Step:
        return steps.pop(0) if len(steps) > 1 else steps[0]

    def complete(self, request: CompletionRequest) -> ProviderCompletion:
        self.requests.append(request)
        step = self._next(self.steps)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, str):
            return ProviderCompletion(
                text=f"{self.name}:{step}", input_tokens=100, output_tokens=50, finish_reason="stop"
            )
        assert isinstance(step, ProviderCompletion)
        return step

    def embed(self, request: EmbeddingRequest) -> ProviderEmbedding:
        if not self.supports_embed:
            return super().embed(request)
        self.embed_requests.append(request)
        if not self.embed_steps:
            dims = request.dimensions or 3
            return ProviderEmbedding(vectors=[[0.1] * dims for _ in request.texts], tokens=10)
        step = self._next(self.embed_steps)
        if isinstance(step, BaseException):
            raise step
        assert isinstance(step, ProviderEmbedding)
        return step


class NoEmbedProvider(FakeProvider):
    """A chat-only provider."""

    def embed(self, request: EmbeddingRequest) -> ProviderEmbedding:
        raise NotSupported(f"{self.name} does not support embeddings", provider=self.name)


def env(**values: str) -> Mapping[str, str]:
    """Build a fake environment."""
    return dict(values)


def completion(text: str, inp: int = 10, out: int = 5) -> ProviderCompletion:
    return ProviderCompletion(text=text, input_tokens=inp, output_tokens=out)


GatewayFactory = Callable[..., Gateway]
