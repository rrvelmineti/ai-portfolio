"""Usage aggregation over ``llm_calls`` (cost, calls, latency, fallback rate).

The source of "cost per answer / per file" numbers: group by product, provider, model,
alias and/or day (UTC).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Date, Integer, case, cast, func, select
from sqlalchemy.orm import Session

from ai_platform_observability.models import LlmCall

Dimension = Literal["product", "provider", "model", "alias", "day"]
DIMENSIONS: tuple[Dimension, ...] = ("product", "provider", "model", "alias", "day")


@dataclass(frozen=True)
class UsageRow:
    group: dict[str, str]
    calls: int
    errors: int
    total_cost_usd: float
    priced_calls: int  # calls with a known cost
    avg_cost_usd: float | None  # per priced call ("cost per answer")
    avg_latency_ms: float
    p95_latency_ms: float
    fallback_rate: float
    error_rate: float
    input_tokens: int
    output_tokens: int
    extra: dict[str, Any] = field(default_factory=dict)


def _dimension(name: Dimension) -> Any:  # noqa: ANN401 - SQL expression
    if name == "day":
        return cast(func.timezone("UTC", LlmCall.created_at), Date).label("day")
    return func.coalesce(getattr(LlmCall, name), "(none)").label(name)


def usage_summary(
    session: Session,
    group_by: Sequence[Dimension] = ("product", "provider", "day"),
    *,
    days: int | None = 30,
    now: datetime | None = None,
    products: Sequence[str] | None = None,
) -> list[UsageRow]:
    """Aggregate ``llm_calls`` per group. ``days=None`` means all time; ``products``
    limits the report to those products."""
    unknown = [d for d in group_by if d not in DIMENSIONS]
    if unknown:
        raise ValueError(f"unknown dimensions: {unknown}; choose from {DIMENSIONS}")
    dims = [_dimension(d) for d in group_by]
    calls = func.count(LlmCall.id)
    stmt = select(
        *dims,
        calls.label("calls"),
        func.sum(cast(LlmCall.status == "error", Integer)).label("errors"),
        func.coalesce(func.sum(LlmCall.cost_usd), 0).label("total_cost"),
        func.count(LlmCall.cost_usd).label("priced_calls"),
        func.avg(LlmCall.latency_ms).label("avg_latency"),
        func.percentile_cont(0.95).within_group(LlmCall.latency_ms).label("p95_latency"),
        func.avg(case((LlmCall.fallback_used, 1.0), else_=0.0)).label("fallback_rate"),
        func.coalesce(func.sum(LlmCall.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(LlmCall.output_tokens), 0).label("output_tokens"),
    )
    if days is not None:
        since = (now or datetime.now(UTC)) - timedelta(days=days)
        stmt = stmt.where(LlmCall.created_at >= since)
    if products:
        stmt = stmt.where(LlmCall.product.in_(list(products)))
    if dims:
        stmt = stmt.group_by(*dims).order_by(*dims)
    rows: list[UsageRow] = []
    for r in session.execute(stmt).mappings():
        n = int(r["calls"])
        if n == 0:
            continue
        priced = int(r["priced_calls"])
        total = float(r["total_cost"])
        rows.append(
            UsageRow(
                group={d: str(r[d]) for d in group_by},
                calls=n,
                errors=int(r["errors"] or 0),
                total_cost_usd=round(total, 8),
                priced_calls=priced,
                avg_cost_usd=round(total / priced, 8) if priced else None,
                avg_latency_ms=round(float(r["avg_latency"]), 2),
                p95_latency_ms=round(_float(r["p95_latency"]), 2),
                fallback_rate=round(float(r["fallback_rate"]), 4),
                error_rate=round(int(r["errors"] or 0) / n, 4),
                input_tokens=int(r["input_tokens"]),
                output_tokens=int(r["output_tokens"]),
            )
        )
    return rows


def _float(value: Any) -> float:  # noqa: ANN401
    return float(value) if value is not None else 0.0


def format_table(rows: Sequence[UsageRow], group_by: Sequence[str]) -> str:
    """Plain-text table for terminals and case-study notes."""
    headers = [*group_by, "calls", "cost_usd", "avg_cost", "unpriced", "avg_ms", "p95_ms",
               "fallback%", "error%", "tok_in", "tok_out"]  # fmt: skip
    data = [
        [*(r.group[g] for g in group_by), str(r.calls), f"{r.total_cost_usd:.6f}",
         f"{r.avg_cost_usd:.6f}" if r.avg_cost_usd is not None else "-",
         str(r.calls - r.priced_calls), f"{r.avg_latency_ms:.0f}", f"{r.p95_latency_ms:.0f}",
         f"{r.fallback_rate * 100:.1f}", f"{r.error_rate * 100:.1f}", str(r.input_tokens),
         str(r.output_tokens)]
        for r in rows
    ]  # fmt: skip
    if not data:
        return "(no llm_calls in the selected period)"
    widths = [max(len(h), *(len(row[i]) for row in data)) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))]
    lines.append("  ".join("-" * w for w in widths))
    lines += ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in data]
    return "\n".join(lines)


def format_csv(rows: Sequence[UsageRow], group_by: Sequence[str]) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([*group_by, "calls", "errors", "total_cost_usd", "priced_calls",
                     "avg_cost_usd", "avg_latency_ms", "p95_latency_ms", "fallback_rate",
                     "error_rate", "input_tokens", "output_tokens"])  # fmt: skip
    for r in rows:
        writer.writerow([*(r.group[g] for g in group_by), r.calls, r.errors, r.total_cost_usd,
                         r.priced_calls, r.avg_cost_usd if r.avg_cost_usd is not None else "",
                         r.avg_latency_ms, r.p95_latency_ms, r.fallback_rate, r.error_rate,
                         r.input_tokens, r.output_tokens])  # fmt: skip
    return buf.getvalue()
