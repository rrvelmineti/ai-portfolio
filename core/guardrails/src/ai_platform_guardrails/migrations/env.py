"""Alembic environment for ai_platform_guardrails (run via ``db.migrate``)."""

from __future__ import annotations

from ai_platform_guardrails.approvals.models import SCHEMA, Base
from alembic import context
from sqlalchemy import create_engine, text

config = context.config
url = config.attributes.get("database_url") or config.get_main_option("sqlalchemy.url")
version_table = config.attributes.get("version_table", "alembic_version_guardrails")


def run_migrations_online() -> None:
    engine = create_engine(str(url))
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            version_table=version_table,
            version_table_schema=SCHEMA,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
