from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from ai_platform_gateway import hooks
from ai_platform_gateway.config import GatewayConfig, load_config
from ai_platform_gateway.gateway import Gateway
from ai_platform_gateway.providers.registry import ProviderRegistry
from ai_platform_gateway.retry import RetryPolicy
from ai_platform_gateway.testing import FakeProvider, GatewayFactory

TEST_MODELS = """
models:
  a/chat:   {provider: a, model: a-chat, price_input_per_1m: 1.0, price_output_per_1m: 2.0,
             supports_images: true}
  b/chat:   {provider: b, model: b-chat, price_input_per_1m: 3.0, price_output_per_1m: 6.0,
             supports_temperature: false}
  c/chat:   {provider: c, model: c-chat, price_input_per_1m: null, price_output_per_1m: null,
             supports_images: true}
  a/embed:  {provider: a, model: a-embed, kind: embed, price_input_per_1m: 0.5}
  b/embed:  {provider: b, model: b-embed, kind: embed, price_input_per_1m: 0.1}
  c/embed:  {provider: c, model: c-embed, kind: embed, price_input_per_1m: 0.2,
             supported_dimensions: [256]}
  d/chat:   {provider: d, model: "${D_DEPLOYMENT}", price_input_per_1m: 1.0,
             price_output_per_1m: 1.0}
aliases:
  main:    {kind: chat, fallback: [a/chat, b/chat, c/chat]}
  single:  {kind: chat, fallback: [a/chat]}
  nocost:  {kind: chat, fallback: [c/chat]}
  envmod:  {kind: chat, fallback: [d/chat]}
  vec:     {kind: embed, dimensions: 4, fallback: [a/embed, b/embed]}
  vec256:  {kind: embed, dimensions: 4, fallback: [c/embed, b/embed]}
"""


@pytest.fixture
def test_config(tmp_path: Path) -> GatewayConfig:
    path = tmp_path / "models.yaml"
    path.write_text(TEST_MODELS)
    return load_config(path)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def make_gateway(test_config: GatewayConfig, sleeps: list[float]) -> GatewayFactory:
    """Factory: make_gateway(providers=[...], environ={...}) -> Gateway."""

    def factory(providers: list[FakeProvider], environ: dict[str, str] | None = None) -> Gateway:
        registry = ProviderRegistry(factories={})
        for p in providers:
            registry.register(p.name, p)
        return Gateway(
            config=test_config,
            registry=registry,
            retry_policy=RetryPolicy(sleep=sleeps.append),
            environ=environ or {},
        )

    return factory


@pytest.fixture(autouse=True)
def _clean_hooks() -> Iterator[None]:
    saved = list(hooks.on_call_complete)
    hooks.on_call_complete.clear()
    yield
    hooks.on_call_complete[:] = saved
