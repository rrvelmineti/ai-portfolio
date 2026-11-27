"""The nightly demo reset, on a real Postgres with the owner role."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from ai_platform_auth.models import Role, Tenant, User
from ai_platform_auth.passwords import verify_password
from ai_platform_common.context import request_context
from ai_platform_demo.reset import reset_demo
from ai_platform_demo.seed import DEMO_TENANT, read_passwords, seed
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DemoDb

PASSWORDS = {
    "DEMO_ADMIN_PASSWORD": "demo-admin-passphrase",
    "DEMO_ANALYST_PASSWORD": "demo-analyst-passphrase",
    "DEMO_VIEWER_PASSWORD": "demo-viewer-passphrase",
    "DEMO_REVIEWER_PASSWORD": "demo-reviewer-passphrase",
}
NOW = datetime.now(UTC)
OTHER_TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _count(engine: Engine, sql: str, **params: object) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def _visitor_activity(owner: Engine, tenant_id: uuid.UUID, viewer_id: uuid.UUID) -> None:
    """What a day of visitors leaves behind, plus another tenant's rows that must stay."""
    with owner.begin() as conn:
        conn.execute(text("INSERT INTO auth.tenants (id, slug, name) VALUES (:o, 'other', 'O')"),
                     {"o": OTHER_TENANT})  # fmt: skip
        for tid in (tenant_id, OTHER_TENANT):
            conn.execute(text(
                "INSERT INTO guardrails.approval_requests (id, tenant_id, requester_id, action, "
                "payload_summary, status, expires_at) VALUES (:i, :t, :u, 'issue_refund', "
                "'Refund 1.00 to <PERSON_1>', 'pending', now() + interval '1 day')"),
                {"i": uuid.uuid4(), "t": tid, "u": viewer_id})  # fmt: skip
            conn.execute(text(
                "INSERT INTO observability.llm_calls (request_id, tenant_id, operation, alias, "
                "attempts, fallback_used, cost_unknown, latency_ms, status) "
                "VALUES ('r', :t, 'complete', 'demo-free', 1, false, true, 1.0, 'ok')"),
                {"t": tid})  # fmt: skip
            for age in (8, 6):  # one audit row past retention, one inside it
                conn.execute(text(
                    "INSERT INTO auth.audit_log (tenant_id, action, details, created_at) "
                    "VALUES (:t, 'test.event', '{}'::jsonb, :c)"),
                    {"t": tid, "c": NOW - timedelta(days=age)})  # fmt: skip
        conn.execute(text(
            "INSERT INTO auth.refresh_tokens (id, user_id, family_id, token_hash, expires_at) "
            "VALUES (:i, :u, :f, :h, now() + interval '1 day')"),
            {"i": uuid.uuid4(), "u": viewer_id, "f": uuid.uuid4(), "h": "a" * 64})  # fmt: skip
        conn.execute(text(  # an admin-created extra user, and a changed, locked guest
            "INSERT INTO auth.users (id, tenant_id, username, email, password_hash, role) "
            "VALUES (:i, :t, 'visitor-made', 'v@example.com', 'x', 'viewer')"),
            {"i": uuid.uuid4(), "t": tenant_id})  # fmt: skip
        conn.execute(text(
            "UPDATE auth.users SET role = 'admin', is_active = false, failed_logins = 9, "
            "locked_until = now() + interval '1 hour', password_hash = 'changed' WHERE id = :u"),
            {"u": viewer_id})  # fmt: skip
        yesterday, today = NOW - timedelta(days=1), NOW
        for period, calls in ((f"day:{yesterday:%Y-%m-%d}", 7), (f"day:{today:%Y-%m-%d}", 3),
                              (f"month:{today:%Y-%m}", 10)):  # fmt: skip
            conn.execute(text("INSERT INTO demo.ai_usage (period, subject, calls) "
                              "VALUES (:p, 'global', :c)"), {"p": period, "c": calls})  # fmt: skip


def test_reset_restores_the_demo_and_keeps_caps_and_other_tenants(demo_db: DemoDb) -> None:
    owner = demo_db.owner_engine()
    factory = sessionmaker(bind=owner, expire_on_commit=False)
    passwords = read_passwords(PASSWORDS)
    with factory() as session:
        seed(session, passwords)
        session.commit()
        tenant = session.scalar(select(Tenant).where(Tenant.slug == DEMO_TENANT))
        viewer = session.scalar(select(User).where(User.username == "demo-viewer"))
        assert tenant is not None and viewer is not None
        tenant_id, viewer_id = tenant.id, viewer.id
    _visitor_activity(owner, tenant_id, viewer_id)

    with request_context("reset-test"), factory() as session:
        counts = reset_demo(session, passwords, NOW)
        session.commit()
    assert counts == {"approvals": 1, "llm_calls": 1, "refresh_tokens": 1, "extra_users": 1,
                      "old_daily_usage": 1, "audit_rows_pruned": 1}  # fmt: skip

    demo, other = {"t": tenant_id}, {"t": OTHER_TENANT}
    for table in ("guardrails.approval_requests", "observability.llm_calls"):
        assert _count(owner, f"SELECT count(*) FROM {table} WHERE tenant_id = :t", **demo) == 0  # noqa: S608
        assert _count(owner, f"SELECT count(*) FROM {table} WHERE tenant_id = :t", **other) == 1  # noqa: S608
    assert _count(owner, "SELECT count(*) FROM auth.refresh_tokens") == 0
    # audit: the 8-day-old demo row went; the 6-day-old one and the other tenant's stay
    old = NOW - timedelta(days=7)
    assert _count(owner, "SELECT count(*) FROM auth.audit_log WHERE tenant_id = :t "
                  "AND created_at < :c", c=old, **demo) == 0  # fmt: skip
    assert _count(owner, "SELECT count(*) FROM auth.audit_log WHERE tenant_id = :t "
                  "AND action = 'test.event'", **demo) == 1  # fmt: skip
    assert _count(owner, "SELECT count(*) FROM auth.audit_log WHERE tenant_id = :t "
                  "AND created_at < :c", c=old, **other) == 1  # fmt: skip
    assert _count(owner, "SELECT count(*) FROM auth.audit_log WHERE action = 'demo.reset'") == 1
    # caps: only earlier days' counters go; today's and the month's stay
    with owner.connect() as conn:
        usage = {p: c for p, c in conn.execute(text("SELECT period, calls FROM demo.ai_usage"))}
    assert usage == {f"day:{NOW:%Y-%m-%d}": 3, f"month:{NOW:%Y-%m}": 10}

    with factory() as session:
        users = {u.username: u for u in session.scalars(
            select(User).where(User.tenant_id == tenant_id))}  # fmt: skip
    assert set(users) == {"demo-admin", "demo-analyst", "demo-viewer", "demo-reviewer"}
    restored = users["demo-viewer"]
    assert restored.role == Role.VIEWER and restored.is_active
    assert restored.failed_logins == 0 and restored.locked_until is None
    assert verify_password(PASSWORDS["DEMO_VIEWER_PASSWORD"], restored.password_hash)[0]
    owner.dispose()


def test_reset_the_retention_flag_does_not_leak_past_the_reset(demo_db: DemoDb) -> None:
    """After the reset (same transaction), even the owner can't delete audit rows again."""
    owner = demo_db.owner_engine()
    factory = sessionmaker(bind=owner, expire_on_commit=False)
    with request_context("reset-test"), factory() as session:
        reset_demo(session, read_passwords(PASSWORDS))
        session.execute(text("INSERT INTO auth.audit_log (action, details, created_at) VALUES "
                             "('old', '{}'::jsonb, now() - interval '30 days')"))  # fmt: skip
        with pytest.raises(Exception, match="append-only"):
            session.execute(text("DELETE FROM auth.audit_log WHERE action = 'old'"))
    owner.dispose()


def test_app_role_cannot_run_the_reset(demo_db: DemoDb) -> None:
    app = demo_db.app_engine()
    factory: sessionmaker[Session] = sessionmaker(bind=app)
    with request_context("reset-test"), factory() as session:
        with pytest.raises(Exception, match="permission denied"):
            reset_demo(session, read_passwords(PASSWORDS))
        session.rollback()
    app.dispose()


def _script() -> Any:  # noqa: ANN401 - a module loaded from a file path
    path = Path(__file__).resolve().parents[3] / "scripts" / "reset_demo.py"
    spec = importlib.util.spec_from_file_location("reset_demo", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reset_script(
    demo_db: DemoDb, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script()
    monkeypatch.delenv("OWNER_DATABASE_URL", raising=False)
    assert script.main([]) == 2
    assert "OWNER_DATABASE_URL" in capsys.readouterr().err
    monkeypatch.setenv("OWNER_DATABASE_URL",
                       demo_db.owner_url.render_as_string(hide_password=False))  # fmt: skip
    for name in PASSWORDS:
        monkeypatch.delenv(name, raising=False)
    assert script.main([]) == 2
    for name, value in PASSWORDS.items():
        monkeypatch.setenv(name, value)
    assert script.main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith("demo reset: approvals=0") and "audit_rows_pruned=0" in out
    assert all(p not in out for p in PASSWORDS.values())
