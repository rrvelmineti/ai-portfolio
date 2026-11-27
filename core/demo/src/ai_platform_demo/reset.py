"""Nightly reset of the public demo (ADR 0008), run by CI with the **owner** role.

One visitor's changes must not persist for the next, so every night (in one transaction):

1. approval requests and AI-call records of the demo tenant are deleted;
2. refresh tokens of demo users are deleted (everyone logs in again);
3. users in the demo tenant that the seed doesn't define are deleted, and the seeded
   users are restored: password from secrets, original role, active, unlocked;
4. daily AI-usage counters from *earlier* days are deleted. Today's and the monthly
   counters are kept, so a reset never gives back AI calls (the caps protect cost);
5. audit retention: demo-tenant audit rows older than 7 days are deleted. Only this
   owner job can do that (``SET LOCAL ai_portfolio.audit_retention = 'on'``; auth
   migration 0002); the app role can never update or delete audit rows;
6. a ``demo.reset`` audit row records what was removed.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from ai_platform_auth.audit import audit
from ai_platform_auth.models import Tenant
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ai_platform_demo.seed import DEMO_TENANT, DEMO_USERS, seed

AUDIT_RETENTION_DAYS = 7  # enforced by the audit trigger as well


def reset_demo(
    session: Session, passwords: Mapping[str, str], now: datetime | None = None
) -> dict[str, int]:
    """Reset the demo tenant to its seeded state. Returns row counts (logged and audited).

    The caller commits. Needs the owner role (the app role has no DELETE anywhere).
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    seed(session, passwords)  # creates the tenant and users if missing
    tenant = session.scalar(select(Tenant).where(Tenant.slug == DEMO_TENANT))
    if tenant is None:  # pragma: no cover - seed just created it
        raise RuntimeError("demo tenant missing after seeding")
    t = {"t": tenant.id}
    seeded = [name for name, _, _ in DEMO_USERS]

    def run(sql: str, **params: object) -> int:
        result = session.execute(text(sql), {**t, **params})
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    counts = {
        "approvals": run("DELETE FROM guardrails.approval_requests WHERE tenant_id = :t"),
        "llm_calls": run("DELETE FROM observability.llm_calls WHERE tenant_id = :t"),
        "refresh_tokens": run(
            "DELETE FROM auth.refresh_tokens WHERE user_id IN "
            "(SELECT id FROM auth.users WHERE tenant_id = :t)"
        ),
        "extra_users": run(
            "DELETE FROM auth.users WHERE tenant_id = :t AND NOT (username = ANY(:names))",
            names=seeded,
        ),
        "old_daily_usage": run(
            "DELETE FROM demo.ai_usage WHERE period LIKE 'day:%' AND period < :today",
            today=f"day:{now:%Y-%m-%d}",
        ),
    }
    seed(session, passwords, reset_passwords=True)  # restore password, role, lock state
    session.execute(text("SET LOCAL ai_portfolio.audit_retention = 'on'"))
    counts["audit_rows_pruned"] = run(
        "DELETE FROM auth.audit_log WHERE tenant_id = :t "
        "AND created_at < now() - make_interval(days => :days)",
        days=AUDIT_RETENTION_DAYS,
    )
    session.execute(text("SET LOCAL ai_portfolio.audit_retention = 'off'"))
    audit(session, "demo.reset", tenant_id=tenant.id, user_id=None,
          resource_type="tenant", resource_id=str(tenant.id), details=dict(counts))  # fmt: skip
    return counts
