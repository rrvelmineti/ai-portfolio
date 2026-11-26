"""Every call logs exactly one JSON line; prompts and keys never appear by default."""

from __future__ import annotations

import json
import logging

import pytest
from ai_platform_common.logging import JsonFormatter
from ai_platform_gateway import AllProvidersFailed, AuthError, CallRecord, on_call_complete
from ai_platform_gateway.testing import FakeProvider, GatewayFactory

SENSITIVE_PROMPT = "Policyholder Jane Synthetic, DOB 1970-01-01, says the roof leaked"
FAKE_KEY = "sk-test-NOT-A-REAL-KEY-1234567890"  # noqa: S105 - synthetic


def call_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    fmt = JsonFormatter()
    out = []
    for rec in caplog.records:
        line = fmt.format(rec)
        if rec.getMessage() == "gateway.call":
            out.append(json.loads(line))
    return out


def all_text(caplog: pytest.LogCaptureFixture) -> str:
    fmt = JsonFormatter()
    return "\n".join(fmt.format(r) for r in caplog.records)


def test_one_structured_line_per_call(
    make_gateway: GatewayFactory, caplog: pytest.LogCaptureFixture
) -> None:
    a = FakeProvider("a", [AuthError("bad", status_code=401)])
    gw = make_gateway([a, FakeProvider("b"), FakeProvider("c")])
    with caplog.at_level(logging.DEBUG):
        res = gw.complete("hello", "main", request_id="req-123")
    lines = call_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    for key in (
        "request_id", "alias", "provider", "model", "attempts", "fallback_used",
        "input_tokens", "output_tokens", "cost_usd", "latency_ms", "status",
    ):  # fmt: skip
        assert key in line, key
    assert line["request_id"] == "req-123" == res.request_id
    assert line["status"] == "ok"
    assert line["provider"] == "b" and line["fallback_used"] is True
    assert line["attempts"] == 2
    assert line["cost_unknown"] is False


def test_request_id_generated(make_gateway: GatewayFactory) -> None:
    res = make_gateway([FakeProvider("a")]).complete("hi", "single")
    assert len(res.request_id) == 36


def test_cost_unknown_flag(make_gateway: GatewayFactory, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        make_gateway([FakeProvider("c")]).complete("hi", "nocost")
    line = call_lines(caplog)[0]
    assert line["cost_usd"] is None and line["cost_unknown"] is True


def test_error_line(make_gateway: GatewayFactory, caplog: pytest.LogCaptureFixture) -> None:
    gw = make_gateway([FakeProvider("a", [AuthError(f"bad key {FAKE_KEY}", status_code=401)])])
    with caplog.at_level(logging.INFO), pytest.raises(AllProvidersFailed):
        gw.complete(SENSITIVE_PROMPT, "single")
    line = call_lines(caplog)[0]
    assert line["status"] == "error" and line["error_type"] == "AllProvidersFailed"
    assert FAKE_KEY not in all_text(caplog)


def test_prompts_and_keys_never_logged_by_default(
    make_gateway: GatewayFactory, caplog: pytest.LogCaptureFixture
) -> None:
    a = FakeProvider("a", required_env=("A_KEY",))
    gw = make_gateway([a, FakeProvider("b"), FakeProvider("c")], environ={"A_KEY": FAKE_KEY})
    with caplog.at_level(logging.DEBUG):
        gw.complete(SENSITIVE_PROMPT, "main", system="secret system prompt", temperature=0.3)
        gw.embed(SENSITIVE_PROMPT, "vec")
    text = all_text(caplog)
    assert "gateway.call" in text
    assert SENSITIVE_PROMPT not in text
    assert "secret system prompt" not in text
    assert "a:ok" not in text  # response text
    assert FAKE_KEY not in text


def test_debug_log_prompts_flag(
    make_gateway: GatewayFactory, caplog: pytest.LogCaptureFixture
) -> None:
    gw = make_gateway([FakeProvider("a")], environ={"DEBUG_LOG_PROMPTS": "true"})
    with caplog.at_level(logging.INFO):
        gw.complete(SENSITIVE_PROMPT, "single")
    line = call_lines(caplog)[0]
    assert line["prompt"] == [SENSITIVE_PROMPT]
    assert line["response"] == "a:ok"


def test_hooks_receive_call_record(make_gateway: GatewayFactory) -> None:
    seen: list[CallRecord] = []
    on_call_complete.append(seen.append)
    gw = make_gateway([FakeProvider("a"), FakeProvider("b")])
    gw.complete("hi", "single", metadata={"product": "claims-onboarding"})
    gw.embed(["a"], "vec")
    assert [r.operation for r in seen] == ["complete", "embed"]
    assert seen[0].metadata == {"product": "claims-onboarding"}
    assert seen[0].status == "ok" and seen[0].provider == "a"
    dumped = seen[0].model_dump_json()
    assert "hi" not in json.loads(dumped).values()


def test_hook_errors_are_swallowed(
    make_gateway: GatewayFactory, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(_record: CallRecord) -> None:
        raise RuntimeError("hook bug")

    on_call_complete.append(broken)
    with caplog.at_level(logging.ERROR):
        res = make_gateway([FakeProvider("a")]).complete("hi", "single")
    assert res.text == "a:ok"
    assert any(r.getMessage() == "gateway.hook_failed" for r in caplog.records)


def test_embed_error_logged(make_gateway: GatewayFactory, caplog: pytest.LogCaptureFixture) -> None:
    from ai_platform_gateway import NoProvidersConfigured

    gw = make_gateway(
        [FakeProvider("a", required_env=("A",)), FakeProvider("b", required_env=("B",))]
    )
    with caplog.at_level(logging.INFO), pytest.raises(NoProvidersConfigured):
        gw.embed("x", "vec")
    assert call_lines(caplog)[0]["error_type"] == "NoProvidersConfigured"


def test_request_id_and_user_from_context(make_gateway: GatewayFactory) -> None:
    import uuid

    from ai_platform_common.context import request_context, set_user

    seen: list[CallRecord] = []
    on_call_complete.append(seen.append)
    gw = make_gateway([FakeProvider("a"), FakeProvider("b")])
    tid, uid = uuid.uuid4(), uuid.uuid4()
    with request_context("ctx-req-7", product="claims-onboarding"):
        set_user(tid, uid)
        res = gw.complete("hi", "single")
        emb = gw.embed("x", "vec")
        explicit = gw.complete("hi", "single", request_id="explicit-1",
                               metadata={"product": "override"})  # fmt: skip
    assert res.request_id == "ctx-req-7" and emb.request_id == "ctx-req-7"
    assert explicit.request_id == "explicit-1"
    assert [r.request_id for r in seen] == ["ctx-req-7", "ctx-req-7", "explicit-1"]
    assert seen[0].tenant_id == str(tid) and seen[0].user_id == str(uid)
    assert seen[0].product == "claims-onboarding" and seen[2].product == "override"
    assert all(r.content is None for r in seen)  # no text unless capture is enabled
    outside = gw.complete("hi", "single")
    assert outside.request_id not in ("ctx-req-7", "explicit-1")
    assert seen[-1].tenant_id is None and seen[-1].product is None


def test_content_captured_only_when_enabled(make_gateway: GatewayFactory) -> None:
    from ai_platform_gateway import AuthError, ImagePart, Message, TextPart
    from ai_platform_gateway.hooks import capture_content, set_capture_content

    seen: list[CallRecord] = []
    on_call_complete.append(seen.append)
    gw = make_gateway([FakeProvider("a"), FakeProvider("b")])
    assert capture_content() is False
    set_capture_content(True)
    try:
        img = ImagePart.from_bytes(b"\x89PNG", "image/png")
        gw.complete([Message(role="user", content=[TextPart(text="what is "), img])],
                    "single", system="be brief")  # fmt: skip
        gw.embed(["chunk"], "vec")
        failing = make_gateway([FakeProvider("a", [AuthError("x", status_code=401)])])
        with pytest.raises(AllProvidersFailed):
            failing.complete("doomed prompt", "single")
    finally:
        set_capture_content(False)
    c0, c1, c2 = (r.content for r in seen)
    assert c0 is not None and c0.system == "be brief" and c0.output == "a:ok"
    assert c0.messages == [{"role": "user", "content": "what is [image]"}]
    assert c1 is not None and c1.messages == [{"role": "input", "content": "chunk"}]
    assert c2 is not None and c2.messages[0]["content"] == "doomed prompt" and c2.output is None
    assert "doomed prompt" not in repr(seen[2])  # content is not in the repr
