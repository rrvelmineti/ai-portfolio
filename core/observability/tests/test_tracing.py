from __future__ import annotations

import logging
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from ai_platform_common.context import request_context, set_user
from ai_platform_gateway import AllProvidersFailed, AuthError, Gateway, hooks
from ai_platform_gateway.hooks import CallContent, CallRecord
from ai_platform_gateway.testing import FakeProvider, fake_gateway
from ai_platform_observability.langsmith_tracer import (
    LangSmithTracer,
    get_tracer,
    set_tracer,
    traced,
)
from ai_platform_observability.metrics import Metrics
from ai_platform_observability.models import LlmCall
from ai_platform_observability.recorder import (
    LlmCallRecorder,
    llm_call_values,
    register_recorder,
    unregister_recorders,
)
from ai_platform_observability.redaction import redact_content, set_content_redactor
from ai_platform_observability.settings import ObservabilitySettings
from langsmith import Client
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

SECRET_PROMPT = "Synthetic claimant Jo Example, policy P-0000, water damage"


@pytest.fixture(autouse=True)
def _clean_hooks() -> Iterator[None]:
    saved = list(hooks.on_call_complete)
    yield
    unregister_recorders()
    hooks.on_call_complete[:] = saved
    set_tracer(None)
    set_content_redactor(None)


def gateway(*steps: Any) -> Gateway:  # noqa: ANN401
    return fake_gateway({
        "anthropic": FakeProvider("anthropic", list(steps) or ["ok"]),
        "groq": FakeProvider("groq"),
        "bedrock": FakeProvider("bedrock", [AuthError("no", status_code=403)]),
        "openai": FakeProvider("openai", [AuthError("no", status_code=401)]),
    })  # fmt: skip


def mock_client() -> MagicMock:
    return MagicMock(spec=Client)


def run_payloads(client: MagicMock) -> list[dict[str, Any]]:
    return [c.kwargs for c in client.create_run.call_args_list]


# ------------------------------------------------------------------ settings
def test_settings_default_off() -> None:
    s = ObservabilitySettings.from_env({})
    assert not s.langsmith_enabled and not s.sentry_enabled
    assert s.langsmith_project == "ai-portfolio-dev" and s.langsmith_log_content is False
    assert LangSmithTracer.from_settings(s) is None
    s2 = ObservabilitySettings.from_env({"LANGSMITH_API_KEY": "ls-dummy-key-123",
                                         "LANGSMITH_PROJECT": "p", "SENTRY_DSN": "https://k@x/1",
                                         "LANGSMITH_LOG_CONTENT": "true"})  # fmt: skip
    assert s2.langsmith_enabled and s2.sentry_enabled and s2.langsmith_log_content
    assert "ls-dummy-key-123" not in repr(s2) and "https://k@x/1" not in repr(s2)
    tracer = LangSmithTracer.from_settings(s2)
    assert tracer is not None and tracer.project == "p" and tracer.log_content


# ------------------------------------------------------------------ llm_calls rows
def test_row_written_on_success_with_context(
    session_factory: sessionmaker[Session], session: Session
) -> None:
    import uuid

    register_recorder(LlmCallRecorder(session_factory))
    tid, uid = uuid.uuid4(), uuid.uuid4()
    with request_context("req-llm-1", product="claims-onboarding"):
        set_user(tid, uid)
        res = gateway().complete(SECRET_PROMPT)
    row = session.scalar(select(LlmCall).where(LlmCall.request_id == "req-llm-1"))
    assert row is not None
    assert (row.tenant_id, row.user_id, row.product) == (tid, uid, "claims-onboarding")
    assert (row.operation, row.alias, row.provider, row.model) == (
        "complete",
        "default",
        "anthropic",
        "claude-sonnet-5-5",
    )
    assert (row.input_tokens, row.output_tokens, row.status) == (100, 50, "ok")
    assert row.cost_usd == Decimal(str(res.cost_usd)) and row.cost_unknown is False
    assert row.attempts == 1 and row.fallback_used is False and row.latency_ms >= 0
    values = " ".join(str(v) for v in llm_call_values(
        CallRecord(request_id="x", operation="complete", alias="a", provider=None, model=None,
                   status="ok", attempts=1, fallback_used=False, latency_ms=1.0,
                   tenant_id="not-a-uuid"))
    .values())  # fmt: skip
    assert SECRET_PROMPT not in values


def test_row_written_on_failure_and_unknown_cost(
    session_factory: sessionmaker[Session], session: Session
) -> None:
    register_recorder(LlmCallRecorder(session_factory))
    gw = fake_gateway({"anthropic": FakeProvider("anthropic", [AuthError("x", status_code=401)]),
                       "bedrock": FakeProvider("bedrock", [AuthError("x", status_code=401)]),
                       "groq": FakeProvider("groq")})  # fmt: skip
    with request_context("req-fail"), pytest.raises(AllProvidersFailed):
        gw.complete("hi", "claude-sonnet")
    with request_context("req-unknown-cost"):
        gw.complete("hi", "groq-llama")  # Groq price is null in models.yaml
    fail = session.scalar(select(LlmCall).where(LlmCall.request_id == "req-fail"))
    assert fail is not None and fail.status == "error" and fail.error_type == "AllProvidersFailed"
    assert fail.cost_usd is None and fail.cost_unknown is False and fail.attempts == 2
    unknown = session.scalar(select(LlmCall).where(LlmCall.request_id == "req-unknown-cost"))
    assert unknown is not None and unknown.cost_usd is None and unknown.cost_unknown is True


def test_embed_rows(session_factory: sessionmaker[Session], session: Session) -> None:
    register_recorder(LlmCallRecorder(session_factory))
    gw = fake_gateway({"openai": FakeProvider("openai")})
    with request_context("req-embed"):
        gw.embed(["a", "b"])
    row = session.scalar(select(LlmCall).where(LlmCall.request_id == "req-embed"))
    assert row is not None and row.operation == "embed" and row.input_tokens == 10


def test_hook_failures_never_break_the_ai_call(caplog: pytest.LogCaptureFixture) -> None:
    def broken_factory() -> Session:
        raise RuntimeError("database down")

    metrics = MagicMock(spec=Metrics)
    metrics.observe_llm.side_effect = RuntimeError("metrics broken")
    tracer = MagicMock(spec=LangSmithTracer)
    tracer.log_content = False
    tracer.record_call.side_effect = RuntimeError("langsmith down")
    register_recorder(LlmCallRecorder(broken_factory, metrics, tracer))  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING):
        res = gateway().complete("still works")
    assert res.text == "anthropic:ok"
    failed = [r.fields["step"] for r in caplog.records  # type: ignore[attr-defined]
              if r.getMessage() == "observability.record_failed"]  # fmt: skip
    assert failed == ["db", "metrics", "langsmith"]


def test_register_replaces_previous_recorder() -> None:
    register_recorder(LlmCallRecorder())
    register_recorder(LlmCallRecorder())
    assert sum(isinstance(h, LlmCallRecorder) for h in hooks.on_call_complete) == 1
    unregister_recorders()
    assert not any(isinstance(h, LlmCallRecorder) for h in hooks.on_call_complete)


# ------------------------------------------------------------------ LangSmith
def test_langsmith_metadata_only_by_default() -> None:
    client = mock_client()
    tracer = LangSmithTracer(client, "proj")
    register_recorder(LlmCallRecorder(tracer=tracer))
    assert hooks.capture_content() is False
    with request_context("req-ls", product="query-desk"):
        gateway().complete(SECRET_PROMPT, system="secret system prompt")
    (payload,) = run_payloads(client)
    assert payload["run_type"] == "llm" and payload["name"] == "complete:default"
    assert payload["session_name"] == "proj"
    assert payload["inputs"] == {} and payload["outputs"] == {}
    meta = payload["extra"]["metadata"]
    assert meta["request_id"] == "req-ls" and meta["product"] == "query-desk"
    assert meta["provider"] == "anthropic" and meta["input_tokens"] == 100
    assert meta["cost_usd"] is not None
    assert SECRET_PROMPT not in str(payload) and "secret system prompt" not in str(payload)


def test_langsmith_content_only_with_flag_and_redacted() -> None:
    client = mock_client()
    register_recorder(LlmCallRecorder(tracer=LangSmithTracer(client, "proj", log_content=True)))
    assert hooks.capture_content() is True
    calls: list[CallContent] = []

    def redactor(c: CallContent) -> CallContent:
        calls.append(c)
        msgs = [{**m, "content": m["content"].replace("Jo Example", "<PERSON>")}
                for m in c.messages]  # fmt: skip
        return c.model_copy(update={"messages": msgs})

    set_content_redactor(redactor)
    gateway().complete(SECRET_PROMPT, system="be brief")
    (payload,) = run_payloads(client)
    assert len(calls) == 1
    assert payload["inputs"]["system"] == "be brief"
    assert "<PERSON>" in payload["inputs"]["messages"][0]["content"]
    assert "Jo Example" not in str(payload)
    assert payload["outputs"] == {"output": "anthropic:ok"}


def test_redact_content_default_is_identity() -> None:
    c = CallContent(system="s", messages=[{"role": "user", "content": "x"}], output="y")
    assert redact_content(c) == c


def test_langsmith_error_run_and_embedding() -> None:
    client = mock_client()
    register_recorder(LlmCallRecorder(tracer=LangSmithTracer(client, "proj")))
    gw = fake_gateway({"anthropic": FakeProvider("anthropic", [AuthError("x", status_code=401)]),
                       "bedrock": FakeProvider("bedrock", [AuthError("x", status_code=401)]),
                       "openai": FakeProvider("openai")})  # fmt: skip
    with pytest.raises(AllProvidersFailed):
        gw.complete("hi", "claude-sonnet")
    gw.embed("x")
    err, emb = run_payloads(client)
    assert err["error"] == "AllProvidersFailed"
    assert emb["run_type"] == "embedding" and emb["name"] == "embed:default-embed"


def test_traced_groups_calls_under_parent_run() -> None:
    client = mock_client()
    tracer = LangSmithTracer(client, "proj")
    set_tracer(tracer)
    register_recorder(LlmCallRecorder(tracer=tracer))
    with request_context("req-flow"), traced("map file", metadata={"file_id": "f-1"}):
        gateway().complete("step one")
        gateway().complete("step two")
    parent, child1, child2 = run_payloads(client)
    assert parent["name"] == "map file" and parent["run_type"] == "chain"
    assert parent["extra"]["metadata"] == {"file_id": "f-1", "request_id": "req-flow"}
    for child in (child1, child2):
        assert child["parent_run_id"] == parent["id"] and child["trace_id"] == parent["trace_id"]
    (update,) = [c.kwargs for c in client.update_run.call_args_list]
    assert update.get("error") is None
    # outside the block, calls are root runs again
    gateway().complete("later")
    assert run_payloads(client)[-1].get("parent_run_id") is None


def test_traced_decorator_sync_async_and_errors(caplog: pytest.LogCaptureFixture) -> None:
    import asyncio

    client = mock_client()
    set_tracer(LangSmithTracer(client, "proj"))

    @traced("sync step")
    def work(x: int) -> int:
        return x * 2

    @traced("async step")
    async def awork(x: int) -> int:
        return x + 1

    @traced("failing step")
    def boom() -> None:
        raise ValueError("bad input")

    assert work(2) == 4
    assert asyncio.run(awork(1)) == 2
    with pytest.raises(ValueError):
        boom()
    names = [p["name"] for p in run_payloads(client)]
    assert names == ["sync step", "async step", "failing step"]
    assert client.update_run.call_args_list[-1].kwargs["error"] == "ValueError"


def test_traced_without_langsmith_logs_span(caplog: pytest.LogCaptureFixture) -> None:
    assert get_tracer() is None
    with caplog.at_level(logging.INFO), traced("local step", metadata={"n": 1}):
        pass
    span = [r for r in caplog.records if r.getMessage() == "trace.span"][0]
    assert span.fields["span"] == "local step" and span.fields["n"] == 1  # type: ignore[attr-defined]
    assert span.fields["status"] == "ok"  # type: ignore[attr-defined]


def test_traced_survives_langsmith_errors(caplog: pytest.LogCaptureFixture) -> None:
    client = mock_client()
    client.create_run.side_effect = RuntimeError("langsmith down")
    client.update_run.side_effect = RuntimeError("langsmith down")
    set_tracer(LangSmithTracer(client, "proj"))
    with caplog.at_level(logging.WARNING), traced("resilient"):
        value = 42
    assert value == 42
    assert any(r.getMessage() == "observability.trace_start_failed" for r in caplog.records)


# ------------------------------------------------------------------ metrics
def test_llm_metrics_counters() -> None:
    metrics = Metrics()
    register_recorder(LlmCallRecorder(metrics=metrics))
    gw = gateway(AuthError("no", status_code=401))  # anthropic fails -> bedrock fails -> openai
    with pytest.raises(AllProvidersFailed):
        gw.complete("hi")
    fake_gateway({"groq": FakeProvider("groq")}).complete("hi", "groq-llama")
    ok = fake_gateway({"anthropic": FakeProvider("anthropic"),
                       "bedrock": FakeProvider("bedrock")})  # fmt: skip
    ok.complete("hi", "claude-sonnet")
    fb = fake_gateway({"anthropic": FakeProvider("anthropic", [AuthError("x", status_code=401)]),
                       "bedrock": FakeProvider("bedrock")})  # fmt: skip
    fb.complete("hi", "claude-sonnet")

    def value(name: str, **labels: str) -> float:
        v = metrics.registry.get_sample_value(name, labels)
        return v or 0.0

    assert value("llm_calls_total", provider="none", model="none", status="error") == 1
    assert value("llm_calls_total", provider="anthropic", model="claude-sonnet-5-5",
                 status="ok") == 1  # fmt: skip
    assert value("llm_tokens_total", provider="anthropic", model="claude-sonnet-5-5",
                 direction="input") == 100  # fmt: skip
    assert value("llm_cost_usd_total", provider="anthropic", model="claude-sonnet-5-5") > 0
    assert value("llm_cost_unknown_total", provider="groq",
                 model="llama-3.3-70b-versatile") == 1  # fmt: skip
    assert value("llm_fallbacks_total", alias="claude-sonnet") == 1
    assert value("llm_call_duration_seconds_count", provider="anthropic",
                 model="claude-sonnet-5-5") == 1  # fmt: skip
    assert b"llm_calls_total" in metrics.render()
