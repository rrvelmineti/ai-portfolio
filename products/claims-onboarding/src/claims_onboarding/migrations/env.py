"""Alembic environment for claims_onboarding (run via ``db.migrate``)."""

from __future__ import annotations

from alembic import context
from claims_onboarding.models import SCHEMA, Base
from sqlalchemy import create_engine, text

config = context.config
url = config.attributes.get("database_url") or config.get_main_option("sqlalchemy.url")
version_table = config.attributes.get("version_table", "alembic_version_claims")


def _include_object(obj: object, name: str | None, type_: str, *_args: object) -> bool:
    # only this package's schema; other components own theirs
    schema = getattr(obj, "schema", None)
    return type_ != "table" or schema == SCHEMA


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
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
