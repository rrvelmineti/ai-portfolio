"""Guard interface: every AI call passes through a guard before and after the provider.

The real guard lives in ``ai_platform_guardrails`` (PII masking, injection defence,
canary, output checks, topic policy). The gateway loads it lazily on first use, so the
two packages don't import each other at import time.

Fail closed: if no guard can be loaded, or a guard raises anything unexpected, the call
is blocked with ``GuardrailError`` and nothing is sent to a provider. Tests that don't
exercise guardrails pass ``PassthroughGuard()`` explicitly.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ai_platform_gateway.errors import GatewayError, GuardrailError
from ai_platform_gateway.types import Message

PiiMode = Literal["mask", "block", "off"]
InjectionMode = Literal["block", "flag", "off"]


@dataclass
class PreparedCall:
    """What the guard lets through, plus what it learned (no raw values)."""

    messages: list[Message]
    system: str | None
    mapping_id: str | None = None
    pii_entities: dict[str, int] = field(default_factory=dict)
    injection_score: float | None = None
    flags: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict, repr=False)  # guard-private


@dataclass
class PreparedTexts:
    texts: list[str]
    mapping_id: str | None = None
    pii_entities: dict[str, int] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


class Guard(Protocol):
    """Implemented by ``ai_platform_guardrails`` (and ``PassthroughGuard`` for tests)."""

    def before_complete(
        self,
        messages: list[Message],
        system: str | None,
        *,
        pii: PiiMode,
        injection: InjectionMode,
        metadata: dict[str, Any],
    ) -> PreparedCall:
        """Mask/scan the input. Raise ``GuardrailBlocked``/``PolicyViolation`` to block."""
        ...

    def after_complete(self, prepared: PreparedCall, output: str) -> list[str]:
        """Check the output; return flags, or raise ``GuardrailBlocked`` to block."""
        ...

    def before_embed(self, texts: list[str], *, pii: PiiMode) -> PreparedTexts:
        """Mask/scan texts before embedding."""
        ...


class PassthroughGuard:
    """No-op guard for unit tests of code that doesn't exercise guardrails."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def before_complete(
        self,
        messages: list[Message],
        system: str | None,
        *,
        pii: PiiMode,
        injection: InjectionMode,
        metadata: dict[str, Any],
    ) -> PreparedCall:
        self.calls.append(f"before_complete:{pii}:{injection}")
        return PreparedCall(messages=list(messages), system=system)

    def after_complete(self, prepared: PreparedCall, output: str) -> list[str]:
        self.calls.append("after_complete")
        return []

    def before_embed(self, texts: list[str], *, pii: PiiMode) -> PreparedTexts:
        self.calls.append(f"before_embed:{pii}")
        return PreparedTexts(texts=list(texts))


_GUARD_FACTORY = "ai_platform_guardrails.gateway_guard:default_guard"
_installed: dict[str, Guard | None] = {"guard": None}


def set_guard(guard: Guard | None) -> None:
    """Install the process-wide guard (``None`` = load the guardrails default lazily)."""
    _installed["guard"] = guard


def get_guard() -> Guard:
    """The installed guard, else the guardrails default. Raises ``GuardrailError``."""
    guard = _installed["guard"]
    if guard is not None:
        return guard
    module_name, _, attr = _GUARD_FACTORY.partition(":")
    try:
        factory: Callable[[], Guard] = getattr(importlib.import_module(module_name), attr)
        guard = factory()
    except Exception as exc:
        raise GuardrailError("guardrails unavailable; AI call blocked (fail closed)") from exc
    _installed["guard"] = guard
    return guard


def run_guarded[T](step: str, fn: Callable[[], T]) -> T:
    """Run a guard step: gateway errors pass through, anything else fails closed."""
    try:
        return fn()
    except GatewayError:
        raise
    except Exception as exc:
        raise GuardrailError(f"guardrail step {step!r} failed; call blocked") from exc
