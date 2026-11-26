"""Guardrails tests use the real Presidio model (loaded once per session)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from ai_platform_guardrails.pii.engine import get_analyzer
from ai_platform_guardrails.pii.masking import set_config


@pytest.fixture(scope="session", autouse=True)
def _warm_analyzer() -> None:
    get_analyzer()  # load the spaCy model once


@pytest.fixture(autouse=True)
def _reset_pii_config() -> Iterator[None]:
    yield
    set_config(None)
