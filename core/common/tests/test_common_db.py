from __future__ import annotations

from pathlib import Path

import pytest
from ai_platform_common import db
from sqlalchemy import text


def test_database_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/x")
    assert db.database_url().database == "x"
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(db.DatabaseConfigError):
        db.database_url()


def test_session_scope_commits_and_rolls_back(tmp_path: Path) -> None:
    engine = db.get_engine(f"sqlite:///{tmp_path / 't.db'}")
    factory = db.make_session_factory(engine)
    with db.session_scope(factory) as s:
        s.execute(text("create table t (x int)"))
        s.execute(text("insert into t values (1)"))
    with pytest.raises(RuntimeError), db.session_scope(factory) as s:
        s.execute(text("insert into t values (2)"))
        raise RuntimeError("boom")
    with db.session_scope(factory) as s:
        assert s.execute(text("select count(*) from t")).scalar_one() == 1


def test_alembic_config_carries_url_and_version_table() -> None:
    cfg = db.alembic_config("/x/migrations", "postgresql+psycopg://u:secret@h/d", "v_tbl")
    assert cfg.get_main_option("script_location") == "/x/migrations"
    assert cfg.attributes["version_table"] == "v_tbl"
    assert cfg.attributes["database_url"].endswith("@h/d")
