"""The demo's AI call path and landing page."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from ai_platform_demo.ask import DEMO_MODEL, AskRefused, ask
from ai_platform_demo.landing import GUESTS, guest_logins, render_landing
from ai_platform_demo.usage import DemoLimitReached, UsageCaps, usage_snapshot
from ai_platform_gateway import (
    Gateway,
    GuardrailBlocked,
    PolicyViolation,
    ProviderUnavailable,
)
from ai_platform_gateway.testing import FakeProvider, fake_gateway
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DemoDb

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


def _sessions(demo_db: DemoDb) -> sessionmaker[Session]:
    return sessionmaker(bind=demo_db.app_engine(), expire_on_commit=False)


class _Raises:
    """A stand-in gateway whose ``complete`` raises ``exc``."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def complete(self, *_args: Any, **_kwargs: Any) -> None:  # noqa: ANN401
        raise self.exc


def test_ask_uses_the_free_alias_and_counts_the_call(demo_db: DemoDb) -> None:
    factory = _sessions(demo_db)
    gemini = FakeProvider("gemini", ["fine"])
    result = ask(factory, uuid.uuid4(), "What is a claim?", UsageCaps(),
                 gateway=fake_gateway({"gemini": gemini}), now=NOW)  # fmt: skip
    assert result.answer == "gemini:fine" and result.provider == "gemini"
    assert result.usage == {"today": 1, "this_month": 1}
    assert gemini.requests[0].max_tokens == 400


@pytest.mark.parametrize(
    ("exc", "status", "reasons"),
    [
        (GuardrailBlocked("x", reasons=["injection:rules"]), 422, ["injection:rules"]),
        (PolicyViolation("medical", "I can't help with that."), 422, ["topic:medical"]),
        (ProviderUnavailable("down", provider="gemini"), 503, []),
    ],
)
def test_refusals_still_count_the_reservation(
    demo_db: DemoDb, exc: Exception, status: int, reasons: list[str]
) -> None:
    factory = _sessions(demo_db)
    with pytest.raises(AskRefused) as info:
        ask(factory, uuid.uuid4(), "hi", UsageCaps(), gateway=cast(Gateway, _Raises(exc)),
            now=NOW)  # fmt: skip
    assert info.value.status_code == status and info.value.reasons == reasons
    if isinstance(exc, PolicyViolation):
        assert info.value.detail == "I can't help with that."
    with factory() as session:  # the host's compute was spent, so the call counts
        assert usage_snapshot(session, NOW) == {"today": 1, "this_month": 1}


def test_cap_reached_before_any_model_call(demo_db: DemoDb) -> None:
    factory = _sessions(demo_db)
    gemini = FakeProvider("gemini")
    with pytest.raises(DemoLimitReached):
        ask(factory, uuid.uuid4(), "hi", UsageCaps(global_daily=0),
            gateway=fake_gateway({"gemini": gemini}), now=NOW)  # fmt: skip
    assert gemini.requests == []


def test_demo_model_is_the_free_alias() -> None:
    assert DEMO_MODEL == "demo-free"


def test_landing_lists_only_guests() -> None:
    assert {g.username for g in GUESTS} == {"demo-viewer", "demo-analyst", "demo-reviewer"}
    env = {"DEMO_VIEWER_PASSWORD": "viewer-pw-<b>", "DEMO_ADMIN_PASSWORD": "admin-secret-pw"}
    assert [pw for _, pw in guest_logins(env)] == ["viewer-pw-<b>", None, None]
    page = render_landing(UsageCaps(), env, usage={"today": 4, "this_month": 9})
    assert "viewer-pw-&lt;b&gt;" in page and "viewer-pw-<b>" not in page  # escaped
    assert "admin-secret-pw" not in page and "demo-admin" not in page
    assert page.count("not available") == 2
    assert "15 per guest per day" in page and "1500 per month" in page
    assert "4 of 60 today" in page
    assert '<option value="demo-viewer">' in page
    assert '<option value="demo-analyst">' not in page  # no password, no option
