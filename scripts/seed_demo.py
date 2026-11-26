"""Create a demo tenant with one admin, one analyst and one viewer.

Passwords come only from env vars (never hard-coded):
DEMO_ADMIN_PASSWORD, DEMO_ANALYST_PASSWORD, DEMO_VIEWER_PASSWORD (12+ chars each).
Runs the auth migrations first. Safe to re-run: existing users are left alone
unless --reset-passwords is given.

    set -a; source .env; set +a
    uv run python scripts/seed_demo.py
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Mapping

from ai_platform_auth.audit import RequestInfo, audit
from ai_platform_auth.db import migrate
from ai_platform_auth.models import Role, Tenant, User
from ai_platform_auth.passwords import hash_password
from ai_platform_common.config import get_env
from ai_platform_common.db import database_url, get_engine, make_session_factory, session_scope
from ai_platform_common.logging import configure_logging
from sqlalchemy import select
from sqlalchemy.orm import Session

DEMO_TENANT = "demo"
DEMO_USERS: list[tuple[str, Role, str]] = [
    ("demo-admin", Role.ADMIN, "DEMO_ADMIN_PASSWORD"),
    ("demo-analyst", Role.ANALYST, "DEMO_ANALYST_PASSWORD"),
    ("demo-viewer", Role.VIEWER, "DEMO_VIEWER_PASSWORD"),
]


class SeedError(RuntimeError):
    """Missing or invalid seed configuration."""


def read_passwords(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Password per demo username, from env vars. Raises if any is missing."""
    missing = [var for _, _, var in DEMO_USERS if get_env(var, environ) is None]
    if missing:
        raise SeedError("missing env vars: " + ", ".join(missing))
    return {name: str(get_env(var, environ)) for name, _, var in DEMO_USERS}


def seed(
    session: Session, passwords: Mapping[str, str], *, reset_passwords: bool = False
) -> list[str]:
    """Create the demo tenant and users. Returns a list of human-readable actions."""
    actions: list[str] = []
    info = RequestInfo(request_id=f"seed-{uuid.uuid4()}")
    tenant = session.scalar(select(Tenant).where(Tenant.slug == DEMO_TENANT))
    if tenant is None:
        tenant = Tenant(id=uuid.uuid4(), slug=DEMO_TENANT, name="Demo Tenant (synthetic)")
        session.add(tenant)
        session.flush()
        audit(session, "tenant.create", request=info, tenant_id=tenant.id,
              resource_type="tenant", resource_id=str(tenant.id),
              details={"slug": DEMO_TENANT, "source": "seed_demo"})  # fmt: skip
        actions.append(f"created tenant {DEMO_TENANT}")
    for username, role, _ in DEMO_USERS:
        user = session.scalar(
            select(User).where(User.tenant_id == tenant.id, User.username == username)
        )
        if user is None:
            user = User(
                id=uuid.uuid4(), tenant_id=tenant.id, username=username,
                email=f"{username}@example.com", password_hash=hash_password(passwords[username]),
                role=role,
            )  # fmt: skip
            session.add(user)
            session.flush()
            audit(session, "user.create", request=info, tenant_id=tenant.id,
                  resource_type="user", resource_id=str(user.id),
                  details={"username": username, "role": role.value,
                           "source": "seed_demo"})  # fmt: skip
            actions.append(f"created {username} ({role.value})")
        elif reset_passwords:
            user.password_hash = hash_password(passwords[username])
            user.failed_logins = 0
            user.locked_until = None
            audit(session, "user.update", request=info, tenant_id=tenant.id,
                  resource_type="user", resource_id=str(user.id),
                  details={"password_reset": True, "source": "seed_demo"})  # fmt: skip
            actions.append(f"reset password for {username}")
        else:
            actions.append(f"kept existing {username}")
    return actions


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
    with session_scope(factory) as session:
        for line in seed(session, passwords, reset_passwords=args.reset_passwords):
            print(line)
    print(f"Log in with tenant '{DEMO_TENANT}' and the passwords from your env vars.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
