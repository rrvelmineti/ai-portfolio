"""AI usage caps for the public demo (ADR 0008).

Three counters, all checked before any model call:

| Scope | Default | Env override |
|---|---:|---|
| per guest, per UTC day | 15 | ``DEMO_AI_GUEST_DAILY_CAP`` |
| all users, per UTC day | 60 | ``DEMO_AI_DAILY_CAP`` |
| all users, per UTC month | 1,500 | ``DEMO_AI_MONTHLY_CAP`` |

The caps sit well below the host's own free quota, so visitors see our friendly "demo
limit reached" message instead of the platform's HTTP 429. Counters live in Postgres
(``demo.ai_usage``) so they survive restarts and cold starts, and are shared by every
instance. A reservation that would exceed any cap is rolled back, so refused calls are
not counted.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ai_platform_common.config import get_env
from ai_platform_common.db import get_engine, normalize_url
from sqlalchemy import URL, Column, DateTime, Integer, MetaData, String, Table, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

SCHEMA = "demo"
metadata = MetaData(schema=SCHEMA)

ai_usage = Table(
    "ai_usage",
    metadata,
    Column("period", String(16), primary_key=True),  # "day:2026-11-27" or "month:2026-11"
    Column("subject", String(64), primary_key=True),  # "global" or "user:<uuid>"
    Column("calls", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


def migrate(url: str | URL) -> None:
    """Create the ``demo`` schema and its usage table if missing (owner role)."""
    engine = get_engine(normalize_url(url))
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
            metadata.create_all(conn, checkfirst=True)
    finally:
        engine.dispose()


@dataclass(frozen=True)
class UsageCaps:
    per_guest_daily: int = 15
    global_daily: int = 60
    global_monthly: int = 1500

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> UsageCaps:
        def value(name: str, default: int) -> int:
            raw = get_env(name, environ)
            if raw is None:
                return default
            number = int(raw)
            if number < 0:
                raise ValueError(f"{name} must be >= 0")
            return number

        return cls(
            per_guest_daily=value("DEMO_AI_GUEST_DAILY_CAP", cls.per_guest_daily),
            global_daily=value("DEMO_AI_DAILY_CAP", cls.global_daily),
            global_monthly=value("DEMO_AI_MONTHLY_CAP", cls.global_monthly),
        )


class DemoLimitReached(Exception):
    """A usage cap is reached. ``message`` is safe to show to visitors."""

    MESSAGES = {
        "guest_daily": "You've used today's AI calls for this guest account.",
        "global_daily": "The demo has used today's AI calls.",
        "global_monthly": "The demo has used this month's AI calls.",
    }

    def __init__(self, scope: str, resets_at: datetime) -> None:
        self.scope = scope
        self.resets_at = resets_at
        super().__init__(scope)

    @property
    def message(self) -> str:
        when = self.resets_at.strftime("%Y-%m-%d %H:%M UTC")
        return f"Demo limit reached. {self.MESSAGES[self.scope]} It resets at {when}."


def _next_day(now: datetime) -> datetime:
    return datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)


def _next_month(now: datetime) -> datetime:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=UTC)


def _bump(session: Session, period: str, subject: str) -> int:
    stmt = (
        pg_insert(ai_usage)
        .values(period=period, subject=subject, calls=1)
        .on_conflict_do_update(
            index_elements=[ai_usage.c.period, ai_usage.c.subject],
            set_={"calls": ai_usage.c.calls + 1, "updated_at": func.now()},
        )
        .returning(ai_usage.c.calls)
    )
    return int(session.execute(stmt).scalar_one())


def reserve_ai_call(
    session: Session, user_id: uuid.UUID, caps: UsageCaps, now: datetime | None = None
) -> None:
    """Count one AI call against every cap, or raise ``DemoLimitReached`` and count nothing.

    Runs in a savepoint: the caller's transaction commits the reservation.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    day, month = f"day:{now:%Y-%m-%d}", f"month:{now:%Y-%m}"
    checks = [
        (day, f"user:{user_id}", caps.per_guest_daily, "guest_daily", _next_day(now)),
        (day, "global", caps.global_daily, "global_daily", _next_day(now)),
        (month, "global", caps.global_monthly, "global_monthly", _next_month(now)),
    ]
    savepoint = session.begin_nested()
    try:
        for period, subject, limit, scope, resets_at in checks:
            if _bump(session, period, subject) > limit:
                raise DemoLimitReached(scope, resets_at)
    except BaseException:
        savepoint.rollback()
        raise
    savepoint.commit()


def usage_snapshot(session: Session, now: datetime | None = None) -> dict[str, int]:
    """Today's and this month's global counts (for the landing page)."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    rows = session.execute(
        ai_usage.select().where(
            ai_usage.c.subject == "global",
            ai_usage.c.period.in_([f"day:{now:%Y-%m-%d}", f"month:{now:%Y-%m}"]),
        )
    ).all()
    found = {r.period.split(":")[0]: int(r.calls) for r in rows}
    return {"today": found.get("day", 0), "this_month": found.get("month", 0)}
