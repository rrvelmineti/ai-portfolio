"""Live check (excluded by default; run with ``uv run pytest -m live``).

Needs DATABASE_URL and at least one provider key. One real AI call.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from ai_platform_common.config import get_env

pytestmark = pytest.mark.live


def test_smoke_observability_live() -> None:
    if get_env("DATABASE_URL") is None:
        pytest.skip("DATABASE_URL not set")
    path = Path(__file__).resolve().parents[3] / "scripts" / "smoke_observability.py"
    spec = importlib.util.spec_from_file_location("smoke_observability", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if mod.first_configured_alias() is None:
        pytest.skip("no provider configured")
    assert mod.main() == 0
