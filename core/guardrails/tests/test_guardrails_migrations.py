from __future__ import annotations

from ai_platform_common.testing import temporary_database
from ai_platform_guardrails.approvals.models import SCHEMA, Base
from ai_platform_guardrails.db import downgrade, migrate
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.engine import URL


def test_models_match_migration(engine: Engine) -> None:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "include_schemas": True,
                "version_table": "alembic_version_guardrails",
                "version_table_schema": SCHEMA,
                "include_name": lambda name, type_, _p: type_ != "schema" or name == SCHEMA,
            },
        )
        assert compare_metadata(ctx, Base.metadata) == []


def test_roundtrip(db_url: URL) -> None:
    with temporary_database(db_url.set(database="postgres"), prefix="guard_rt") as url:
        migrate(url)
        downgrade(url)
        eng = create_engine(url)
        assert "approval_requests" not in inspect(eng).get_table_names(schema=SCHEMA)
        migrate(url)
        assert "approval_requests" in inspect(eng).get_table_names(schema=SCHEMA)
        eng.dispose()
