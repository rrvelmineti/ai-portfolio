"""Test helpers: scripted fake providers (no network, no SDKs, no keys).

Products can use these in their own unit tests::

    from ai_platform_gateway.testing import FakeProvider, fake_gateway

    gw = fake_gateway({"anthropic": FakeProvider("anthropic", ["ok"])})
    set_gateway(gw)
"""

from __future__ import annotations

from collections.abc import Callable

from ai_platform_gateway.config import GatewayConfig, default_config
from ai_platform_gateway.errors import NotSupported
from ai_platform_gateway.gateway import Gateway
from ai_platform_gateway.guards import Guard, PassthroughGuard
from ai_platform_gateway.providers.base import (
    BaseProvider,
    CompletionRequest,
    EmbeddingRequest,
    ProviderCompletion,
    ProviderEmbedding,
)
from ai_platform_gateway.providers.registry import ProviderRegistry
from ai_platform_gateway.retry import RetryPolicy

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
        if not isinstance(step, ProviderCompletion):
            raise TypeError(f"bad scripted step {step!r}")
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
        if not isinstance(step, ProviderEmbedding):
            raise TypeError(f"bad scripted step {step!r}")
        return step


class NoEmbedProvider(FakeProvider):
    """A chat-only provider."""

    def embed(self, request: EmbeddingRequest) -> ProviderEmbedding:
        raise NotSupported(f"{self.name} does not support embeddings", provider=self.name)


def completion(text: str, inp: int = 10, out: int = 5) -> ProviderCompletion:
    """A scripted completion step."""
    return ProviderCompletion(text=text, input_tokens=inp, output_tokens=out)


GatewayFactory = Callable[..., Gateway]


def fake_gateway(
    providers: dict[str, FakeProvider],
    *,
    config: GatewayConfig | None = None,
    environ: dict[str, str] | None = None,
    guard: Guard | None = None,
) -> Gateway:
    """A Gateway wired to fake providers, with instant (no-sleep) retries.

    Providers named in the config but not passed in are treated as unconfigured
    (skipped like a provider without keys). ``guard`` defaults to ``PassthroughGuard``
    so product unit tests don't load the Presidio model; pass the real guardrails guard
    in integration tests.
    """
    cfg = config or default_config()
    registry = ProviderRegistry(factories={})
    for entry in cfg.models.values():
        if entry.provider not in providers:
            registry.register(
                entry.provider,
                FakeProvider(entry.provider, required_env=("FAKE_PROVIDER_NOT_CONFIGURED",)),
            )
    for name, provider in providers.items():
        registry.register(name, provider)
    return Gateway(
        config=cfg,
        registry=registry,
        retry_policy=RetryPolicy(sleep=lambda _s: None),
        environ=environ or {},
        guard=guard or PassthroughGuard(),
    )
