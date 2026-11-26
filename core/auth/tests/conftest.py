"""DB fixtures for auth tests.

Tests that need Postgres use the ``db_url`` / ``session`` fixtures. A fresh database is
created from ``TEST_DATABASE_URL`` per test session, migrated, and dropped afterwards.
Each test runs inside a transaction that is rolled back, so tests never see each
other's rows (and the append-only audit log needs no cleanup).

Without ``TEST_DATABASE_URL`` the DB tests are skipped locally; with
``REQUIRE_DB_TESTS=1`` (set in CI) a missing database is an error instead.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from ai_platform_auth.db import migrate
from ai_platform_common.testing import (
    DatabaseUnavailable,
    db_tests_required,
    temporary_database,
    test_database_admin_url,
)
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="session")
def db_url() -> Iterator[URL]:
    try:
        admin_url = test_database_admin_url()
    except DatabaseUnavailable:
        if db_tests_required():
            pytest.fail("TEST_DATABASE_URL is not set but REQUIRE_DB_TESTS=1")
        pytest.skip("TEST_DATABASE_URL not set; skipping Postgres tests")
    with temporary_database(admin_url, [migrate], prefix="auth_test") as url:
        yield url


@pytest.fixture(scope="session")
def engine(db_url: URL) -> Iterator[Engine]:
    eng = create_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


@pytest.fixture
def session_factory(connection: Connection) -> sessionmaker[Session]:
    """Sessions bound to the test transaction; their commits become savepoints."""
    return sessionmaker(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = session_factory()
    yield s
    s.close()
