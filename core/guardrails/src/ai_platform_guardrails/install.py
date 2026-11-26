"""Building and installing the guardrails guard."""

from __future__ import annotations

from ai_platform_guardrails.gateway_guard import GuardrailsGuard


def build_guard() -> GuardrailsGuard:
    """The default guard from environment configuration."""
    return GuardrailsGuard()
