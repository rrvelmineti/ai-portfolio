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


# The app role's privileges on this schema (passed to the demo role bootstrap by
# scripts/migrate_and_bootstrap.py). From what the service does: no DELETE anywhere;
# SELECT ... FOR UPDATE needs UPDATE; sign-offs and file columns are insert-only.
CLAIMS_GRANTS: dict[str, tuple[str, ...]] = {
    "claims.clients": ("SELECT", "INSERT", "UPDATE"),
    "claims.data_dictionaries": ("SELECT", "INSERT", "UPDATE"),
    "claims.files": ("SELECT", "INSERT", "UPDATE"),
    "claims.file_columns": ("SELECT", "INSERT"),
    "claims.mappings": ("SELECT", "INSERT", "UPDATE"),
    "claims.rule_definitions": ("SELECT", "INSERT", "UPDATE"),
    "claims.rule_results": ("SELECT", "INSERT", "UPDATE"),
    "claims.issue_letters": ("SELECT", "INSERT", "UPDATE"),
    "claims.go_live_signoffs": ("SELECT", "INSERT"),
}
