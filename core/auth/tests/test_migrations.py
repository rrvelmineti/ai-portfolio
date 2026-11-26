from __future__ import annotations

import uuid

import pytest
from ai_platform_auth.db import downgrade, migrate
from ai_platform_auth.models import SCHEMA, AuditLog, Base, Role, Tenant, User
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, Engine, create_engine, insert, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

NOT_A_HASH = "not-a-real-hash"  # noqa: S105 - placeholder, never verified


def test_tables_created(engine: Engine) -> None:
    names = set(inspect(engine).get_table_names(schema=SCHEMA))
    assert {"tenants", "users", "refresh_tokens", "audit_log", "alembic_version_auth"} <= names


def test_models_match_migration(engine: Engine) -> None:
    """The ORM models and the hand-written migration must describe the same schema."""
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "include_schemas": True,
                "version_table": "alembic_version_auth",
                "version_table_schema": SCHEMA,
                "include_name": lambda name, type_, _p: type_ != "schema" or name == SCHEMA,
            },
        )
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_username_unique_per_tenant(session: Session) -> None:
    t1, t2 = Tenant(slug="t1", name="T1"), Tenant(slug="t2", name="T2")
    session.add_all([t1, t2])
    session.flush()
    for t in (t1, t2):  # same username in two tenants is fine
        session.add(User(tenant_id=t.id, username="sam", email="s@x.test",
                         password_hash=NOT_A_HASH, role=Role.VIEWER))  # fmt: skip
    session.flush()
    nested = session.begin_nested()
    session.add(User(tenant_id=t1.id, username="sam", email="s2@x.test",
                     password_hash=NOT_A_HASH, role=Role.VIEWER))  # fmt: skip
    with pytest.raises(DBAPIError):
        session.flush()
    nested.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE auth.audit_log SET action = 'tampered'",
        "DELETE FROM auth.audit_log",
        "TRUNCATE auth.audit_log",
    ],
)
def test_audit_log_rejects_update_delete_truncate(connection: Connection, statement: str) -> None:
    connection.execute(insert(AuditLog).values(action="test.event", details={"k": "v"}))
    savepoint = connection.begin_nested()
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text(statement))
    savepoint.rollback()
    count = connection.execute(text("SELECT count(*) FROM auth.audit_log")).scalar_one()
    assert count >= 1


def test_downgrade_and_upgrade_roundtrip(db_url: URL) -> None:
    """Migrations apply cleanly to an empty database and can be rolled back."""
    admin = create_engine(db_url, isolation_level="AUTOCOMMIT")
    name = f"auth_rt_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = db_url.set(database=name)
    try:
        migrate(url)
        downgrade(url)
        eng = create_engine(url)
        assert "users" not in inspect(eng).get_table_names(schema=SCHEMA)
        migrate(url)
        assert "users" in inspect(eng).get_table_names(schema=SCHEMA)
        eng.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
