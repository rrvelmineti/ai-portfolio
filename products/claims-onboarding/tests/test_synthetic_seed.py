"""The claims demo seed, the nightly reset's claims part, and the app role's claims grants."""

from __future__ import annotations

import importlib.util
import json
import secrets
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from ai_platform_auth import TenantScope
from ai_platform_auth.models import AuditLog, Tenant, User
from ai_platform_auth.passwords import verify_password
from ai_platform_common.testing import temporary_database
from ai_platform_demo.roles import BootstrapError, bootstrap_app_role, migrate_all
from claims_onboarding import service
from claims_onboarding.db import CLAIMS_GRANTS
from claims_onboarding.db import migrate as migrate_claims
from claims_onboarding.models import ClaimsFile, Client, DataDictionary, FileColumn, FileStatus
from claims_onboarding.synthetic.generate import SYNTHETIC_DIR
from claims_onboarding.synthetic.seed import (
    SYSTEM_USERNAME,
    SeedError,
    clear_demo_claims,
    seed_demo_claims,
    system_user,
)
from claims_onboarding.synthetic.seed import main as seed_main
from sqlalchemy import URL, create_engine, func, select, text
from sqlalchemy.orm import Session

from .conftest import Org

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = json.loads((SYNTHETIC_DIR / "manifest.json").read_text("utf-8"))
WEEK1 = {e["code"]: e for e in MANIFEST["files"] if e["week"] == 1}
PASSWORDS = {
    "DEMO_ADMIN_PASSWORD": "demo-admin-passphrase",
    "DEMO_ANALYST_PASSWORD": "demo-analyst-passphrase",
    "DEMO_VIEWER_PASSWORD": "demo-viewer-passphrase",
    "DEMO_REVIEWER_PASSWORD": "demo-reviewer-passphrase",
}


@pytest.fixture
def demo(session: Session) -> Tenant:
    tenant = Tenant(id=uuid.uuid4(), slug="demo", name="Demo Tenant (synthetic)")
    session.add(tenant)
    session.flush()
    return tenant


def _count(session: Session, model: Any, tenant_id: uuid.UUID) -> int:  # noqa: ANN401
    return int(
        session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id))
        or 0
    )


def test_seed_loads_the_dictionary_clients_and_week1_files(session: Session, demo: Tenant) -> None:
    assert seed_demo_claims(session) == {"dictionaries": 1, "clients": 10, "files": 10}
    dictionary = session.scalar(select(DataDictionary).where(DataDictionary.tenant_id == demo.id))
    assert dictionary is not None and dictionary.client_id is None
    assert (dictionary.version, dictionary.status, len(dictionary.fields)) == (1, "active", 13)
    clients = {
        c.code: c for c in session.scalars(select(Client).where(Client.tenant_id == demo.id))
    }
    assert set(clients) == set(WEEK1)
    for code, entry in WEEK1.items():
        file = session.scalar(select(ClaimsFile).where(ClaimsFile.client_id == clients[code].id))
        assert file is not None
        assert (file.status, file.sha256, file.row_count, file.size_bytes) == (
            FileStatus.RECEIVED,
            entry["sha256"],
            entry["rows"],
            entry["bytes"],
        )
        assert file.dictionary_id == dictionary.id
        headers = list(
            session.scalars(
                select(FileColumn.header)
                .where(FileColumn.file_id == file.id)
                .order_by(FileColumn.position)
            )
        )
        key = json.loads((SYNTHETIC_DIR / entry["mapping_key"]["path"]).read_text("utf-8"))
        assert headers == [c["header"] for c in key["columns"]]

    actor = session.scalar(
        select(User).where(User.tenant_id == demo.id, User.username == SYSTEM_USERNAME)
    )
    assert actor is not None and actor.is_active is False
    assert verify_password("anything at all", actor.password_hash) == (False, None)
    actors = set(
        session.scalars(
            select(AuditLog.user_id).where(
                AuditLog.tenant_id == demo.id, AuditLog.action.like("claims.%")
            )
        )
    )
    assert actors == {actor.id}

    audit_rows = _count(session, AuditLog, demo.id)
    assert seed_demo_claims(session) == {"dictionaries": 0, "clients": 0, "files": 0}
    assert _count(session, AuditLog, demo.id) == audit_rows  # idempotent and quiet
    assert system_user(session, demo).id == actor.id


def test_seed_needs_the_demo_tenant(
    session: Session, db_url: URL, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    with pytest.raises(SeedError, match="seed_demo.py first"):
        seed_demo_claims(session)
    monkeypatch.setenv("DATABASE_URL", db_url.render_as_string(hide_password=False))
    assert seed_main() == 2
    assert "tenant 'demo' not found" in capsys.readouterr().err


def test_clear_removes_every_claims_row_of_that_tenant_only(
    session: Session, demo: Tenant, org_b: Org
) -> None:
    seed_demo_claims(session)
    scope = TenantScope(demo.id)
    actor = system_user(session, demo)
    # a visitor's own client, a mapping decision and a resubmission chain
    visitor = service.create_client(session, scope, actor, code="VISITOR", name="Visitor plan")
    first = service.register_file(
        session,
        scope,
        actor,
        visitor.id,
        filename="v.csv",
        sha256="a" * 64,
        size_bytes=1,
        headers=["Claim"],
    )
    service.transition(session, scope, actor, first.id, FileStatus.MASKED)
    column = service.file_columns(session, scope, first.id)[0]
    m = service.suggest_mapping(
        session, scope, actor, first.id, file_column_id=column.id, target_field="claim_id"
    )
    service.decide_mapping(session, scope, actor, m.id, approve=True)
    first.status = FileStatus.ISSUES_SENT  # (state shortcut: only the link matters here)
    service.register_file(
        session,
        scope,
        actor,
        visitor.id,
        filename="v2.csv",
        sha256="b" * 64,
        size_bytes=1,
        headers=["Claim"],
        previous_file_id=first.id,
    )
    other = service.create_client(session, org_b.scope, org_b.analyst, code="KEEP", name="Kept")

    counts = clear_demo_claims(session, demo.id)
    assert counts["clients"] == 11 and counts["files"] == 12 and counts["mappings"] == 1
    assert counts["data_dictionaries"] == 1
    for table in ("clients", "files", "file_columns", "mappings", "data_dictionaries"):
        n = session.scalar(
            text(f"SELECT count(*) FROM claims.{table} WHERE tenant_id = :t"),  # noqa: S608
            {"t": demo.id},
        )
        assert n == 0, table
    assert session.get(Client, other.id) is not None  # other tenants are untouched
    cleared = session.scalar(
        select(AuditLog.details).where(
            AuditLog.action == "claims.demo.cleared", AuditLog.tenant_id == demo.id
        )
    )
    assert cleared is not None and cleared["clients"] == 11
    assert seed_demo_claims(session) == {"dictionaries": 1, "clients": 10, "files": 10}


def _script(name: str) -> Any:  # noqa: ANN401 - a module loaded from a file path
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def owner_db(admin_url: URL) -> Iterator[URL]:
    """A fresh database with every platform migration and the claims schema (owner)."""
    with temporary_database(admin_url, [migrate_all, migrate_claims], prefix="claims_ops") as url:
        yield url


def test_nightly_reset_clears_visitor_claims_and_reseeds(
    owner_db: URL, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reset = _script("reset_demo")
    monkeypatch.setenv("OWNER_DATABASE_URL", owner_db.render_as_string(hide_password=False))
    for name, value in PASSWORDS.items():
        monkeypatch.setenv(name, value)
    assert reset.main([]) == 0  # first night: creates the demo tenant and seeds claims
    assert "claims_seeded_clients=10" in capsys.readouterr().out
    engine = create_engine(owner_db)
    with engine.begin() as conn:
        system_id = conn.scalar(
            text("SELECT id FROM auth.users WHERE username = :u"), {"u": SYSTEM_USERNAME}
        )
        tenant_id = conn.scalar(text("SELECT id FROM auth.tenants WHERE slug = 'demo'"))
        conn.execute(
            text(
                "INSERT INTO claims.clients (id, tenant_id, code, name, status) "
                "VALUES (:i, :t, 'VISITOR', 'Visitor plan', 'onboarding')"
            ),
            {"i": uuid.uuid4(), "t": tenant_id},
        )
    assert reset.main([]) == 0
    out = capsys.readouterr().out
    assert "claims_rows_cleared=" in out and "claims_seeded_clients=10" in out
    with engine.connect() as conn:
        codes = set(conn.scalars(text("SELECT code FROM claims.clients")))
        assert codes == set(WEEK1)  # the visitor's client is gone
        same_system = conn.scalar(
            text("SELECT id FROM auth.users WHERE username = :u"), {"u": SYSTEM_USERNAME}
        )
        assert same_system == system_id  # kept by the reset, not recreated
    engine.dispose()


def test_app_role_gets_exactly_the_claims_grants(owner_db: URL, admin_url: URL) -> None:
    role = f"app_test_{uuid.uuid4().hex[:10]}"
    try:
        bootstrap_app_role(
            owner_db, secrets.token_urlsafe(24), role=role, extra_grants=CLAIMS_GRANTS
        )
        engine = create_engine(owner_db)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT table_schema || '.' || table_name, privilege_type "
                    "FROM information_schema.role_table_grants "
                    "WHERE grantee = :r AND table_schema = 'claims'"
                ),
                {"r": role},
            ).all()
            granted: dict[str, set[str]] = {}
            for table, privilege in rows:
                granted.setdefault(table, set()).add(privilege)
            assert granted == {t: set(p) for t, p in CLAIMS_GRANTS.items()}
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE claims.undecided (id int)"))
        with pytest.raises(BootstrapError, match="claims.undecided"):
            bootstrap_app_role(
                owner_db, secrets.token_urlsafe(24), role=role, extra_grants=CLAIMS_GRANTS
            )
        with engine.begin() as conn:
            conn.execute(text(f'DROP OWNED BY "{role}"'))
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()
