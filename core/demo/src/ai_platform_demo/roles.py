"""Database roles for hosted deployments (ADR 0008).

Two roles:

- **Owner** (e.g. Neon's ``neondb_owner``): runs migrations, this bootstrap and the
  nightly reset. Its credentials live only in CI secrets, never in the app.
- **App** (``ai_portfolio_app``): what the running app connects as. It gets exactly the
  privileges in ``GRANTS`` and nothing else: no DDL, no TRUNCATE, and on the audit log
  only SELECT and INSERT, so it can never UPDATE or DELETE audit rows. (The audit trigger
  blocks those for every role anyway; see auth migration 0002 for the owner-only
  retention exception.)

Every table in the managed schemas must appear in ``GRANTS`` (or be an Alembic version
table, which the app can't touch): a new table without an explicit decision stops the
bootstrap instead of silently getting access or silently breaking the app.
"""

from __future__ import annotations

from collections.abc import Mapping

from ai_platform_auth.db import migrate as migrate_auth
from ai_platform_common.db import get_engine, normalize_url
from ai_platform_guardrails.db import migrate as migrate_guardrails
from ai_platform_observability.db import migrate as migrate_observability
from psycopg import sql
from sqlalchemy import URL, text

from ai_platform_demo.usage import SCHEMA as DEMO_SCHEMA
from ai_platform_demo.usage import migrate as migrate_usage

APP_ROLE = "ai_portfolio_app"
SCHEMAS = ("auth", "observability", "guardrails", DEMO_SCHEMA)

# Least privilege, from what the app actually does (no deletes anywhere; SELECT ... FOR
# UPDATE needs UPDATE). Approval *history* is the approval.* events in the audit log.
GRANTS: Mapping[str, tuple[str, ...]] = {
    "auth.tenants": ("SELECT", "INSERT"),
    "auth.users": ("SELECT", "INSERT", "UPDATE"),
    "auth.refresh_tokens": ("SELECT", "INSERT", "UPDATE"),
    "auth.audit_log": ("SELECT", "INSERT"),
    "observability.llm_calls": ("SELECT", "INSERT"),
    "guardrails.approval_requests": ("SELECT", "INSERT", "UPDATE"),
    f"{DEMO_SCHEMA}.ai_usage": ("SELECT", "INSERT", "UPDATE"),
}


class BootstrapError(RuntimeError):
    """The database has a table with no explicit privilege decision."""


def migrate_all(owner_url: str | URL) -> None:
    """Run every component's migrations and create the demo tables (owner role)."""
    url = normalize_url(owner_url)
    migrate_auth(url)
    migrate_observability(url)
    migrate_guardrails(url)
    migrate_usage(url)


def bootstrap_app_role(owner_url: str | URL, app_password: str, role: str = APP_ROLE) -> None:
    """Create or update the app role and set its privileges exactly to ``GRANTS``.

    Idempotent: safe to run on every deploy. The password is sent to the server only as
    a quoted literal and is never logged.
    """
    if len(app_password) < 16:
        raise BootstrapError("the app role password must be at least 16 characters")
    engine = get_engine(normalize_url(owner_url))
    try:
        with engine.begin() as conn:
            tables = list(conn.execute(
                text("SELECT schemaname || '.' || tablename FROM pg_tables "
                     "WHERE schemaname = ANY(:s) ORDER BY 1"),
                {"s": list(SCHEMAS)},
            ).scalars())  # fmt: skip
            undecided = [t for t in tables if t not in GRANTS and not _is_version_table(t)]
            if undecided:
                raise BootstrapError(f"no privilege decision for: {', '.join(undecided)}")
            missing = sorted(set(GRANTS) - set(tables))
            if missing:
                raise BootstrapError(f"tables not found (run migrations first): {missing}")
            raw = conn.connection.driver_connection
            if raw is None:  # pragma: no cover - psycopg always provides one
                raise BootstrapError("no DBAPI connection")
            with raw.cursor() as cur:
                role_id = sql.Identifier(role)
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
                verb = "ALTER" if cur.fetchone() else "CREATE"
                cur.execute(sql.SQL("{} ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.SQL(verb), role_id, sql.Literal(app_password)))  # fmt: skip
                for schema in SCHEMAS:
                    ident = sql.Identifier(schema)
                    cur.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(ident, role_id))
                    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident, role_id))
                    cur.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}")
                                .format(ident, role_id))  # fmt: skip
                    cur.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA {} TO {}")
                                .format(ident, role_id))  # fmt: skip
                for table in tables:
                    schema, name = table.split(".", 1)
                    target = sql.Identifier(schema, name)
                    cur.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(target, role_id))
                    privileges = GRANTS.get(table, ())
                    if privileges:
                        cur.execute(sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                            sql.SQL(", ").join(sql.SQL(p) for p in privileges),
                            target, role_id))  # fmt: skip
    finally:
        engine.dispose()


def _is_version_table(table: str) -> bool:
    return table.split(".", 1)[1].startswith("alembic_version")


def app_database_url(owner_url: str | URL, app_password: str, role: str = APP_ROLE) -> URL:
    """The app's URL: the owner's host and database with the app role's credentials."""
    return normalize_url(owner_url).set(username=role, password=app_password)
