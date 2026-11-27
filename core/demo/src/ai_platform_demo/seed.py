"""The demo tenant and its users (shared by ``scripts/seed_demo.py`` and the nightly reset).

Passwords come only from env vars: DEMO_ADMIN_PASSWORD, DEMO_ANALYST_PASSWORD,
DEMO_VIEWER_PASSWORD and DEMO_REVIEWER_PASSWORD. The admin's is used only by CI jobs that
seed or reset; the hosted app never gets it (ADR 0008).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from ai_platform_auth.audit import audit
from ai_platform_auth.models import Role, Tenant, User
from ai_platform_auth.passwords import hash_password
from ai_platform_common.config import get_env
from sqlalchemy import select
from sqlalchemy.orm import Session

DEMO_TENANT = "demo"
DEMO_USERS: list[tuple[str, Role, str]] = [
    ("demo-admin", Role.ADMIN, "DEMO_ADMIN_PASSWORD"),
    ("demo-analyst", Role.ANALYST, "DEMO_ANALYST_PASSWORD"),
    ("demo-viewer", Role.VIEWER, "DEMO_VIEWER_PASSWORD"),
    # A second analyst, so every approval request has at least two people who may decide
    # it (nobody may approve their own request).
    ("demo-reviewer", Role.ANALYST, "DEMO_REVIEWER_PASSWORD"),
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
    tenant = session.scalar(select(Tenant).where(Tenant.slug == DEMO_TENANT))
    if tenant is None:
        tenant = Tenant(id=uuid.uuid4(), slug=DEMO_TENANT, name="Demo Tenant (synthetic)")
        session.add(tenant)
        session.flush()
        audit(session, "tenant.create", tenant_id=tenant.id,
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
            audit(session, "user.create", tenant_id=tenant.id,
                  resource_type="user", resource_id=str(user.id),
                  details={"username": username, "role": role.value,
                           "source": "seed_demo"})  # fmt: skip
            actions.append(f"created {username} ({role.value})")
        elif reset_passwords:
            # back to the seeded state: password, role, active, unlocked
            user.password_hash = hash_password(passwords[username])
            user.role = role
            user.is_active = True
            user.is_superadmin = False
            user.failed_logins = 0
            user.locked_until = None
            audit(session, "user.update", tenant_id=tenant.id,
                  resource_type="user", resource_id=str(user.id),
                  details={"password_reset": True, "source": "seed_demo"})  # fmt: skip
            actions.append(f"reset password for {username}")
        else:
            actions.append(f"kept existing {username}")
    return actions
