"""The claims migration: upgrade, matches the models exactly, downgrade cleanly."""

from __future__ import annotations

from ai_platform_common.testing import temporary_database
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from claims_onboarding.db import VERSION_TABLE, downgrade, migrate
from claims_onboarding.models import SCHEMA, Base
from sqlalchemy import URL, create_engine, inspect

TABLES = {
    "clients",
    "data_dictionaries",
    "files",
    "file_columns",
    "mappings",
    "rule_definitions",
    "rule_results",
    "issue_letters",
    "go_live_signoffs",
}


def test_upgrade_matches_models_and_downgrades(admin_url: URL) -> None:
    with temporary_database(admin_url, prefix="claims_mig") as url:
        migrate(url)
        engine = create_engine(url)
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names(schema=SCHEMA))
            assert tables == TABLES | {VERSION_TABLE}
            # every table has a NOT NULL tenant_id
            for table in TABLES:
                cols = {c["name"]: c for c in inspect(conn).get_columns(table, schema=SCHEMA)}
                assert cols["tenant_id"]["nullable"] is False, table
            ctx = MigrationContext.configure(
                conn,
                opts={
                    "include_schemas": True,
                    "version_table": VERSION_TABLE,
                    "version_table_schema": SCHEMA,
                    "include_object": lambda obj, name, type_, *_: (
                        type_ != "table"
                        or (getattr(obj, "schema", None) == SCHEMA and name != VERSION_TABLE)
                    ),
                },
            )
            assert compare_metadata(ctx, Base.metadata) == []  # migration == models
        migrate(url)  # idempotent at head
        downgrade(url)
        with engine.connect() as conn:
            assert set(inspect(conn).get_table_names(schema=SCHEMA)) <= {VERSION_TABLE}
        migrate(url)  # and back up again
        engine.dispose()
