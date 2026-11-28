"""Load the synthetic clients into the demo tenant, and clear them for the nightly reset.

    uv run python -m claims_onboarding.synthetic seed      # DATABASE_URL (owner, locally)

What the seed loads (idempotent: existing rows are kept):

- ``data_dictionaries`` v1 from ``claims_v1.json`` as the tenant default (active);
- the 10 clients;
- each client's week-1 file **metadata and headers** (status ``received``). Contents are
  ingested in Phase 3.

It acts as ``system:seed``: a demo-tenant account that can never log in (inactive, and
its password hash is unusable), so every audit row names a clear, non-human actor.
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any

from ai_platform_auth import Role, TenantScope, audit
from ai_platform_auth.models import Tenant, User
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from claims_onboarding import service
from claims_onboarding.models import ClaimsFile, Client, DataDictionary, DictionaryStatus
from claims_onboarding.synthetic.dictionary import DICTIONARY_PATH
from claims_onboarding.synthetic.formats import read
from claims_onboarding.synthetic.generate import SYNTHETIC_DIR
from claims_onboarding.synthetic.profiles import CLIENTS

DEMO_TENANT = "demo"
SYSTEM_USERNAME = "system:seed"
UNUSABLE_PASSWORD_HASH = "!"  # noqa: S105 - not a secret: no password ever verifies against it

# Child tables first, so the composite foreign keys never block a delete.
_CLAIMS_TABLES = (
    "go_live_signoffs", "issue_letters", "rule_results", "mappings", "file_columns", "files",
    "rule_definitions", "data_dictionaries", "clients",
)  # fmt: skip


class SeedError(RuntimeError):
    """The demo tenant isn't there yet (run scripts/seed_demo.py first)."""


def system_user(session: Session, tenant: Tenant) -> User:
    """The non-login ``system:seed`` account, created if missing."""
    user = session.scalar(select(User).where(User.tenant_id == tenant.id,
                                             User.username == SYSTEM_USERNAME))  # fmt: skip
    if user is None:
        user = User(id=uuid.uuid4(), tenant_id=tenant.id, username=SYSTEM_USERNAME,
                    email="system-seed@example.invalid", password_hash=UNUSABLE_PASSWORD_HASH,
                    role=Role.ANALYST, is_active=False)  # fmt: skip
        session.add(user)
        session.flush()
    return user


def seed_demo_claims(session: Session, tenant_slug: str = DEMO_TENANT) -> dict[str, int]:
    """Load the dictionary, clients and week-1 file metadata. The caller commits."""
    tenant = session.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
    if tenant is None:
        raise SeedError(f"tenant {tenant_slug!r} not found: run scripts/seed_demo.py first")
    actor = system_user(session, tenant)
    scope = TenantScope(tenant.id)
    counts = {"dictionaries": 0, "clients": 0, "files": 0}

    dictionary = session.scalar(scope.apply(select(DataDictionary), DataDictionary).where(
        DataDictionary.client_id.is_(None), DataDictionary.version == 1))  # fmt: skip
    if dictionary is None:
        raw = json.loads(DICTIONARY_PATH.read_text(encoding="utf-8"))
        dictionary = DataDictionary(id=uuid.uuid4(), tenant_id=tenant.id, client_id=None,
                                    version=1, status=DictionaryStatus.ACTIVE,
                                    fields=raw["fields"], created_by=actor.id)  # fmt: skip
        session.add(dictionary)
        session.flush()
        audit(session, "claims.dictionary.loaded", tenant_id=tenant.id, user_id=actor.id,
              resource_type="claims_dictionary", resource_id=str(dictionary.id),
              details={"name": raw["name"], "version": 1,
                       "fields": len(raw["fields"])})  # fmt: skip
        counts["dictionaries"] += 1

    manifest: dict[str, Any] = json.loads((SYNTHETIC_DIR / "manifest.json").read_text("utf-8"))
    week1 = {e["client"]: e for e in manifest["files"] if e["week"] == 1}
    for profile in CLIENTS:
        client = session.scalar(scope.apply(select(Client), Client).where(
            Client.code == profile.code))  # fmt: skip
        if client is None:
            client = service.create_client(session, scope, actor, code=profile.code,
                                           name=f"{profile.name} (synthetic)")  # fmt: skip
            counts["clients"] += 1
        entry = week1[profile.number]
        exists = session.scalar(scope.apply(select(ClaimsFile.id), ClaimsFile).where(
            ClaimsFile.client_id == client.id, ClaimsFile.sha256 == entry["sha256"]))  # fmt: skip
        if exists is not None:
            continue
        headers, _rows = read(profile.fmt, (SYNTHETIC_DIR / entry["path"]).read_bytes())
        file = service.register_file(
            session, scope, actor, client.id, filename=entry["path"].rsplit("/", 1)[1],
            sha256=entry["sha256"], size_bytes=entry["bytes"], headers=headers,
            row_count=entry["rows"],
        )  # fmt: skip
        file.dictionary_id = dictionary.id
        session.flush()
        counts["files"] += 1
    return counts


def clear_demo_claims(session: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    """Delete all of a tenant's claims rows (owner role; nightly demo reset only).

    Returns rows deleted per table and writes a ``claims.demo.cleared`` audit row. Does
    nothing if the claims schema isn't migrated on this database yet.
    """
    if session.scalar(text("SELECT to_regclass('claims.clients')")) is None:
        return {}
    t = {"t": tenant_id}
    # resubmission links point within the same table; drop them first
    session.execute(text("UPDATE claims.files SET previous_file_id = NULL "
                         "WHERE tenant_id = :t AND previous_file_id IS NOT NULL"), t)  # fmt: skip
    counts: dict[str, int] = {}
    for table in _CLAIMS_TABLES:
        result = session.execute(text(f"DELETE FROM claims.{table} WHERE tenant_id = :t"), t)  # noqa: S608 - fixed table names
        counts[table] = int(result.rowcount or 0)  # type: ignore[attr-defined]
    audit(session, "claims.demo.cleared", tenant_id=tenant_id, user_id=None,
          resource_type="tenant", resource_id=str(tenant_id), details=dict(counts))  # fmt: skip
    return counts


def main() -> int:
    from ai_platform_common.context import request_context
    from ai_platform_common.db import database_url, get_engine, make_session_factory, session_scope

    engine = get_engine(database_url())
    try:
        factory = make_session_factory(engine)
        with request_context(f"claims-seed-{uuid.uuid4()}"), session_scope(factory) as session:
            counts = seed_demo_claims(session)
    except SeedError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    finally:
        engine.dispose()
    sys.stdout.write("claims seed: " + ", ".join(f"{k}={v}" for k, v in counts.items()) + "\n")
    return 0
