"""Database wiring for the observability package."""

from __future__ import annotations

from pathlib import Path

from ai_platform_common.db import downgrade_migrations, run_migrations
from sqlalchemy import URL

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
VERSION_TABLE = "alembic_version_observability"


def migrate(url: str | URL, revision: str = "head") -> None:
    """Apply the observability migrations to the database at ``url``."""
    run_migrations(MIGRATIONS_DIR, url, version_table=VERSION_TABLE, revision=revision)


def downgrade(url: str | URL, revision: str = "base") -> None:
    """Roll the observability migrations back to ``revision``."""
    downgrade_migrations(MIGRATIONS_DIR, url, version_table=VERSION_TABLE, revision=revision)
