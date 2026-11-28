"""Database wiring for claims onboarding (schema ``claims``)."""

from __future__ import annotations

from pathlib import Path

from ai_platform_common.db import downgrade_migrations, run_migrations
from sqlalchemy import URL

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
VERSION_TABLE = "alembic_version_claims"


def migrate(url: str | URL, revision: str = "head") -> None:
    """Apply the claims migrations (owner role)."""
    run_migrations(MIGRATIONS_DIR, url, version_table=VERSION_TABLE, revision=revision)


def downgrade(url: str | URL, revision: str = "base") -> None:
    """Revert the claims migrations (tests and local use)."""
    downgrade_migrations(MIGRATIONS_DIR, url, version_table=VERSION_TABLE, revision=revision)
