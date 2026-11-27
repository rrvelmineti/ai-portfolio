"""Usage caps: per guest per day, global per day, global per month (as the app role)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from ai_platform_demo.usage import (
    DemoLimitReached,
    UsageCaps,
    reserve_ai_call,
    usage_snapshot,
)
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DemoDb

NOW = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)


def _sessions(demo_db: DemoDb) -> sessionmaker[Session]:
    return sessionmaker(bind=demo_db.app_engine(), expire_on_commit=False)


def _call(factory: sessionmaker[Session], user: uuid.UUID, caps: UsageCaps,
          now: datetime = NOW) -> None:  # fmt: skip
    with factory() as session, session.begin():
        reserve_ai_call(session, user, caps, now)


def test_per_guest_daily_cap_and_nothing_counted_when_refused(demo_db: DemoDb) -> None:
    factory = _sessions(demo_db)
    caps = UsageCaps(per_guest_daily=2, global_daily=10, global_monthly=100)
    alice, bob = uuid.uuid4(), uuid.uuid4()
    _call(factory, alice, caps)
    _call(factory, alice, caps)
    with pytest.raises(DemoLimitReached) as info:
        _call(factory, alice, caps)
    assert info.value.scope == "guest_daily"
    assert info.value.resets_at == datetime(2027, 1, 1, tzinfo=UTC)
    assert info.value.message.startswith("Demo limit reached. You've used today's AI calls")
    _call(factory, bob, caps)  # other guests are unaffected
    with factory() as session:
        assert usage_snapshot(session, NOW) == {"today": 3, "this_month": 3}  # refusal not counted


def test_global_daily_cap(demo_db: DemoDb) -> None:
    factory = _sessions(demo_db)
    caps = UsageCaps(per_guest_daily=5, global_daily=3, global_monthly=100)
    for _ in range(3):
        _call(factory, uuid.uuid4(), caps)
    with pytest.raises(DemoLimitReached) as info:
        _call(factory, uuid.uuid4(), caps)
    assert info.value.scope == "global_daily"
    # a new day starts a new daily count
    _call(factory, uuid.uuid4(), caps, datetime(2027, 1, 1, 0, 5, tzinfo=UTC))


def test_global_monthly_cap_survives_day_changes(demo_db: DemoDb) -> None:
    factory = _sessions(demo_db)
    caps = UsageCaps(per_guest_daily=5, global_daily=5, global_monthly=3)
    for day in (1, 2, 3):
        _call(factory, uuid.uuid4(), caps, datetime(2026, 11, day, 12, tzinfo=UTC))
    with pytest.raises(DemoLimitReached) as info:
        _call(factory, uuid.uuid4(), caps, datetime(2026, 11, 4, 12, tzinfo=UTC))
    assert info.value.scope == "global_monthly"
    assert info.value.resets_at == datetime(2026, 12, 1, tzinfo=UTC)
    assert "this month's" in info.value.message
    _call(factory, uuid.uuid4(), caps, datetime(2026, 12, 1, 0, 1, tzinfo=UTC))


def test_caps_from_env_defaults_overrides_and_validation() -> None:
    assert UsageCaps.from_env({}) == UsageCaps(15, 60, 1500)
    env = {"DEMO_AI_GUEST_DAILY_CAP": "3", "DEMO_AI_DAILY_CAP": "7", "DEMO_AI_MONTHLY_CAP": "99"}
    assert UsageCaps.from_env(env) == UsageCaps(3, 7, 99)
    with pytest.raises(ValueError, match=">= 0"):
        UsageCaps.from_env({"DEMO_AI_DAILY_CAP": "-1"})


def test_zero_cap_disables_ai_calls(demo_db: DemoDb) -> None:
    factory = _sessions(demo_db)
    with pytest.raises(DemoLimitReached, match="global_daily"):
        _call(factory, uuid.uuid4(), UsageCaps(per_guest_daily=5, global_daily=0))
