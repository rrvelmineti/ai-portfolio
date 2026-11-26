"""The gateway hook: llm_calls row + metrics + LangSmith run for every AI call.

Each step is isolated: a failure logs a warning and never reaches the AI call.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from ai_platform_common.logging import get_logger
from ai_platform_gateway import hooks
from ai_platform_gateway.hooks import CallRecord
from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_observability.langsmith_tracer import LangSmithTracer
from ai_platform_observability.metrics import Metrics
from ai_platform_observability.models import LlmCall

log = get_logger("ai_platform_observability.recorder")


def _uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def llm_call_values(record: CallRecord) -> dict[str, object]:
    """The ``llm_calls`` row for a call record (no text, ever)."""
    return {
        "request_id": record.request_id,
        "tenant_id": _uuid(record.tenant_id),
        "user_id": _uuid(record.user_id),
        "product": record.product,
        "operation": record.operation,
        "alias": record.alias,
        "provider": record.provider,
        "model": record.model,
        "attempts": record.attempts,
        "fallback_used": record.fallback_used,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "cost_usd": Decimal(str(record.cost_usd)) if record.cost_usd is not None else None,
        "cost_unknown": record.status == "ok" and record.cost_usd is None,
        "latency_ms": record.latency_ms,
        "status": record.status,
        "error_type": record.error_type,
    }


class LlmCallRecorder:
    """Callable registered in ``ai_platform_gateway.on_call_complete``."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        metrics: Metrics | None = None,
        tracer: LangSmithTracer | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.metrics = metrics
        self.tracer = tracer

    def __call__(self, record: CallRecord) -> None:
        if self.session_factory is not None:
            self._safely("db", self._write_row, record)
        if self.metrics is not None:
            self._safely("metrics", self.metrics.observe_llm, record)
        if self.tracer is not None:
            self._safely("langsmith", self.tracer.record_call, record)

    def _write_row(self, record: CallRecord) -> None:
        assert self.session_factory is not None  # noqa: S101 - checked by caller
        with self.session_factory() as session:
            session.execute(insert(LlmCall).values(**llm_call_values(record)))
            session.commit()

    @staticmethod
    def _safely(step: str, fn: object, record: CallRecord) -> None:
        try:
            fn(record)  # type: ignore[operator]
        except Exception as exc:
            log.warning(
                "observability.record_failed",
                extra={"fields": {"step": step, "error_type": type(exc).__name__,
                                  "request_id": record.request_id}},
            )  # fmt: skip


def register_recorder(recorder: LlmCallRecorder) -> LlmCallRecorder:
    """Install ``recorder`` as the gateway hook (replacing any earlier recorder).

    Turns on content capture only if the tracer will actually send content.
    """
    hooks.on_call_complete[:] = [
        h for h in hooks.on_call_complete if not isinstance(h, LlmCallRecorder)
    ]
    hooks.on_call_complete.append(recorder)
    hooks.set_capture_content(bool(recorder.tracer and recorder.tracer.log_content))
    return recorder


def unregister_recorders() -> None:
    """Remove all recorders and switch content capture off (tests, shutdown)."""
    hooks.on_call_complete[:] = [
        h for h in hooks.on_call_complete if not isinstance(h, LlmCallRecorder)
    ]
    hooks.set_capture_content(False)
