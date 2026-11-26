"""Shared database helpers: engine, sessions and an Alembic migrations runner.

Every component that owns tables keeps its own Alembic folder and its own version
table, so components migrate independently against one database::

    from ai_platform_common.db import get_engine, make_session_factory, run_migrations

    engine = get_engine()                       # reads DATABASE_URL
    run_migrations(MIGRATIONS_DIR, engine.url, version_table="alembic_version_auth")
    Session = make_session_factory(engine)
    with session_scope(Session) as session:
        ...

Connection strings come only from the environment. Use the ``postgresql+psycopg://``
driver (psycopg 3).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_common.config import get_env


class DatabaseConfigError(RuntimeError):
    """Raised when no database URL is configured."""


def database_url(env_var: str = "DATABASE_URL") -> URL:
    """Return the database URL from ``env_var`` or raise ``DatabaseConfigError``."""
    raw = get_env(env_var)
    if raw is None:
        raise DatabaseConfigError(f"{env_var} is not set")
    return make_url(raw)


def get_engine(url: str | URL | None = None, *, echo: bool = False) -> Engine:
    """Create an engine. ``echo`` stays off by default so SQL parameters are never logged."""
    return create_engine(url or database_url(), echo=echo, pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """A session factory with ``expire_on_commit=False`` (safe to read objects after commit)."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit on success, roll back on error, always close."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def alembic_config(script_location: Path | str, url: str | URL, version_table: str) -> Config:
    """Build an in-memory Alembic config (no ``alembic.ini`` needed)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(script_location))
    rendered = url if isinstance(url, str) else url.render_as_string(hide_password=False)
    cfg.attributes["database_url"] = rendered
    cfg.attributes["version_table"] = version_table
    return cfg


def run_migrations(
    script_location: Path | str,
    url: str | URL,
    *,
    version_table: str,
    revision: str = "head",
) -> None:
    """Upgrade the database to ``revision`` using the migrations in ``script_location``."""
    command.upgrade(alembic_config(script_location, url, version_table), revision)


def downgrade_migrations(
    script_location: Path | str,
    url: str | URL,
    *,
    version_table: str,
    revision: str = "base",
) -> None:
    """Downgrade the database to ``revision`` (default: remove everything)."""
    command.downgrade(alembic_config(script_location, url, version_table), revision)
