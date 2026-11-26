"""DB fixtures for auth tests.

Tests that need Postgres use the ``db_url`` / ``session`` fixtures. They read
``TEST_DATABASE_URL`` (an admin connection, e.g. the docker-compose or CI service).
A fresh database is created per test session, migrated, and dropped afterwards.
Each test runs inside a transaction that is rolled back, so tests never see each
other's rows (and the append-only audit log needs no cleanup).

Without ``TEST_DATABASE_URL`` the DB tests are skipped locally; with
``REQUIRE_DB_TESTS=1`` (set in CI) a missing database is an error instead.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from ai_platform_auth.db import migrate
from ai_platform_common.config import get_bool, get_env
from sqlalchemy import Connection, Engine, create_engine, make_url, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="session")
def db_url() -> Iterator[URL]:
    raw = get_env("TEST_DATABASE_URL")
    if raw is None:
        if get_bool("REQUIRE_DB_TESTS"):
            pytest.fail("TEST_DATABASE_URL is not set but REQUIRE_DB_TESTS=1")
        pytest.skip("TEST_DATABASE_URL not set; skipping Postgres tests")
    admin_url = make_url(raw)
    name = f"auth_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.set(database=name)
    try:
        migrate(url)
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


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
