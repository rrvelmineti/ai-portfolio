"""LangSmith runs for AI calls, plus ``traced()`` to group workflow steps.

Only active when ``LANGSMITH_API_KEY`` is set. Each AI call becomes one ``llm`` run
with metadata (alias, provider, model, tokens, cost, attempts, fallback, request_id,
tenant, product). Prompt and response text are attached only when
``LANGSMITH_LOG_CONTENT=true``, and always after ``redact_content()``.
"""

from __future__ import annotations

import functools
import inspect
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, ParamSpec, TypeVar

from ai_platform_common.context import current_request_id
from ai_platform_common.logging import get_logger
from ai_platform_gateway.hooks import CallRecord
from langsmith import Client, RunTree

from ai_platform_observability.redaction import redact_content
from ai_platform_observability.settings import ObservabilitySettings

log = get_logger("ai_platform_observability.langsmith")

P = ParamSpec("P")
R = TypeVar("R")

_parent_run: ContextVar[RunTree | None] = ContextVar("ai_platform_parent_run", default=None)


class LangSmithTracer:
    """Sends runs to LangSmith. Construct with ``from_settings`` (None when disabled)."""

    def __init__(self, client: Client, project: str, log_content: bool = False) -> None:
        self.client = client
        self.project = project
        self.log_content = log_content

    @classmethod
    def from_settings(cls, settings: ObservabilitySettings) -> LangSmithTracer | None:
        if not settings.langsmith_enabled:
            return None
        client = Client(api_key=settings.langsmith_api_key, api_url=settings.langsmith_endpoint)
        return cls(client, settings.langsmith_project, settings.langsmith_log_content)

    def _new_run(self, **kwargs: Any) -> RunTree:  # noqa: ANN401
        parent = _parent_run.get()
        if parent is not None:
            return parent.create_child(**kwargs)
        return RunTree(ls_client=self.client, project_name=self.project, **kwargs)

    def record_call(self, record: CallRecord) -> RunTree:
        """Post one finished ``llm`` run for a gateway call."""
        end = datetime.now(UTC)
        inputs: dict[str, Any] = {}
        outputs: dict[str, Any] = {}
        if self.log_content and record.content is not None:
            content = redact_content(record.content)
            inputs = {"system": content.system, "messages": content.messages}
            outputs = {"output": content.output} if content.output is not None else {}
        metadata = {
            "request_id": record.request_id,
            "operation": record.operation,
            "alias": record.alias,
            "provider": record.provider,
            "model": record.model,
            "attempts": record.attempts,
            "fallback_used": record.fallback_used,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cost_usd": record.cost_usd,
            "latency_ms": record.latency_ms,
            "tenant_id": record.tenant_id,
            "user_id": record.user_id,
            "product": record.product,
            "ls_provider": record.provider,
            "ls_model_name": record.model,
        }
        run = self._new_run(
            name=f"{record.operation}:{record.alias}",
            run_type="llm" if record.operation == "complete" else "embedding",
            inputs=inputs,
            outputs=outputs,
            start_time=end - timedelta(milliseconds=record.latency_ms),
            end_time=end,
            error=record.error_type if record.status == "error" else None,
            extra={"metadata": metadata},
            tags=[t for t in (record.product, record.alias) if t],
        )
        run.post()
        return run


_tracer: dict[str, LangSmithTracer | None] = {"t": None}


def set_tracer(tracer: LangSmithTracer | None) -> None:
    """Install (or clear) the process-wide tracer used by ``traced()``."""
    _tracer["t"] = tracer


def get_tracer() -> LangSmithTracer | None:
    return _tracer["t"]


class traced:  # noqa: N801 - used like a function: ``with traced(...)`` / ``@traced(...)``
    """Group the steps of one workflow (e.g. "map file") under a parent run.

    With LangSmith enabled, AI calls inside the block become child runs of a ``chain``
    run named ``name``. Without it, a ``trace.span`` log line with the duration is
    written. Never raises because of tracing problems.

        with traced("map file", metadata={"file_id": fid}):
            ...

        @traced("summarise claim")
        def summarise(...): ...
    """

    def __init__(self, name: str, *, metadata: dict[str, Any] | None = None,
                 run_type: str = "chain") -> None:  # fmt: skip
        self.name = name
        self.metadata = dict(metadata or {})
        self.run_type = run_type
        self._run: RunTree | None = None
        self._token: Token[RunTree | None] | None = None
        self._start = 0.0

    @property
    def run(self) -> RunTree | None:
        """The LangSmith parent run (None when LangSmith is off or failed)."""
        return self._run

    def __enter__(self) -> traced:
        self._start = time.perf_counter()
        tracer = get_tracer()
        if tracer is not None:
            try:
                meta = {**self.metadata, "request_id": current_request_id()}
                self._run = tracer._new_run(
                    name=self.name, run_type=self.run_type, inputs={},
                    extra={"metadata": meta},
                )  # fmt: skip
                self._run.post()
                self._token = _parent_run.set(self._run)
            except Exception:
                log.warning("observability.trace_start_failed", exc_info=True,
                            extra={"fields": {"span": self.name}})  # fmt: skip
                self._run = None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        latency_ms = round((time.perf_counter() - self._start) * 1000, 2)
        if self._token is not None:
            _parent_run.reset(self._token)
            self._token = None
        if self._run is not None:
            try:
                self._run.end(error=exc_type.__name__ if exc_type else None)
                self._run.patch()
            except Exception:
                log.warning("observability.trace_end_failed", exc_info=True,
                            extra={"fields": {"span": self.name}})  # fmt: skip
        log.info(
            "trace.span",
            extra={"fields": {"span": self.name, "latency_ms": latency_ms,
                              "status": "error" if exc_type else "ok", **self.metadata}},
        )  # fmt: skip

    def __call__(self, fn: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:  # noqa: ANN401
                with traced(self.name, metadata=self.metadata, run_type=self.run_type):
                    return await fn(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with traced(self.name, metadata=self.metadata, run_type=self.run_type):
                return fn(*args, **kwargs)

        return wrapper
