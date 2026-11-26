"""Per-product policy: blocked topics (with refusals), link and tool allowlists.

YAML (path from ``GUARDRAILS_POLICY`` or passed to ``install_guardrails``)::

    product: rewards-advisor
    link_allowlist: [example-bank.com]
    tool_allowlist: [lookup_offer, list_programmes]
    blocked_topics:
      - name: investment_advice
        applies_to: both            # input | output | both
        refusal: "I can't give investment advice. Please talk to a licensed adviser."
        phrases: ["investment advice", "which stocks should i buy"]
        patterns: ['\\bshould i (?:buy|sell)\\b.{0,40}\\b(?:stocks?|crypto|funds?)\\b']

Phrases are matched case-insensitively as whole words; patterns are regexes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

import yaml
from ai_platform_common.config import get_env
from ai_platform_gateway.errors import PolicyViolation
from ai_platform_gateway.guards import InjectionMode, PreparedCall
from ai_platform_gateway.types import Message
from pydantic import BaseModel, ConfigDict, Field

from ai_platform_guardrails.events import record_event


class BlockedTopic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    refusal: str
    applies_to: Literal["input", "output", "both"] = "both"
    phrases: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)

    @cached_property
    def _compiled(self) -> list[re.Pattern[str]]:
        rx = [re.compile(rf"\b{re.escape(p.strip())}\b", re.I) for p in self.phrases if p.strip()]
        rx += [re.compile(p, re.I | re.S) for p in self.patterns]
        return rx

    def matches(self, text: str) -> bool:
        return any(r.search(text) for r in self._compiled)


class ProductPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = "default"
    link_allowlist: list[str] = Field(default_factory=list)
    tool_allowlist: list[str] = Field(default_factory=list)
    blocked_topics: list[BlockedTopic] = Field(default_factory=list)

    def match(self, text: str, side: Literal["input", "output"]) -> BlockedTopic | None:
        """The first blocked topic matching ``text`` on this side, if any."""
        for topic in self.blocked_topics:
            if topic.applies_to in (side, "both") and topic.matches(text):
                return topic
        return None


def load_policy(path: Path | str | None = None) -> ProductPolicy:
    """Load a policy file (``GUARDRAILS_POLICY``), or an empty default policy."""
    target = path or get_env("GUARDRAILS_POLICY")
    if target is None:
        return ProductPolicy()
    raw: dict[str, Any] = yaml.safe_load(Path(target).read_text(encoding="utf-8")) or {}
    return ProductPolicy.model_validate(raw)


def _violation(policy: ProductPolicy, topic: BlockedTopic, side: str, text: str) -> PolicyViolation:
    record_event("guardrail.policy_blocked", reasons=[f"topic:{topic.name}", f"side:{side}"],
                 payload=text, kind=side, source=policy.product)  # fmt: skip
    return PolicyViolation(topic.name, topic.refusal)


def make_topic_input_check(policy: ProductPolicy) -> Callable[..., None]:
    def check(
        _guard: Any,  # noqa: ANN401
        messages: list[Message],
        _system: str | None,
        _mode: InjectionMode,
        _metadata: dict[str, Any],
        _prepared: PreparedCall,
    ) -> None:
        user_text = "\n".join(m.text() for m in messages if m.role == "user")
        topic = policy.match(user_text, "input")
        if topic is not None:
            raise _violation(policy, topic, "input", user_text)

    return check


def make_topic_output_check(policy: ProductPolicy) -> Callable[..., list[str]]:
    def check(_guard: Any, _prepared: PreparedCall, output: str) -> list[str]:  # noqa: ANN401
        topic = policy.match(output, "output")
        if topic is not None:
            raise _violation(policy, topic, "output", output)
        return []

    return check
