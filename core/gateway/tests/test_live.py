"""Live provider calls. Excluded by default; run with ``uv run pytest -m live``."""

from __future__ import annotations

import pytest
from ai_platform_gateway.config import default_config
from ai_platform_gateway.gateway import Gateway
from ai_platform_gateway.providers.registry import ProviderRegistry

pytestmark = pytest.mark.live

PROVIDERS = ["anthropic", "openai", "azure_openai", "bedrock", "gemini", "groq"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_live_complete(provider: str) -> None:
    registry = ProviderRegistry()
    cfg = default_config()
    entry = next(m for m in cfg.models.values() if m.provider == provider and m.kind == "chat")
    missing = registry.get(provider).missing_env() + entry.missing_env()
    if missing:
        pytest.skip(f"{provider}: missing {', '.join(missing)}")
    alias = next(a for a, c in cfg.aliases.items() if c.fallback and c.fallback[0] == entry.key)
    res = Gateway(config=cfg, registry=registry).complete(
        "Reply with the single word: pong", alias, max_tokens=16
    )
    assert res.text.strip()
    assert res.input_tokens > 0
