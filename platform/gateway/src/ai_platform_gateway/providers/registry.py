"""Provider name -> adapter instance. Adapters (and their SDK clients) load lazily."""

from __future__ import annotations

import importlib
from collections.abc import Callable

from ai_platform_gateway.providers.base import Provider

ProviderFactory = Callable[[], Provider]

_BUILTIN: dict[str, tuple[str, str]] = {
    "anthropic": ("ai_platform_gateway.providers.anthropic", "AnthropicProvider"),
    "openai": ("ai_platform_gateway.providers.openai", "OpenAIProvider"),
    "azure_openai": ("ai_platform_gateway.providers.azure_openai", "AzureOpenAIProvider"),
    "bedrock": ("ai_platform_gateway.providers.bedrock", "BedrockProvider"),
    "gemini": ("ai_platform_gateway.providers.gemini", "GeminiProvider"),
    "groq": ("ai_platform_gateway.providers.groq", "GroqProvider"),
}


def _builtin_factory(module: str, cls: str) -> ProviderFactory:
    def factory() -> Provider:
        provider: Provider = getattr(importlib.import_module(module), cls)()
        return provider

    return factory


class ProviderRegistry:
    """Holds one adapter instance per provider name."""

    def __init__(self, factories: dict[str, ProviderFactory] | None = None) -> None:
        self._factories: dict[str, ProviderFactory] = (
            dict(factories)
            if factories is not None
            else {name: _builtin_factory(m, c) for name, (m, c) in _BUILTIN.items()}
        )
        self._instances: dict[str, Provider] = {}

    def register(self, name: str, provider: Provider) -> None:
        """Register (or replace) a provider instance, e.g. a fake in tests."""
        self._instances[name] = provider
        self._factories[name] = lambda: provider

    def names(self) -> list[str]:
        """All known provider names."""
        return sorted(self._factories)

    def get(self, name: str) -> Provider:
        """Return the adapter for ``name`` (created on first use)."""
        if name not in self._instances:
            if name not in self._factories:
                raise KeyError(f"unknown provider {name!r}")
            self._instances[name] = self._factories[name]()
        return self._instances[name]
