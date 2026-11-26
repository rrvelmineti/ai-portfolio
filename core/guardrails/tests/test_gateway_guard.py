"""The real guardrails guard behind the gateway (integration: loads Presidio)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from ai_platform_common.context import request_context
from ai_platform_gateway import (
    Gateway,
    GuardrailBlocked,
    GuardrailError,
    Message,
    hooks,
    set_guard,
)
from ai_platform_gateway.guards import get_guard
from ai_platform_gateway.hooks import CallContent
from ai_platform_gateway.testing import FakeProvider, completion, fake_gateway
from ai_platform_gateway.types import ImagePart, TextPart
from ai_platform_guardrails import gateway_guard
from ai_platform_guardrails.gateway_guard import (
    GuardrailsGuard,
    default_guard,
    install_guard,
    mask_call_content,
)
from ai_platform_guardrails.pii import unmask
from ai_platform_observability.redaction import redact_content, set_content_redactor

PROMPT = "Draft a letter to member Ana Ruiz (SSN 536-22-8471) about claim CLM-2024-0012345."


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    saved = list(hooks.on_call_complete)
    yield
    set_guard(None)
    set_content_redactor(None)
    hooks.on_call_complete[:] = saved


def gw(output: str = "ok") -> tuple[Gateway, FakeProvider]:
    p = FakeProvider("anthropic", [completion(output)])
    return fake_gateway({"anthropic": p}, guard=GuardrailsGuard()), p


def test_provider_never_sees_raw_pii_and_unmask_restores() -> None:
    gateway, provider = gw("Dear <PERSON_1>, claim <CLAIM_NUMBER_1> is approved.")
    with request_context("r-guard"):
        res = gateway.complete(PROMPT, system="You write letters for member Ana Ruiz.")
        sent = provider.requests[0]
        assert (
            "Ana Ruiz" not in sent.messages[0].text()
            and "536-22-8471" not in sent.messages[0].text()
        )
        assert sent.system is not None and "Ana Ruiz" not in sent.system
        assert "<PERSON_1>" in sent.system and "<PERSON_1>" in sent.messages[0].text()  # shared
        assert res.pii_entities == {"PERSON": 2, "US_SSN": 1, "CLAIM_NUMBER": 1}
        assert res.mapping_id is not None
        letter = unmask(res.text, res.mapping_id)
    assert letter == "Dear Ana Ruiz, claim CLM-2024-0012345 is approved."
    assert res.output_flags == []


def test_images_untouched_and_text_parts_masked() -> None:
    gateway, provider = gw()
    img = ImagePart.from_bytes(b"\x89PNG", "image/png")
    msg = Message(role="user", content=[TextPart(text="Member Ana Ruiz's receipt"), img])
    gateway.complete([msg])
    parts = provider.requests[0].messages[0].parts()
    assert parts[1] == img and "Ana Ruiz" not in parts[0].text  # type: ignore[union-attr]


def test_pii_block_mode() -> None:
    gateway, provider = gw()
    with pytest.raises(GuardrailBlocked) as info:
        gateway.complete(PROMPT, pii="block")
    assert "pii_detected:US_SSN" in info.value.reasons and provider.requests == []
    assert "536-22-8471" not in str(info.value)
    gateway.complete("What is a deductible?", pii="block")


def test_pii_off_sends_raw_with_reason() -> None:
    gateway, provider = gw()
    gateway.complete("Tim Cook is CEO per the 10-K.", pii="off",
                     pii_reason="public SEC filing")  # fmt: skip
    assert "Tim Cook" in provider.requests[0].messages[0].text()


def test_output_pii_not_in_input_is_flagged() -> None:
    gateway, _ = gw("Contact <PERSON_1> or call Dr. Maria Lopez at (312) 555-0199.")
    with request_context("r-out"):
        res = gateway.complete("Who handles member Ana Ruiz?")
    assert "output_pii:PERSON" in res.output_flags and "output_pii:PHONE" in res.output_flags
    gateway2, _ = gw("Tim Cook said so.")
    res2 = gateway2.complete("Summarise what Tim Cook said.", pii="off",
                             pii_reason="public filing")  # fmt: skip
    assert res2.output_flags == []  # the name was in the input


def test_embed_masked() -> None:
    p = FakeProvider("openai")
    gateway = fake_gateway({"openai": p}, guard=GuardrailsGuard())
    res = gateway.embed(["Member Ana Ruiz, SSN 536-22-8471"])
    assert p.embed_requests[0].texts == ["Member <PERSON_1>, SSN <US_SSN_1>"]
    assert res.pii_entities == {"PERSON": 1, "US_SSN": 1}
    with pytest.raises(GuardrailBlocked):
        gateway.embed("SSN 536-22-8471", pii="block")
    gateway.embed("Tim Cook", pii="off", pii_reason="public filing")
    assert p.embed_requests[-1].texts == ["Tim Cook"]


def test_presidio_failure_blocks_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("model missing")

    monkeypatch.setattr("ai_platform_guardrails.pii.engine.get_analyzer", boom)
    gateway, provider = gw()
    with pytest.raises(GuardrailError):
        gateway.complete(PROMPT)
    assert provider.requests == []


def test_default_guard_is_loaded_lazily_and_registers_redactor() -> None:
    set_guard(None)
    guard = get_guard()  # imports ai_platform_guardrails.gateway_guard:default_guard
    assert isinstance(guard, GuardrailsGuard)
    content = CallContent(system="Member Ana Ruiz", messages=[{"role": "user",
                          "content": "SSN 536-22-8471"}], output="Ana Ruiz is fine")  # fmt: skip
    red = redact_content(content)
    assert "Ana Ruiz" not in str(red) and "536-22-8471" not in str(red)
    assert isinstance(default_guard(), GuardrailsGuard)
    installed = install_guard(GuardrailsGuard())
    assert get_guard() is installed


def test_redactor_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_guard, "mask_text",
                        lambda *a, **k: (_ for _ in ()).throw(GuardrailError("x")))  # fmt: skip
    with pytest.raises(GuardrailError):
        mask_call_content(CallContent(system="Ana Ruiz"))
