"""Migrate the database and set up the restricted app role (run by CI before deploying).

    OWNER_DATABASE_URL=postgresql://owner:...@host/db?sslmode=require \
    APP_DB_PASSWORD=... uv run python scripts/migrate_and_bootstrap.py

Uses the OWNER role (only CI holds its credentials). Steps:
1. run every component's migrations and create the demo tables;
2. create or update the app role ``ai_portfolio_app`` with ``APP_DB_PASSWORD`` and set its
   privileges exactly (SELECT/INSERT only on the audit log; see ai_platform_demo.roles).

Idempotent. Prints no secrets. The app itself then connects as the app role.
"""

from __future__ import annotations

import sys

from ai_platform_common.config import get_env
from ai_platform_common.logging import configure_logging
from ai_platform_demo.roles import APP_ROLE, BootstrapError, bootstrap_app_role, migrate_all


def main(argv: list[str] | None = None) -> int:
    configure_logging("WARNING")
    owner_url = get_env("OWNER_DATABASE_URL")
    password = get_env("APP_DB_PASSWORD")
    missing = [n for n, v in (("OWNER_DATABASE_URL", owner_url), ("APP_DB_PASSWORD", password))
               if not v]  # fmt: skip
    if missing:
        print(f"missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 2
    assert owner_url is not None and password is not None  # noqa: S101 - narrowed above
    role = get_env("APP_DB_ROLE") or APP_ROLE
    try:
        migrate_all(owner_url)
        bootstrap_app_role(owner_url, password, role=role)
    except BootstrapError as exc:
        print(f"bootstrap refused: {exc}", file=sys.stderr)
        return 1
    print(f"migrated; app role '{role}' bootstrapped with least-privilege grants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
