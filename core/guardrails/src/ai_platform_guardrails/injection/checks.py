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
from ai_platform_guardrails.injection.decision import Decision, assess
from ai_platform_guardrails.injection.ml import ModelUnavailable, get_prompt_classifier
from ai_platform_guardrails.injection.output import (
    CANARY_LINE,
    canary_leaked,
    check_links,
    new_canary,
)
from ai_platform_guardrails.injection.untrusted import STANDING_INSTRUCTION, split_untrusted

Classifier = Callable[[str], InjectionVerdict]


def make_injection_check(
    classifier: Classifier | None = None, *, use_classifier: bool | None = None
) -> Callable[..., None]:
    """Input check: rules + ML classifier (``decision.assess``).

    Chat text is blocked or flagged; content inside untrusted fences is only flagged.
    ``classifier``/``use_classifier`` control the optional *LLM* second opinion for chat
    input in the flag band; the local ML classifier is always on unless
    ``GUARD_ML_CLASSIFIER=false``.
    """

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
        try:
            ml = get_prompt_classifier()
        except ModelUnavailable as exc:  # fail closed: the guard can't run as configured
            record_event("guardrail.blocked", reasons=["ml_classifier_unavailable"], score=None,
                         payload="")  # fmt: skip
            raise GuardrailError("injection classifier unavailable; call blocked") from exc
        enabled = classifier_enabled() if use_classifier is None else use_classifier
        chat_texts: list[str] = []
        has_untrusted = False
        for text, is_user in [(system or "", False), *((m.text(), m.role == "user")
                                                       for m in messages)]:  # fmt: skip
            outside, segments = split_untrusted(text)
            has_untrusted = has_untrusted or bool(segments)
            if is_user:
                chat_texts.append(outside)
            for source, body in segments:
                res = assess(body, ml, "untrusted")
                if res.truncated:
                    prepared.flags.append(f"untrusted_unscanned:{source}")
                if res.action != "none":
                    prepared.flags.append(f"untrusted_injection:{source}")
                    record_event("guardrail.flagged", reasons=res.reasons, score=res.score,
                                 payload=body, kind="untrusted", source=source)  # fmt: skip
        prepared.state["untrusted"] = has_untrusted
        top: Decision | None = None
        rank = {"none": 0, "flag": 1, "block": 2}
        for text in chat_texts:
            res = assess(text, ml, "chat")
            if top is None or (rank[res.action], res.score) > (rank[top.action], top.score):
                top = res
        if top is None:
            return
        prepared.injection_score = top.score
        if top.truncated:
            prepared.flags.append("injection_unscanned_tail")
        if top.action == "none":
            return
        payload = "\n".join(chat_texts)
        reasons = list(top.reasons)
        if mode == "flag":
            prepared.flags += [f"injection:{r}" for r in reasons]
            record_event("guardrail.flagged", reasons=reasons, score=top.score, payload=payload)
            return
        if top.action == "flag":
            blocked = False
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
