"""Reset the public demo to its seeded state (run nightly by CI; ADR 0008).

    OWNER_DATABASE_URL=postgresql://owner:...@host/db?sslmode=require \
    DEMO_ADMIN_PASSWORD=... DEMO_ANALYST_PASSWORD=... DEMO_VIEWER_PASSWORD=... \
    DEMO_REVIEWER_PASSWORD=... uv run python scripts/reset_demo.py

Uses the OWNER role (only CI holds it). See ``ai_platform_demo.reset`` for what is
removed; today's and this month's AI-usage counters are kept. Prints counts, never secrets.
"""

from __future__ import annotations

import sys
import uuid

from ai_platform_common.config import get_env
from ai_platform_common.context import request_context
from ai_platform_common.db import get_engine, make_session_factory, normalize_url, session_scope
from ai_platform_common.logging import configure_logging
from ai_platform_demo.reset import reset_demo
from ai_platform_demo.seed import SeedError, read_passwords


def main(argv: list[str] | None = None) -> int:
    configure_logging("WARNING")
    owner_url = get_env("OWNER_DATABASE_URL")
    try:
        if not owner_url:
            raise SeedError("missing env vars: OWNER_DATABASE_URL")
        passwords = read_passwords()
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    engine = get_engine(normalize_url(owner_url))
    try:
        factory = make_session_factory(engine)
        with request_context(f"demo-reset-{uuid.uuid4()}"), session_scope(factory) as session:
            counts = reset_demo(session, passwords)
    finally:
        engine.dispose()
    print("demo reset: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
