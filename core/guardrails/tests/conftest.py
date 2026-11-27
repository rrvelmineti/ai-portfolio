"""Guardrails tests use the real Presidio model (loaded once per session) and, for audit
and approvals, a throwaway Postgres database."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest
from ai_platform_auth.db import migrate as migrate_auth
from ai_platform_common.testing import (
    DatabaseUnavailable,
    db_tests_required,
    temporary_database,
    test_database_admin_url,
)
from ai_platform_guardrails.injection.ml import (
    GUARD_SMALL,
    ClassifierScore,
    ModelUnavailable,
    model_dir,
    set_prompt_classifier,
    verify_files,
)
from ai_platform_guardrails.pii.engine import get_analyzer
from ai_platform_guardrails.pii.masking import set_config
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="session", autouse=True)
def _warm_analyzer() -> None:
    get_analyzer()  # load the spaCy model once


class ZeroClassifier:
    """Stand-in ML classifier that never fires, so rules-layer tests stay deterministic."""

    name = "zero"

    def score(self, text: str) -> ClassifierScore:
        return ClassifierScore(0.0, 1, False)


@pytest.fixture(autouse=True)
def _ml_classifier(request: pytest.FixtureRequest) -> Iterator[None]:
    """Default: a classifier that never fires. ``@pytest.mark.real_classifier``: the real
    pinned model (skipped if not downloaded, an error when ``REQUIRE_GUARD_MODEL=1``)."""
    if request.node.get_closest_marker("real_classifier"):
        try:
            verify_files(GUARD_SMALL, model_dir(GUARD_SMALL))
        except ModelUnavailable as exc:
            if os.environ.get("REQUIRE_GUARD_MODEL") == "1":
                pytest.fail(f"REQUIRE_GUARD_MODEL=1 but the model is unavailable: {exc}")
            pytest.skip("guard model not downloaded (scripts/download_guard_model.py)")
        set_prompt_classifier(None)
    else:
        set_prompt_classifier(ZeroClassifier())
    yield
    set_prompt_classifier(None)


@pytest.fixture(autouse=True)
def _reset_pii_config() -> Iterator[None]:
    yield
    set_config(None)


# ---------------------------------------------------------------- Postgres fixtures
def _migrations() -> list[Callable[[URL], None]]:
    from ai_platform_guardrails.db import migrate as migrate_guardrails

    return [migrate_auth, migrate_guardrails]


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
