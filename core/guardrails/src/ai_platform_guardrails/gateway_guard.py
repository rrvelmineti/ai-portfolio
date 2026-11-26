"""The guardrails implementation of the gateway's ``Guard`` interface.

Before the provider call: topic policy, prompt-injection scan, PII masking, canary.
After it: canary leak, exfiltration links, topic policy, and new PII in the output.
Every step fails closed (the gateway turns unexpected errors into ``GuardrailError``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from ai_platform_common.logging import get_logger
from ai_platform_gateway.errors import GuardrailBlocked
from ai_platform_gateway.guards import (
    InjectionMode,
    PiiMode,
    PreparedCall,
    PreparedTexts,
    set_guard,
)
from ai_platform_gateway.hooks import CallContent
from ai_platform_gateway.types import Message, TextPart

from ai_platform_guardrails.pii.config import GuardrailsPiiConfig
from ai_platform_guardrails.pii.engine import detect
from ai_platform_guardrails.pii.masking import PLACEHOLDER_RE, MaskSession, get_config, mask_text

log = get_logger("ai_platform_guardrails.guard")

# Extension points filled by later layers (injection, canary, policy). Each takes the
# guard, the prepared call and returns nothing; raising GuardrailBlocked/PolicyViolation
# blocks the call.
InputCheck = Callable[["GuardrailsGuard", list[Message], str | None, InjectionMode,
                       dict[str, Any], PreparedCall], None]  # fmt: skip
OutputCheck = Callable[["GuardrailsGuard", PreparedCall, str], list[str]]
# Runs after masking (e.g. adding the canary, so the canary itself is never masked).
AfterMaskHook = Callable[["GuardrailsGuard", PreparedCall], None]


class GuardrailsGuard:
    """PII masking plus pluggable input/output checks."""

    def __init__(
        self,
        *,
        pii_config: GuardrailsPiiConfig | None = None,
        input_checks: list[InputCheck] | None = None,
        output_checks: list[OutputCheck] | None = None,
        after_mask: list[AfterMaskHook] | None = None,
    ) -> None:
        self.pii_config = pii_config
        self.input_checks: list[InputCheck] = list(input_checks or [])
        self.output_checks: list[OutputCheck] = list(output_checks or [])
        self.after_mask: list[AfterMaskHook] = list(after_mask or [])

    @property
    def config(self) -> GuardrailsPiiConfig:
        return self.pii_config or get_config()

    # ------------------------------------------------------------------ complete
    def before_complete(
        self,
        messages: list[Message],
        system: str | None,
        *,
        pii: PiiMode,
        injection: InjectionMode,
        metadata: dict[str, Any],
    ) -> PreparedCall:
        prepared = PreparedCall(messages=list(messages), system=system)
        for check in self.input_checks:  # policy, injection (on the original text)
            check(self, messages, system, injection, metadata, prepared)
        if pii == "block":
            self._block_if_pii([system or "", *(m.text() for m in messages)])
        elif pii == "mask":
            session = MaskSession()
            prepared.system = (
                session.mask(prepared.system, config=self.config).masked_text
                if prepared.system else prepared.system
            )  # fmt: skip
            prepared.messages = [self._mask_message(session, m) for m in prepared.messages]
            prepared.mapping_id = session.mapping_id if session.entities else None
            prepared.pii_entities = dict(Counter(e.type for e in session.entities))
        prepared.state["sent_text"] = "\n".join(
            [prepared.system or "", *(m.text() for m in prepared.messages)]
        )
        for hook in self.after_mask:
            hook(self, prepared)
        return prepared

    def after_complete(self, prepared: PreparedCall, output: str) -> list[str]:
        flags: list[str] = []
        for check in self.output_checks:  # canary, exfiltration, policy (may raise)
            flags += check(self, prepared, output)
        flags += self.output_pii_flags(output, prepared.state.get("sent_text", ""))
        return flags

    # ------------------------------------------------------------------ embed
    def before_embed(self, texts: list[str], *, pii: PiiMode) -> PreparedTexts:
        if pii == "block":
            self._block_if_pii(texts)
            return PreparedTexts(texts=list(texts))
        if pii == "off":
            return PreparedTexts(texts=list(texts))
        session = MaskSession()
        masked = [session.mask(t, config=self.config).masked_text for t in texts]
        counts = dict(Counter(e.type for e in session.entities))
        return PreparedTexts(masked, session.mapping_id if session.entities else None, counts)

    # ------------------------------------------------------------------ helpers
    def _mask_message(self, session: MaskSession, message: Message) -> Message:
        if isinstance(message.content, str):
            return Message(role=message.role, content=session.mask(
                message.content, config=self.config).masked_text)  # fmt: skip
        parts = [
            TextPart(text=session.mask(p.text, config=self.config).masked_text)
            if isinstance(p, TextPart) else p
            for p in message.parts()
        ]  # fmt: skip
        return Message(role=message.role, content=parts)

    def _block_if_pii(self, texts: list[str]) -> None:
        found: set[str] = set()
        for text in texts:
            found |= {d.entity for d in detect(text, self.config)} if text else set()
        if found:
            raise GuardrailBlocked(
                "personal data found and pii='block'",
                reasons=[f"pii_detected:{t}" for t in sorted(found)],
            )

    def output_pii_flags(self, output: str, sent_text: str) -> list[str]:
        """Flag PII in the output that the model was never given (leak or invention)."""
        if not output.strip():
            return []
        flagged: set[str] = set()
        for d in detect(output, self.config):
            value = output[d.start : d.end]
            if PLACEHOLDER_RE.fullmatch(value.strip()) or value in sent_text:
                continue
            flagged.add(d.entity)
        if flagged:
            log.warning("guardrail.output_pii", extra={"fields": {"entities": sorted(flagged)}})
        return [f"output_pii:{t}" for t in sorted(flagged)]


def mask_call_content(content: CallContent) -> CallContent:
    """Observability content redactor: mask every text before it leaves the process.

    Raises (fail closed) if masking fails, so the trace content is never sent raw.
    """

    def m(text: str | None) -> str | None:
        return mask_text(text, reversible=False).masked_text if text else text

    return CallContent(
        system=m(content.system),
        messages=[{**msg, "content": m(msg.get("content")) or ""} for msg in content.messages],
        output=m(content.output),
    )


def register_content_redactor() -> None:
    """Make LangSmith content (when enabled) always pass through PII masking first."""
    from ai_platform_observability.redaction import set_content_redactor

    set_content_redactor(mask_call_content)


def default_guard() -> GuardrailsGuard:
    """Factory the gateway calls on first use when no guard was installed explicitly."""
    from ai_platform_guardrails.install import build_guard

    guard = build_guard()
    register_content_redactor()
    return guard


def install_guard(guard: GuardrailsGuard) -> GuardrailsGuard:
    set_guard(guard)
    register_content_redactor()
    return guard
