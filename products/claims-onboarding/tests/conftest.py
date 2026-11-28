"""Claims onboarding tests run on a real Postgres (``TEST_DATABASE_URL``): tenant
isolation, composite foreign keys and audit rows can't be faked.

One database per test session (all platform migrations + claims); each test runs in a
transaction that is rolled back, so tests never see each other's rows. All people and
data here are synthetic.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from ai_platform_auth import Role, TenantScope
from ai_platform_auth.db import migrate as migrate_auth
from ai_platform_auth.models import Tenant, User
from ai_platform_auth.passwords import hash_password
from ai_platform_common.testing import (
    DatabaseUnavailable,
    db_tests_required,
    temporary_database,
    test_database_admin_url,
)
from ai_platform_guardrails.db import migrate as migrate_guardrails
from ai_platform_observability.db import migrate as migrate_observability
from claims_onboarding.db import migrate as migrate_claims
from sqlalchemy import URL, Connection, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

PASSWORD = "synthetic-claims-pass-1"
JWT_SECRET = "claims-unit-test-jwt-secret-" + "q" * 40
_HASH: dict[str, str] = {}


@pytest.fixture(scope="session")
def admin_url() -> URL:
    try:
        return test_database_admin_url()
    except DatabaseUnavailable:
        if db_tests_required():
            pytest.fail("TEST_DATABASE_URL is not set but REQUIRE_DB_TESTS=1")
        pytest.skip("TEST_DATABASE_URL not set; skipping Postgres tests")


@pytest.fixture(scope="session")
def db_url(admin_url: URL) -> Iterator[URL]:
    migrations = [migrate_auth, migrate_observability, migrate_guardrails, migrate_claims]
    with temporary_database(admin_url, migrations, prefix="claims_test") as url:
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


def make_user(session: Session, tenant: Tenant, username: str, role: Role) -> User:
    if PASSWORD not in _HASH:  # Argon2 is slow on purpose; hash once
        _HASH[PASSWORD] = hash_password(PASSWORD)
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        username=username,
        email=f"{username}@example.test",
        password_hash=_HASH[PASSWORD],
        role=role,
    )
    session.add(user)
    session.flush()
    return user


@dataclass
class Org:
    """One tenant (an onboarding organisation) and its people."""

    tenant: Tenant
    manager: User  # admin
    manager2: User  # admin
    analyst: User
    analyst2: User
    viewer: User

    @property
    def scope(self) -> TenantScope:
        return TenantScope(self.tenant.id)


def _org(session: Session, slug: str) -> Org:
    tenant = Tenant(id=uuid.uuid4(), slug=slug, name=f"{slug.upper()} Onboarding (synthetic)")
    session.add(tenant)
    session.flush()
    return Org(
        tenant=tenant,
        manager=make_user(session, tenant, "maya-manager", Role.ADMIN),
        manager2=make_user(session, tenant, "noor-manager", Role.ADMIN),
        analyst=make_user(session, tenant, "ari-analyst", Role.ANALYST),
        analyst2=make_user(session, tenant, "bea-analyst", Role.ANALYST),
        viewer=make_user(session, tenant, "val-viewer", Role.VIEWER),
    )


@pytest.fixture
def org_a(session: Session) -> Org:
    return _org(session, f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def org_b(session: Session) -> Org:
    return _org(session, f"bolt-{uuid.uuid4().hex[:6]}")
