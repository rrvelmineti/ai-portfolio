"""Post-call hooks. Phase 0.4 registers LangSmith tracing here.

Each callback receives a :class:`CallRecord` after every ``complete``/``embed`` call,
successful or not. Callbacks must be fast; exceptions are logged and swallowed so a
broken hook can never fail a model call. ``CallRecord`` deliberately contains no
prompt or response text (redacted I/O can be added once guardrails exist).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_platform_gateway.types import AttemptRecord


class CallRecord(BaseModel):
    """Everything known about one gateway call, minus prompt/response text."""

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


CallHook = Callable[[CallRecord], None]

on_call_complete: list[CallHook] = []
"""Append callables here to observe every gateway call."""
