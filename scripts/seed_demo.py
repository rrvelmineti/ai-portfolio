"""Create a demo tenant with one admin, two analysts and one viewer.

Passwords come only from env vars (never hard-coded):
DEMO_ADMIN_PASSWORD, DEMO_ANALYST_PASSWORD, DEMO_VIEWER_PASSWORD, DEMO_REVIEWER_PASSWORD
(12+ chars each).
Runs the auth migrations first. Safe to re-run: existing users are left alone
unless --reset-passwords is given (which also restores their role and unlocks them).

    set -a; source .env; set +a
    uv run python scripts/seed_demo.py

The logic lives in ``ai_platform_demo.seed``, shared with the nightly reset.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from ai_platform_auth.db import migrate
from ai_platform_common.context import request_context
from ai_platform_common.db import database_url, get_engine, make_session_factory, session_scope
from ai_platform_common.logging import configure_logging
from ai_platform_demo.seed import DEMO_TENANT, DEMO_USERS, SeedError, read_passwords, seed

__all__ = ["DEMO_TENANT", "DEMO_USERS", "SeedError", "main", "read_passwords", "seed"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the demo tenant and users.")
    parser.add_argument("--reset-passwords", action="store_true")
    args = parser.parse_args(argv)
    configure_logging("WARNING")
    try:
        passwords = read_passwords()
        url = database_url()
    except Exception as exc:  # config problems: explain and exit non-zero
        print(f"error: {exc}", file=sys.stderr)
        return 2
    migrate(url)
    factory = make_session_factory(get_engine(url))
    with request_context(f"seed-{uuid.uuid4()}"), session_scope(factory) as session:
        for line in seed(session, passwords, reset_passwords=args.reset_passwords):
            print(line)
    print(f"Log in with tenant '{DEMO_TENANT}' and the passwords from your env vars.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
