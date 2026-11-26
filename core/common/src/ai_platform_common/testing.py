"""Test helpers for Postgres-backed packages (used by each package's conftest)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from sqlalchemy import URL, create_engine, make_url, text

from ai_platform_common.config import get_bool, get_env

Migration = Callable[[URL], None]


class DatabaseUnavailable(Exception):
    """Raised when no test database is configured (callers decide to skip or fail)."""


def test_database_admin_url() -> URL:
    """``TEST_DATABASE_URL`` as a URL, or raise ``DatabaseUnavailable``."""
    raw = get_env("TEST_DATABASE_URL")
    if raw is None:
        raise DatabaseUnavailable("TEST_DATABASE_URL not set")
    return make_url(raw)


def db_tests_required() -> bool:
    """CI sets ``REQUIRE_DB_TESTS=1`` so a missing database fails instead of skipping."""
    return get_bool("REQUIRE_DB_TESTS")


@contextmanager
def temporary_database(
    admin_url: URL, migrations: Sequence[Migration] = (), prefix: str = "test"
) -> Iterator[URL]:
    """Create a uniquely named database, apply ``migrations``, drop it afterwards."""
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.set(database=name)
    try:
        for migrate in migrations:
            migrate(url)
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
