"""Demo-ops tests run against a real Postgres (``TEST_DATABASE_URL``), because roles,
grants and triggers can't be faked. Each test gets a fresh database and a uniquely named
app role, both removed afterwards."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from ai_platform_common.testing import (
    DatabaseUnavailable,
    db_tests_required,
    temporary_database,
    test_database_admin_url,
)
from ai_platform_demo.roles import bootstrap_app_role, migrate_all
from sqlalchemy import URL, Engine, create_engine, text


@dataclass
class DemoDb:
    owner_url: URL
    app_role: str
    app_password: str

    @property
    def app_url(self) -> URL:
        return self.owner_url.set(username=self.app_role, password=self.app_password)

    def owner_engine(self) -> Engine:
        return create_engine(self.owner_url)

    def app_engine(self) -> Engine:
        return create_engine(self.app_url)


@pytest.fixture
def admin_url() -> URL:
    try:
        return test_database_admin_url()
    except DatabaseUnavailable:
        if db_tests_required():
            pytest.fail("TEST_DATABASE_URL is not set but REQUIRE_DB_TESTS=1")
        pytest.skip("TEST_DATABASE_URL not set")


@pytest.fixture
def migrated_url(admin_url: URL) -> Iterator[URL]:
    with temporary_database(admin_url, [migrate_all], prefix="demo") as url:
        yield url


@pytest.fixture
def demo_db(admin_url: URL, migrated_url: URL) -> Iterator[DemoDb]:
    role = f"app_test_{uuid.uuid4().hex[:10]}"
    password = secrets.token_urlsafe(24)
    bootstrap_app_role(migrated_url, password, role=role)
    db = DemoDb(migrated_url, role, password)
    try:
        yield db
    finally:
        engine = create_engine(migrated_url)
        with engine.begin() as conn:
            conn.execute(text(f'DROP OWNED BY "{role}"'))
        engine.dispose()
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            # the database is dropped by temporary_database; the role is cluster-wide
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                              "WHERE usename = :r"), {"r": role})  # fmt: skip
        admin.dispose()
        _drop_role_later.append((admin_url, role))


_drop_role_later: list[tuple[URL, str]] = []


@pytest.fixture(autouse=True, scope="session")
def _drop_test_roles() -> Iterator[None]:
    yield
    for admin_url, role in _drop_role_later:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()
