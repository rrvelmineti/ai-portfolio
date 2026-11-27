"""Approval gate: role checks, four-eyes, expiry, tenant isolation, audit rows.

No ``from __future__ import annotations``: the test app uses FastAPI dependencies.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from ai_platform_auth import Auth, AuthSettings, Role
from ai_platform_auth.models import AuditLog, Tenant, User
from ai_platform_auth.passwords import hash_password
from ai_platform_common.context import request_context, set_user
from ai_platform_gateway import set_guard
from ai_platform_guardrails import ApprovalService, ApprovalStatus, install_guardrails
from ai_platform_guardrails.approvals.models import ApprovalRequest
from ai_platform_observability.redaction import set_content_redactor
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

SECRET = b"approvals-test-jwt-secret-" + b"k" * 32
PASSWORD = "synthetic-pass-phrase-1"


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(autouse=True)
def _reset_guard() -> Iterator[None]:
    yield
    set_guard(None)
    set_content_redactor(None)


@pytest.fixture
def db(committed_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with committed_session_factory() as s:
        yield s


@pytest.fixture
def people(db: Session) -> dict[str, User]:
    slug = f"appr-{uuid.uuid4().hex[:8]}"
    t = Tenant(slug=slug, name="Approvals Co")
    other = Tenant(slug=f"{slug}-x", name="Other Co")
    db.add_all([t, other])
    db.flush()
    pw = hash_password(PASSWORD)
    users = {
        "admin": User(tenant_id=t.id, username="ada", email="ada@example.com",
                      password_hash=pw, role=Role.ADMIN),
        "analyst": User(tenant_id=t.id, username="ana", email="ana@example.com",
                        password_hash=pw, role=Role.ANALYST),
        "analyst2": User(tenant_id=t.id, username="ari", email="ari@example.com",
                         password_hash=pw, role=Role.ANALYST),
        "viewer": User(tenant_id=t.id, username="vic", email="vic@example.com",
                       password_hash=pw, role=Role.VIEWER),
        "outsider": User(tenant_id=other.id, username="otto", email="otto@example.com",
                         password_hash=pw, role=Role.ADMIN),
    }  # fmt: skip
    db.add_all(users.values())
    db.commit()
    return users


def request(
    svc: ApprovalService, db: Session, user: User, summary: str, rid: str
) -> ApprovalRequest:
    with request_context(rid):
        set_user(user.tenant_id, user.id)
        return svc.require_approval(db, "issue_refund", summary)


def test_require_approval_masks_summary_and_audits(db: Session, people: dict[str, User]) -> None:
    svc = ApprovalService()
    rid = f"req-{uuid.uuid4().hex[:8]}"
    req = request(svc, db, people["viewer"], "Refund $120 to member Ana Ruiz, SSN 536-22-8471", rid)
    assert req.status == ApprovalStatus.PENDING and req.request_id == rid
    assert "Ana Ruiz" not in req.payload_summary and "536-22-8471" not in req.payload_summary
    assert req.expires_at - req.created_at == timedelta(days=7)
    row = db.scalar(select(AuditLog).where(AuditLog.request_id == rid))
    assert row is not None and row.action == "approval.requested"
    assert row.details == {"action": "issue_refund"}
    with pytest.raises(Exception, match="authenticated"):
        svc.require_approval(db, "x", "y")  # no user in context


def test_roles_four_eyes_and_audit(db: Session, people: dict[str, User]) -> None:
    from ai_platform_auth.errors import Conflict, NotFound, PermissionDenied

    svc = ApprovalService()
    req = request(svc, db, people["analyst"], "Issue refund", "req-4eyes")
    with pytest.raises(PermissionDenied, match="own request"):
        svc.decide(db, people["analyst"], req.id, approve=True)
    with pytest.raises(PermissionDenied, match="analyst"):
        svc.decide(db, people["viewer"], req.id, approve=True)
    with pytest.raises(NotFound):
        svc.decide(db, people["outsider"], req.id, approve=True)  # other tenant
    with pytest.raises(Conflict, match="reason"):
        svc.decide(db, people["analyst2"], req.id, approve=False)
    done = svc.decide(db, people["analyst2"], req.id, approve=True, reason="checked receipt")
    assert done.status == ApprovalStatus.APPROVED and done.decided_by == people["analyst2"].id
    assert svc.is_approved(db, req.id) and not svc.is_approved(db, uuid.uuid4())
    with pytest.raises(Conflict, match="already approved"):
        svc.decide(db, people["admin"], req.id, approve=False, reason="late")
    actions = [
        a.action
        for a in db.scalars(
            select(AuditLog).where(AuditLog.resource_id == str(req.id)).order_by(AuditLog.id)
        )
    ]
    assert actions == ["approval.requested", "approval.approved"]


def test_visibility_by_role(db: Session, people: dict[str, User]) -> None:
    svc = ApprovalService()
    by_viewer = request(svc, db, people["viewer"], "viewer asks", "r1")
    by_admin = request(svc, db, people["admin"], "admin asks", "r2")
    svc.decide(db, people["analyst"], by_admin.id, approve=False, reason="no")

    def ids(who: str) -> set[uuid.UUID]:
        return {r.id for r in svc.list_for(db, people[who])}

    assert ids("viewer") == {by_viewer.id}  # own only
    assert ids("analyst2") == {by_viewer.id}  # pending in tenant (admin's is rejected)
    assert ids("admin") >= {by_viewer.id, by_admin.id}  # everything in tenant
    assert ids("outsider") == set()
    rejected = svc.list_for(db, people["admin"], ApprovalStatus.REJECTED)
    assert by_admin.id in {r.id for r in rejected}
    from ai_platform_auth.errors import NotFound

    with pytest.raises(NotFound):
        svc.get_for(db, people["viewer"], by_admin.id)
    assert svc.get_for(db, people["admin"], by_admin.id).id == by_admin.id


def test_expiry(db: Session, people: dict[str, User]) -> None:
    from ai_platform_auth.errors import Conflict

    clock = Clock()
    svc = ApprovalService(ttl=timedelta(hours=1), clock=clock)
    req = request(svc, db, people["viewer"], "expiring", "r-exp")
    clock.now += timedelta(hours=2)
    with pytest.raises(Conflict, match="expired"):
        svc.decide(db, people["admin"], req.id, approve=True)
    db.refresh(req)
    assert req.status == ApprovalStatus.EXPIRED
    assert db.scalar(select(AuditLog).where(AuditLog.action == "approval.expired",
                                            AuditLog.resource_id == str(req.id)))  # fmt: skip


def test_router_end_to_end(
    committed_session_factory: sessionmaker[Session], people: dict[str, User], db: Session
) -> None:
    app = FastAPI()
    auth = Auth(AuthSettings(jwt_secret=SECRET), committed_session_factory)
    auth.install(app)
    g = install_guardrails(app, auth, committed_session_factory)
    assert g.policy.blocked_topics == []
    slug = db.get(Tenant, people["viewer"].tenant_id).slug  # type: ignore[union-attr]

    with TestClient(app) as c:

        def token(user: str) -> dict[str, str]:
            r = c.post("/auth/login", data={"username": f"{slug}/{user}", "password": PASSWORD})
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        vic, ana, ada = token("vic"), token("ana"), token("ada")
        body = {"action": "issue_refund", "payload_summary": "Refund member Ana Ruiz"}
        r = c.post("/approvals", headers={**vic, "X-Request-ID": "req-e2e-appr"}, json=body)
        assert r.status_code == 201 and "Ana Ruiz" not in r.json()["payload_summary"]
        aid = r.json()["id"]
        assert c.post(f"/approvals/{aid}/approve", headers=vic, json={}).status_code == 403
        assert [x["id"] for x in c.get("/approvals", headers=ana).json()] == [aid]
        assert c.get(f"/approvals/{aid}", headers=vic).status_code == 200
        r = c.post(f"/approvals/{aid}/reject", headers=ana, json={})
        assert r.status_code == 409  # reason required
        r = c.post(f"/approvals/{aid}/approve", headers=ana, json={"reason": "ok"})
        assert r.status_code == 200 and r.json()["status"] == "approved"
        assert c.get("/approvals?status=approved", headers=ada).json()[0]["id"] == aid
        assert c.get(f"/approvals/{uuid.uuid4()}", headers=ada).status_code == 404
    row = db.scalar(select(AuditLog).where(AuditLog.request_id == "req-e2e-appr"))
    assert row is not None and row.action == "approval.requested"
