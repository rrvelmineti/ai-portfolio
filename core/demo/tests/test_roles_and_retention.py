"""The app role's privileges and the audit log's retention rule, on a real Postgres."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from ai_platform_common.testing import temporary_database
from ai_platform_demo.roles import (
    GRANTS,
    BootstrapError,
    app_database_url,
    bootstrap_app_role,
)
from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from .conftest import DemoDb

TENANT = uuid.UUID("00000000-0000-0000-0000-00000000d001")


def _insert_audit(engine: Engine, created_at: datetime | None = None) -> int:
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO auth.tenants (id, slug, name) VALUES (:t, 'demo', 'Demo') "
                          "ON CONFLICT DO NOTHING"), {"t": TENANT})  # fmt: skip
        if created_at is None:
            row = conn.execute(text("INSERT INTO auth.audit_log (tenant_id, action, details) "
                                    "VALUES (:t, 'test.event', '{}'::jsonb) RETURNING id"),
                               {"t": TENANT})  # fmt: skip
        else:
            row = conn.execute(text("INSERT INTO auth.audit_log (tenant_id, action, details, "
                                    "created_at) VALUES (:t, 'test.event', '{}'::jsonb, :c) "
                                    "RETURNING id"), {"t": TENANT, "c": created_at})  # fmt: skip
        return int(row.scalar_one())


def _denied(engine: Engine, statement: str, **params: object) -> str:
    with pytest.raises(DBAPIError) as info, engine.begin() as conn:
        conn.execute(text(statement), params)
    return str(info.value.orig)


def test_app_role_gets_exactly_the_granted_privileges(demo_db: DemoDb) -> None:
    owner = demo_db.owner_engine()
    with owner.connect() as conn:
        rows = conn.execute(text(
            "SELECT table_schema || '.' || table_name, privilege_type "
            "FROM information_schema.role_table_grants WHERE grantee = :r"),
            {"r": demo_db.app_role}).all()  # fmt: skip
    granted: dict[str, set[str]] = {}
    for table, privilege in rows:
        granted.setdefault(table, set()).add(privilege)
    assert granted == {t: set(p) for t, p in GRANTS.items()}
    owner.dispose()


def test_app_role_can_insert_and_read_audit_but_never_update_or_delete(demo_db: DemoDb) -> None:
    app = demo_db.app_engine()
    row_id = _insert_audit(app)
    with app.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM auth.audit_log")).scalar_one() == 1
    for statement in (
        "UPDATE auth.audit_log SET action = 'tampered' WHERE id = :i",
        "DELETE FROM auth.audit_log WHERE id = :i",
    ):
        assert "permission denied" in _denied(app, statement, i=row_id)
    assert "permission denied" in _denied(app, "TRUNCATE auth.audit_log")
    # even with the retention flag set and an old row, the app role is refused
    with pytest.raises(DBAPIError) as info, app.begin() as conn:
        conn.execute(text("SET LOCAL ai_portfolio.audit_retention = 'on'"))
        conn.execute(text("DELETE FROM auth.audit_log"))
    assert "permission denied" in str(info.value.orig)
    app.dispose()


def test_app_role_has_no_ddl_and_no_access_to_version_tables(demo_db: DemoDb) -> None:
    app = demo_db.app_engine()
    for schema in ("auth", "observability", "guardrails", "demo", "public"):
        assert "permission denied" in _denied(app, f"CREATE TABLE {schema}.evil (x int)")
    assert "permission denied" in _denied(app, "SELECT * FROM auth.alembic_version_auth")
    assert "permission denied" in _denied(app, "DELETE FROM guardrails.approval_requests")
    assert "permission denied" in _denied(app, "DELETE FROM auth.users")
    app.dispose()


def test_audit_retention_only_for_the_owner_with_the_flag_and_old_rows(demo_db: DemoDb) -> None:
    owner = demo_db.owner_engine()
    old = _insert_audit(owner, datetime.now(UTC) - timedelta(days=8))
    recent = _insert_audit(owner, datetime.now(UTC) - timedelta(days=6))
    # 1. owner without the flag: blocked by the trigger
    assert "append-only" in _denied(owner, "DELETE FROM auth.audit_log WHERE id = :i", i=old)
    # 2. owner with the flag: only rows older than 7 days go
    with owner.begin() as conn:
        conn.execute(text("SET LOCAL ai_portfolio.audit_retention = 'on'"))
        conn.execute(text("DELETE FROM auth.audit_log WHERE id = :i"), {"i": old})
    with pytest.raises(DBAPIError, match="append-only"), owner.begin() as conn:
        conn.execute(text("SET LOCAL ai_portfolio.audit_retention = 'on'"))
        conn.execute(text("DELETE FROM auth.audit_log WHERE id = :i"), {"i": recent})
    # 3. UPDATE and TRUNCATE stay blocked for the owner too, flag or not
    with pytest.raises(DBAPIError, match="append-only"), owner.begin() as conn:
        conn.execute(text("SET LOCAL ai_portfolio.audit_retention = 'on'"))
        conn.execute(text("UPDATE auth.audit_log SET action = 'x' WHERE id = :i"), {"i": recent})
    with pytest.raises(DBAPIError, match="append-only"), owner.begin() as conn:
        conn.execute(text("SET LOCAL ai_portfolio.audit_retention = 'on'"))
        conn.execute(text("TRUNCATE auth.audit_log"))
    with owner.connect() as conn:
        ids = set(conn.execute(text("SELECT id FROM auth.audit_log")).scalars())
    assert ids == {recent}
    owner.dispose()


def _stored_password(demo_db: DemoDb) -> str:
    """The role's stored password hash (the test database user is a superuser)."""
    owner = demo_db.owner_engine()
    with owner.connect() as conn:
        value = conn.execute(text("SELECT rolpassword FROM pg_authid WHERE rolname = :r"),
                             {"r": demo_db.app_role}).scalar_one()  # fmt: skip
    owner.dispose()
    return str(value)


def test_bootstrap_is_idempotent_and_rotates_the_password(demo_db: DemoDb) -> None:
    before = _stored_password(demo_db)
    assert before.startswith("SCRAM-SHA-256$")  # never stored in clear text
    new_password = "rotated-" + uuid.uuid4().hex
    bootstrap_app_role(demo_db.owner_url, new_password, role=demo_db.app_role)
    bootstrap_app_role(demo_db.owner_url, new_password, role=demo_db.app_role)
    after = _stored_password(demo_db)
    assert after.startswith("SCRAM-SHA-256$") and after != before
    fresh = create_engine(app_database_url(demo_db.owner_url, new_password, demo_db.app_role))
    with fresh.connect() as conn:
        assert conn.execute(text("SELECT current_user")).scalar_one() == demo_db.app_role
    fresh.dispose()


def test_bootstrap_refuses_a_table_without_a_privilege_decision(demo_db: DemoDb) -> None:
    owner = demo_db.owner_engine()
    with owner.begin() as conn:
        conn.execute(text("CREATE TABLE auth.new_feature (id int)"))
    with pytest.raises(BootstrapError, match="auth.new_feature"):
        bootstrap_app_role(demo_db.owner_url, "x" * 20, role=demo_db.app_role)
    owner.dispose()


def test_bootstrap_rejects_short_passwords_and_unmigrated_databases(
    demo_db: DemoDb, admin_url: URL
) -> None:
    with pytest.raises(BootstrapError, match="16 characters"):
        bootstrap_app_role(demo_db.owner_url, "short", role=demo_db.app_role)
    with (
        temporary_database(admin_url, prefix="demo_empty") as empty,
        pytest.raises(BootstrapError, match="run migrations first"),
    ):
        bootstrap_app_role(empty, "x" * 20, role=demo_db.app_role)


def test_app_database_url_accepts_provider_strings() -> None:
    url = app_database_url("postgresql://owner:o@ep-x.neon.tech/neondb?sslmode=require", "pw")
    assert url.drivername == "postgresql+psycopg" and url.username == "ai_portfolio_app"
    assert url.password == "pw" and url.query["sslmode"] == "require"


def test_migrate_and_bootstrap_script(
    admin_url: URL, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "migrate_and_bootstrap.py"
    spec = importlib.util.spec_from_file_location("migrate_and_bootstrap", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.delenv("OWNER_DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_DB_PASSWORD", raising=False)
    assert script.main([]) == 2
    assert "OWNER_DATABASE_URL, APP_DB_PASSWORD" in capsys.readouterr().err
    role = f"app_test_{uuid.uuid4().hex[:10]}"
    with temporary_database(admin_url, prefix="demo_script") as url:
        # a provider-style URL, as pasted from the Neon dashboard
        monkeypatch.setenv("OWNER_DATABASE_URL", url.set(drivername="postgresql")
                           .render_as_string(hide_password=False))  # fmt: skip
        monkeypatch.setenv("APP_DB_PASSWORD", "y" * 20)
        monkeypatch.setenv("APP_DB_ROLE", role)
        assert script.main([]) == 0
        assert script.main([]) == 0  # idempotent
        out = capsys.readouterr().out
        assert "y" * 20 not in out and role in out
        monkeypatch.setenv("APP_DB_PASSWORD", "short")
        assert script.main([]) == 1
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(text(f'DROP OWNED BY "{role}"'))
        engine.dispose()
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
    admin.dispose()
