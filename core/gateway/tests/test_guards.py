"""The gateway <-> guard contract: fail closed, pii modes, reason logging."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from ai_platform_gateway import (
    CallRecord,
    GuardrailBlocked,
    GuardrailError,
    InvalidRequest,
    Message,
    on_call_complete,
)
from ai_platform_gateway import guards as guards_mod
from ai_platform_gateway.guards import (
    PassthroughGuard,
    PreparedCall,
    PreparedTexts,
    get_guard,
    set_guard,
)
from ai_platform_gateway.testing import FakeProvider, GatewayFactory, completion


class MaskingGuard(PassthroughGuard):
    """Replaces 'Ana' with <PERSON_1> and records what it saw."""

    def before_complete(
        self, messages: list[Message], system: str | None, **kw: Any
    ) -> PreparedCall:
        super().before_complete(messages, system, **kw)
        masked = [Message(role=m.role, content=m.text().replace("Ana", "<PERSON_1>"))
                  for m in messages]  # fmt: skip
        return PreparedCall(messages=masked, system=system, mapping_id="map-1",
                            pii_entities={"PERSON": 1}, injection_score=0.1,
                            flags=["untrusted_injection:doc"])  # fmt: skip

    def after_complete(self, prepared: PreparedCall, output: str) -> list[str]:
        super().after_complete(prepared, output)
        return ["output_pii:EMAIL"] if "@" in output else []

    def before_embed(self, texts: list[str], *, pii: Any) -> PreparedTexts:  # noqa: ANN401
        return PreparedTexts([t.replace("Ana", "<PERSON_1>") for t in texts], "map-2",
                             {"PERSON": 1})  # fmt: skip


class ExplodingGuard(PassthroughGuard):
    def before_complete(self, *a: Any, **k: Any) -> PreparedCall:  # noqa: ANN401
        raise RuntimeError("presidio crashed")


def test_masked_input_is_what_the_provider_sees(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [completion("mail <PERSON_1> at x@example.com")])
    gw = make_gateway([a])
    gw.guard = MaskingGuard()
    seen: list[CallRecord] = []
    on_call_complete.append(seen.append)
    res = gw.complete("Write to Ana", "single")
    assert a.requests[0].messages[0].text() == "Write to <PERSON_1>"
    assert res.mapping_id == "map-1" and res.pii_entities == {"PERSON": 1}
    assert res.injection_score == 0.1 and res.guard_flags == ["untrusted_injection:doc"]
    assert res.output_flags == ["output_pii:EMAIL"] and res.pii_mode == "mask"


def test_embed_is_masked(make_gateway: GatewayFactory) -> None:
    a, b = FakeProvider("a"), FakeProvider("b")
    gw = make_gateway([a, b])
    gw.guard = MaskingGuard()
    res = gw.embed(["Ana's note"], "vec")
    assert a.embed_requests[0].texts == ["<PERSON_1>'s note"]
    assert res.mapping_id == "map-2" and res.pii_entities == {"PERSON": 1}


def test_guard_crash_fails_closed(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a")
    gw = make_gateway([a])
    gw.guard = ExplodingGuard()
    with pytest.raises(GuardrailError, match="blocked"):
        gw.complete("hi", "single")
    assert a.requests == []  # nothing reached the provider


def test_guard_block_passes_through(make_gateway: GatewayFactory) -> None:
    class Blocker(PassthroughGuard):
        def after_complete(self, prepared: PreparedCall, output: str) -> list[str]:
            raise GuardrailBlocked("canary leaked", reasons=["canary_leak"])

    gw = make_gateway([FakeProvider("a")])
    gw.guard = Blocker()
    with pytest.raises(GuardrailBlocked) as info:
        gw.complete("hi", "single")
    assert info.value.reasons == ["canary_leak"]


def test_pii_off_requires_reason_and_is_logged(
    make_gateway: GatewayFactory, caplog: pytest.LogCaptureFixture
) -> None:
    guard = PassthroughGuard()
    gw = make_gateway([FakeProvider("a"), FakeProvider("b")])
    gw.guard = guard
    with pytest.raises(InvalidRequest, match="pii_reason"):
        gw.complete("hi", "single", pii="off")
    with pytest.raises(InvalidRequest, match="pii_reason"):
        gw.embed("hi", "vec", pii="off", pii_reason="   ")
    with caplog.at_level(logging.INFO):
        res = gw.complete("Apple 10-K", "single", pii="off", pii_reason="public SEC filing")
    assert res.pii_mode == "off" and guard.calls[-2] == "before_complete:off:block"
    off = [r for r in caplog.records if r.getMessage() == "gateway.pii_off"]
    assert off and off[0].fields["pii_reason"] == "public SEC filing"  # type: ignore[attr-defined]
    call = [r for r in caplog.records if r.getMessage() == "gateway.call"][-1]
    assert call.fields["pii_reason"] == "public SEC filing"  # type: ignore[attr-defined]
    assert call.fields["pii_mode"] == "off"  # type: ignore[attr-defined]


def test_get_guard_fails_closed_without_guardrails(monkeypatch: pytest.MonkeyPatch) -> None:
    set_guard(None)
    monkeypatch.setattr(guards_mod, "_GUARD_FACTORY", "no_such_module_xyz:default_guard")
    try:
        with pytest.raises(GuardrailError, match="fail closed"):
            get_guard()
        explicit = PassthroughGuard()
        set_guard(explicit)
        assert get_guard() is explicit
    finally:
        set_guard(None)


def test_passthrough_guard_records_calls() -> None:
    g = PassthroughGuard()
    p = g.before_complete([Message(role="user", content="x")], None, pii="mask",
                          injection="flag", metadata={})  # fmt: skip
    assert g.after_complete(p, "y") == [] and g.before_embed(["z"], pii="block").texts == ["z"]
    assert g.calls == ["before_complete:mask:flag", "after_complete", "before_embed:block"]
