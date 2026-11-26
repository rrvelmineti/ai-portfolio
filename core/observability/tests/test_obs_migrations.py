from __future__ import annotations

import pytest
from ai_platform_common.testing import temporary_database
from ai_platform_observability.db import downgrade, migrate
from ai_platform_observability.models import SCHEMA, Base
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.engine import URL


def test_llm_calls_table_and_models_match(engine: Engine) -> None:
    assert "llm_calls" in inspect(engine).get_table_names(schema=SCHEMA)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "include_schemas": True,
                "version_table": "alembic_version_observability",
                "version_table_schema": SCHEMA,
                "include_name": lambda name, type_, _p: type_ != "schema" or name == SCHEMA,
            },
        )
        assert compare_metadata(ctx, Base.metadata) == []


def test_roundtrip(db_url: URL) -> None:
    admin = db_url.set(database="postgres")
    with temporary_database(admin, prefix="obs_rt") as url:
        migrate(url)
        downgrade(url)
        eng = create_engine(url)
        assert "llm_calls" not in inspect(eng).get_table_names(schema=SCHEMA)
        migrate(url)
        assert "llm_calls" in inspect(eng).get_table_names(schema=SCHEMA)
        eng.dispose()


def test_unavailable_database_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_platform_common import testing

    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(testing.DatabaseUnavailable):
        testing.test_database_admin_url()
    monkeypatch.setenv("REQUIRE_DB_TESTS", "1")
    assert testing.db_tests_required() is True
