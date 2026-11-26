"""Post-call hooks (used by ``ai_platform_observability`` for llm_calls, metrics and
LangSmith).

Each callback receives a :class:`CallRecord` after every ``complete``/``embed`` call,
successful or not. Callbacks must be fast; exceptions are logged and swallowed so a
broken hook can never fail a model call.

``CallRecord`` carries no prompt or response text by default. Only when a consumer
opts in with ``set_capture_content(True)`` (observability does this when
``LANGSMITH_LOG_CONTENT=true``) is ``CallRecord.content`` filled; the consumer must run
it through its redaction hook before sending it anywhere.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_platform_gateway.types import AttemptRecord


class CallContent(BaseModel):
    """Prompt and response text of one call. Only present when capture is enabled."""

    system: str | None = None
    messages: list[dict[str, str]] = Field(default_factory=list)  # {"role", "content"}
    output: str | None = None


class CallRecord(BaseModel):
    """Everything known about one gateway call. No text unless capture is enabled."""

    request_id: str
    operation: Literal["complete", "embed"]
    alias: str
    provider: str | None
    model: str | None
    status: Literal["ok", "error"]
    error_type: str | None = None
    attempts: int
    fallback_used: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    attempt_records: list[AttemptRecord] = Field(default_factory=list)
    tenant_id: str | None = None
    user_id: str | None = None
    product: str | None = None
    content: CallContent | None = Field(default=None, repr=False)


CallHook = Callable[[CallRecord], None]

on_call_complete: list[CallHook] = []
"""Append callables here to observe every gateway call."""

_capture = {"enabled": False}


def set_capture_content(enabled: bool) -> None:
    """Opt in (or out) to receiving prompt/response text in ``CallRecord.content``."""
    _capture["enabled"] = enabled


def capture_content() -> bool:
    """Whether hooks currently receive prompt/response text."""
    return _capture["enabled"]
