"""Database wiring for the guardrails package (approval requests)."""

from __future__ import annotations

from pathlib import Path

from ai_platform_common.db import downgrade_migrations, run_migrations
from sqlalchemy import URL

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
VERSION_TABLE = "alembic_version_guardrails"


def migrate(url: str | URL, revision: str = "head") -> None:
    """Apply the guardrails migrations."""
    run_migrations(MIGRATIONS_DIR, url, version_table=VERSION_TABLE, revision=revision)


def downgrade(url: str | URL, revision: str = "base") -> None:
    downgrade_migrations(MIGRATIONS_DIR, url, version_table=VERSION_TABLE, revision=revision)
