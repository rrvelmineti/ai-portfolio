"""Injection and canary checks plugged into ``GuardrailsGuard``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ai_platform_gateway.errors import GuardrailBlocked, GuardrailError
from ai_platform_gateway.guards import InjectionMode, PreparedCall
from ai_platform_gateway.types import Message

from ai_platform_guardrails.events import record_event
from ai_platform_guardrails.injection.classifier import (
    InjectionVerdict,
    classifier_enabled,
    classify,
)
from ai_platform_guardrails.injection.output import (
    CANARY_LINE,
    canary_leaked,
    check_links,
    new_canary,
)
from ai_platform_guardrails.injection.scanner import BORDERLINE_THRESHOLD, ScanResult, scan
from ai_platform_guardrails.injection.untrusted import STANDING_INSTRUCTION, split_untrusted

Classifier = Callable[[str], InjectionVerdict]


def make_injection_check(
    classifier: Classifier | None = None, *, use_classifier: bool | None = None
) -> Callable[..., None]:
    """Input check: block attacks in chat text; flag attacks inside untrusted fences."""

    def check(
        _guard: Any,  # noqa: ANN401
        messages: list[Message],
        system: str | None,
        mode: InjectionMode,
        _metadata: dict[str, Any],
        prepared: PreparedCall,
    ) -> None:
        if mode == "off":
            return
        enabled = classifier_enabled() if use_classifier is None else use_classifier
        top = ScanResult(0.0)
        chat_texts: list[str] = []
        has_untrusted = False
        for text, is_user in [(system or "", False), *((m.text(), m.role == "user")
                                                       for m in messages)]:  # fmt: skip
            outside, segments = split_untrusted(text)
            has_untrusted = has_untrusted or bool(segments)
            if is_user:
                chat_texts.append(outside)
            for source, body in segments:
                res = scan(body)
                if res.score >= BORDERLINE_THRESHOLD:
                    prepared.flags.append(f"untrusted_injection:{source}")
                    record_event("guardrail.flagged", reasons=res.reasons, score=res.score,
                                 payload=body, kind="untrusted", source=source)  # fmt: skip
        prepared.state["untrusted"] = has_untrusted
        for text in chat_texts:
            res = scan(text)
            if res.score > top.score:
                top = res
        prepared.injection_score = top.score
        if top.score < BORDERLINE_THRESHOLD:
            return
        payload = "\n".join(chat_texts)
        if mode == "flag":
            prepared.flags += [f"injection:{r}" for r in top.reasons]
            record_event("guardrail.flagged", reasons=top.reasons, score=top.score,
                         payload=payload)  # fmt: skip
            return
        blocked = top.blocked
        reasons = list(top.reasons)
        if not blocked:  # borderline
            if enabled:
                try:
                    verdict = (classifier or classify)(payload)
                except Exception as exc:  # fail closed: can't decide -> block
                    record_event("guardrail.blocked", reasons=[*reasons, "classifier_unavailable"],
                                 score=top.score, payload=payload)  # fmt: skip
                    raise GuardrailError("injection classifier failed; call blocked") from exc
                blocked = verdict.injection and verdict.confidence >= 0.5
                reasons.append("llm_classifier" if blocked else "llm_classifier_cleared")
            if not blocked:
                prepared.flags.append("injection_borderline")
                record_event("guardrail.flagged", reasons=reasons, score=top.score,
                             payload=payload)  # fmt: skip
                return
        record_event("guardrail.blocked", reasons=reasons, score=top.score, payload=payload)
        raise GuardrailBlocked("possible prompt injection", reasons=reasons, score=top.score)

    return check


def add_canary_and_fence_rule(_guard: Any, prepared: PreparedCall) -> None:  # noqa: ANN401
    """After masking: add the standing untrusted-data rule and a canary to the system prompt."""
    parts = [prepared.system] if prepared.system else []
    if prepared.state.get("untrusted"):
        parts.append(STANDING_INSTRUCTION)
    if parts:
        token = new_canary()
        prepared.state["canary"] = token
        parts.append(CANARY_LINE.format(token=token))
        prepared.system = "\n\n".join(parts)


def canary_check(_guard: Any, prepared: PreparedCall, output: str) -> list[str]:  # noqa: ANN401
    token = prepared.state.get("canary")
    if token and canary_leaked(output, str(token)):
        record_event("guardrail.blocked", reasons=["canary_leak"], payload=output, kind="output")
        raise GuardrailBlocked("system prompt leaked (canary in output)", reasons=["canary_leak"])
    return []


def make_link_check(allowlist: Sequence[str]) -> Callable[..., list[str]]:
    def check(_guard: Any, _prepared: PreparedCall, output: str) -> list[str]:  # noqa: ANN401
        check_links(output, allowlist)
        return []

    return check
