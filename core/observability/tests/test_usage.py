from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ai_platform_observability.models import LlmCall
from ai_platform_observability.usage import format_csv, format_table, usage_summary
from sqlalchemy.orm import Session

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[3]
MINE = ["u-claims", "u-desk"]  # other tests commit rows to the shared test DB


def add(session: Session, *, product: str = "u-claims", provider: str = "anthropic",
        cost: str | None = "0.001", latency: float = 100.0, status: str = "ok",
        fallback: bool = False, day_offset: int = 0, tokens: int = 10) -> None:  # fmt: skip
    session.add(LlmCall(
        request_id="r", product=product, operation="complete", alias="default",
        provider=provider, model="m", attempts=1, fallback_used=fallback,
        input_tokens=tokens, output_tokens=tokens // 2,
        cost_usd=Decimal(cost) if cost else None,
        cost_unknown=cost is None and status == "ok", latency_ms=latency, status=status,
        created_at=NOW - timedelta(days=day_offset),
    ))  # fmt: skip


@pytest.fixture
def seeded(session: Session) -> Session:
    for i in range(20):  # claims/anthropic today: latencies 100..2000
        add(session, latency=100.0 * (i + 1), fallback=i < 2)
    add(session, status="error", cost=None, latency=50.0)
    add(session, product="u-desk", provider="groq", cost=None)  # unpriced
    add(session, product="u-claims", day_offset=3, cost="0.002")
    add(session, product="u-claims", day_offset=40, cost="9.0")  # outside 30 days
    session.flush()
    return session


def test_usage_by_product_and_provider(seeded: Session) -> None:
    rows = usage_summary(seeded, ["product", "provider"], days=30, now=NOW, products=MINE)
    by = {(r.group["product"], r.group["provider"]): r for r in rows}
    claims = by[("u-claims", "anthropic")]
    assert claims.calls == 22 and claims.errors == 1
    assert claims.priced_calls == 21
    assert claims.total_cost_usd == pytest.approx(20 * 0.001 + 0.002)
    assert claims.avg_cost_usd == pytest.approx((20 * 0.001 + 0.002) / 21)
    assert claims.fallback_rate == pytest.approx(2 / 22, abs=1e-4)
    assert claims.error_rate == pytest.approx(1 / 22, abs=1e-4)
    assert claims.p95_latency_ms > claims.avg_latency_ms
    assert claims.input_tokens == 220
    groq = by[("u-desk", "groq")]
    assert groq.priced_calls == 0 and groq.avg_cost_usd is None and groq.total_cost_usd == 0


def test_usage_by_day_and_all_time(seeded: Session) -> None:
    days = usage_summary(seeded, ["day"], days=30, now=NOW, products=MINE)
    assert [r.group["day"] for r in days] == ["2026-11-17", "2026-11-20"]
    all_time = usage_summary(seeded, [], days=None, products=MINE)
    assert len(all_time) == 1 and all_time[0].calls == 24
    with pytest.raises(ValueError, match="unknown dimensions"):
        usage_summary(seeded, ["colour"])  # type: ignore[list-item]


def test_formatters(seeded: Session) -> None:
    rows = usage_summary(seeded, ["product"], days=30, now=NOW, products=MINE)
    table = format_table(rows, ["product"])
    assert table.splitlines()[0].split()[:3] == ["product", "calls", "cost_usd"]
    assert "u-claims" in table and "u-desk" in table
    csv_text = format_csv(rows, ["product"])
    assert csv_text.splitlines()[0].startswith("product,calls,errors,total_cost_usd")
    assert format_table([], ["product"]) == "(no llm_calls in the selected period)"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_usage_report_script(
    committed_session_factory: Any, db_url: Any, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    report = _load("usage_report")
    with committed_session_factory() as s:
        add(s, product="report-test", day_offset=0)
        s.commit()
    monkeypatch.setenv("DATABASE_URL", db_url.render_as_string(hide_password=False))
    assert report.main(["--by", "product", "--days", "0"]) == 0
    assert "report-test" in capsys.readouterr().out
    assert report.main(["--by", "product", "--format", "csv", "--days", "0",
                        "--product", "report-test"]) == 0  # fmt: skip
    assert "report-test" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        report.main(["--by", "colour"])
    monkeypatch.delenv("DATABASE_URL")
    assert report.main([]) == 2
