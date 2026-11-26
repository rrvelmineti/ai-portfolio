"""Guardrails tests use the real Presidio model (loaded once per session) and, for audit
and approvals, a throwaway Postgres database."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from ai_platform_auth.db import migrate as migrate_auth
from ai_platform_common.testing import (
    DatabaseUnavailable,
    db_tests_required,
    temporary_database,
    test_database_admin_url,
)
from ai_platform_guardrails.pii.engine import get_analyzer
from ai_platform_guardrails.pii.masking import set_config
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="session", autouse=True)
def _warm_analyzer() -> None:
    get_analyzer()  # load the spaCy model once


@pytest.fixture(autouse=True)
def _reset_pii_config() -> Iterator[None]:
    yield
    set_config(None)


# ---------------------------------------------------------------- Postgres fixtures
def _migrations() -> list[Callable[[URL], None]]:
    return [migrate_auth]


@pytest.fixture(scope="session")
def db_url() -> Iterator[URL]:
    try:
        admin_url = test_database_admin_url()
    except DatabaseUnavailable:
        if db_tests_required():
            pytest.fail("TEST_DATABASE_URL is not set but REQUIRE_DB_TESTS=1")
        pytest.skip("TEST_DATABASE_URL not set; skipping Postgres tests")
    with temporary_database(admin_url, _migrations(), prefix="guard_test") as url:
        yield url


@pytest.fixture(scope="session")
def engine(db_url: URL) -> Iterator[Engine]:
    eng = create_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def committed_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Real sessions (several writers per call; see core/observability/tests/conftest.py)."""
    return sessionmaker(bind=engine, expire_on_commit=False)
