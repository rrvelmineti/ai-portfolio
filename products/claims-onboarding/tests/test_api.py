"""The claims onboarding app over HTTP: roles, four-eyes, isolation, audit, wiring."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from ai_platform_auth.models import AuditLog, User
from claims_onboarding import service
from claims_onboarding.app import create_app
from claims_onboarding.models import RuleKind, Severity
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .conftest import JWT_SECRET, PASSWORD, Org

SHA = hashlib.sha256(b"synthetic claims file").hexdigest()
FILE = {
    "filename": "northwind_jan.csv",
    "sha256": SHA,
    "size_bytes": 2048,
    "row_count": 300,
    "headers": ["CLM_NBR", "MBR_ID", "SVC_DT"],
}


@pytest.fixture(autouse=True)
def _restore_platform_globals() -> Iterator[None]:
    """create_app registers process-wide gateway hooks and guards; undo them."""
    from ai_platform_gateway import hooks, set_guard
    from ai_platform_observability.recorder import unregister_recorders
    from ai_platform_observability.redaction import set_content_redactor

    saved = list(hooks.on_call_complete)
    yield
    unregister_recorders()
    hooks.on_call_complete[:] = saved
    set_guard(None)
    set_content_redactor(None)


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    session: Session,
    org_a: Org,
    org_b: Org,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    session.commit()  # the orgs' users must be visible to the app's sessions
    with TestClient(create_app(session_factory)) as c:
        yield c


def h(client: TestClient, org: Org, user: User) -> dict[str, str]:
    r = client.post(
        "/auth/login", data={"username": f"{org.tenant.slug}/{user.username}", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _new_file(
    client: TestClient, headers: dict[str, str], code: str = "HP-NORTH"
) -> dict[str, Any]:
    r = client.post(
        "/clients", headers=headers, json={"code": code, "name": "Northwind (synthetic)"}
    )
    assert r.status_code == 201, r.text
    r = client.post(f"/clients/{r.json()['id']}/files", headers=headers, json=FILE)
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


def _move(client: TestClient, headers: dict[str, str], file_id: str, *targets: str) -> None:
    for target in targets:
        r = client.post(f"/files/{file_id}/transitions", headers=headers, json={"to": target})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == target


def test_viewer_is_read_only(client: TestClient, org_a: Org) -> None:
    analyst, viewer = h(client, org_a, org_a.analyst), h(client, org_a, org_a.viewer)
    file = _new_file(client, analyst)
    fid, column = file["id"], file["columns"][0]["id"]
    assert client.get("/clients", headers=viewer).status_code == 200
    assert client.get(f"/clients/{file['client_id']}", headers=viewer).status_code == 200
    got = client.get(f"/files/{fid}", headers=viewer)
    assert (
        got.status_code == 200 and [c["header"] for c in got.json()["columns"]] == FILE["headers"]
    )
    writes = [
        client.post("/clients", headers=viewer, json={"code": "X", "name": "X"}),
        client.post(f"/clients/{file['client_id']}/files", headers=viewer, json=FILE),
        client.post(f"/files/{fid}/transitions", headers=viewer, json={"to": "masked"}),
        client.post(
            f"/files/{fid}/mappings",
            headers=viewer,
            json={"file_column_id": column, "target_field": "claim_id"},
        ),
        client.post(f"/files/{fid}/signoff", headers=viewer, json={"approve": True}),
    ]
    assert [r.status_code for r in writes] == [403] * len(writes)
    assert client.get("/clients").status_code == 401  # and nothing without a token


def test_end_to_end_with_four_eyes(client: TestClient, session: Session, org_a: Org) -> None:
    analyst, manager = h(client, org_a, org_a.analyst), h(client, org_a, org_a.manager)
    manager2 = h(client, org_a, org_a.manager2)
    file = _new_file(client, analyst)
    fid = file["id"]
    _move(client, analyst, fid, "masked", "mapped")
    for column, field in zip(
        file["columns"], ["claim_id", "member_id", "service_date"], strict=True
    ):
        r = client.post(
            f"/files/{fid}/mappings",
            headers=analyst,
            json={
                "file_column_id": column["id"],
                "target_field": field,
                "confidence": "0.87",
                "model": "heuristic",
            },
        )
        assert r.status_code == 201, r.text
        # the manager approves the last one, so the manager can't sign off later
        approver = manager if field == "service_date" else analyst
        d = client.post(
            f"/mappings/{r.json()['id']}/decision", headers=approver, json={"approve": True}
        )
        assert d.status_code == 200 and d.json()["status"] == "approved"
    _move(client, analyst, fid, "under_review", "rules_run")

    # Phase 6 runs the rules; here the result is recorded directly
    rule = service.add_rule_definition(
        session,
        org_a.scope,
        org_a.analyst,
        rule_key="npi",
        target_field="provider_npi",
        rule_text="exactly 10 digits",
        severity=Severity.BLOCKER,
        kind=RuleKind.BUILT_IN,
    )
    service.record_rule_result(
        session,
        org_a.scope,
        org_a.analyst,
        uuid.UUID(fid),
        rule_definition_id=rule.id,
        failed_rows=0,
    )
    session.commit()
    _move(client, analyst, fid, "ready_for_signoff")

    analyst_signoff = client.post(f"/files/{fid}/signoff", headers=analyst, json={"approve": True})
    assert analyst_signoff.status_code == 403  # managers only
    own = client.post(f"/files/{fid}/signoff", headers=manager, json={"approve": True})
    assert own.status_code == 403 and "four-eyes" in own.json()["detail"]
    ok = client.post(
        f"/files/{fid}/signoff", headers=manager2, json={"approve": True, "reason": "Looks good"}
    )
    assert ok.status_code == 201 and ok.json()["decision"] == "approved"
    assert client.get(f"/files/{fid}", headers=analyst).json()["status"] == "live"
    assert client.get(f"/clients/{file['client_id']}", headers=analyst).json()["status"] == "live"


def test_rejected_signoff_over_http(client: TestClient, session: Session, org_a: Org) -> None:
    analyst, manager = h(client, org_a, org_a.analyst), h(client, org_a, org_a.manager)
    file = _new_file(client, analyst)
    fid = file["id"]
    _move(client, analyst, fid, "masked", "mapped")
    column = file["columns"][0]["id"]
    m = client.post(
        f"/files/{fid}/mappings",
        headers=analyst,
        json={"file_column_id": column, "target_field": "claim_id"},
    ).json()
    client.post(f"/mappings/{m['id']}/decision", headers=analyst, json={"approve": True})
    _move(client, analyst, fid, "under_review", "rules_run")
    rule = service.add_rule_definition(
        session,
        org_a.scope,
        org_a.analyst,
        rule_key="dob",
        target_field="member_dob",
        rule_text="not in the future",
        severity=Severity.WARNING,
    )
    service.record_rule_result(
        session,
        org_a.scope,
        org_a.analyst,
        uuid.UUID(fid),
        rule_definition_id=rule.id,
        failed_rows=2,
    )
    session.commit()
    _move(client, analyst, fid, "ready_for_signoff")
    url = f"/files/{fid}/signoff"
    assert (
        client.post(
            url, headers=manager, json={"approve": False, "return_to": "issues_sent"}
        ).status_code
        == 409
    )  # no reason
    assert (
        client.post(
            url, headers=manager, json={"approve": False, "reason": "why", "return_to": "live"}
        ).status_code
        == 422
    )  # not a rejection target
    plain = client.post(
        f"/files/{fid}/transitions", headers=analyst, json={"to": "issues_sent", "reason": "skip"}
    )
    assert plain.status_code == 409 and "sign-off" in plain.json()["detail"]
    r = client.post(
        url,
        headers=manager,
        json={
            "approve": False,
            "return_to": "issues_sent",
            "reason": "Dates of birth after service dates",
        },
    )
    assert r.status_code == 201
    assert (r.json()["decision"], r.json()["returned_to"]) == ("rejected", "issues_sent")
    assert client.get(f"/files/{fid}", headers=analyst).json()["status"] == "issues_sent"


def test_status_errors_are_409_and_send_back_needs_a_reason(client: TestClient, org_a: Org) -> None:
    analyst = h(client, org_a, org_a.analyst)
    fid = _new_file(client, analyst)["id"]
    skip = client.post(f"/files/{fid}/transitions", headers=analyst, json={"to": "rules_run"})
    assert skip.status_code == 409 and "from received to rules_run" in skip.json()["detail"]
    live = client.post(f"/files/{fid}/transitions", headers=analyst, json={"to": "live"})
    assert live.status_code == 409 and "sign-off" in live.json()["detail"]
    bogus = client.post(f"/files/{fid}/transitions", headers=analyst, json={"to": "approved"})
    assert bogus.status_code == 422
    _move(client, analyst, fid, "masked", "mapped", "under_review")
    no_reason = client.post(f"/files/{fid}/transitions", headers=analyst, json={"to": "mapped"})
    assert no_reason.status_code == 409 and "needs a reason" in no_reason.json()["detail"]
    back = client.post(
        f"/files/{fid}/transitions",
        headers=analyst,
        json={"to": "mapped", "reason": "MBR_ID may be a subscriber ID"},
    )
    assert back.status_code == 200 and back.json()["status"] == "mapped"
    guard = client.post(f"/files/{fid}/transitions", headers=analyst, json={"to": "under_review"})
    assert guard.status_code == 200
    blocked = client.post(f"/files/{fid}/transitions", headers=analyst, json={"to": "rules_run"})
    assert blocked.status_code == 409 and "no approved mappings" in blocked.json()["detail"]


def test_input_validation(client: TestClient, org_a: Org) -> None:
    analyst = h(client, org_a, org_a.analyst)
    r = client.post("/clients", headers=analyst, json={"code": "HP NORTH", "name": "x"})
    assert r.status_code == 422  # no spaces in codes
    cid = client.post("/clients", headers=analyst, json={"code": "HP-1", "name": "x"}).json()["id"]
    assert (
        client.post("/clients", headers=analyst, json={"code": "HP-1", "name": "again"}).status_code
        == 409
    )
    bad_inputs: list[dict[str, Any]] = [
        {"sha256": "abc"},
        {"headers": []},
        {"headers": ["ok", "  "]},
        {"size_bytes": -1},
    ]
    for bad in bad_inputs:
        r = client.post(f"/clients/{cid}/files", headers=analyst, json={**FILE, **bad})
        assert r.status_code == 422, bad


def test_other_tenants_get_404_over_http(client: TestClient, org_a: Org, org_b: Org) -> None:
    a_analyst = h(client, org_a, org_a.analyst)
    file = _new_file(client, a_analyst)
    fid, cid = file["id"], file["client_id"]
    b_analyst, b_manager = h(client, org_b, org_b.analyst), h(client, org_b, org_b.manager)
    assert client.get("/clients", headers=b_analyst).json() == []
    attempts = [
        client.get(f"/clients/{cid}", headers=b_analyst),
        client.get(f"/files/{fid}", headers=b_analyst),
        client.post(f"/clients/{cid}/files", headers=b_analyst, json=FILE),
        client.post(f"/files/{fid}/transitions", headers=b_analyst, json={"to": "masked"}),
        client.post(
            f"/files/{fid}/mappings",
            headers=b_analyst,
            json={"file_column_id": file["columns"][0]["id"], "target_field": "claim_id"},
        ),
        client.post(f"/files/{fid}/signoff", headers=b_manager, json={"approve": True}),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts)
    assert client.get(f"/files/{fid}", headers=a_analyst).json()["status"] == "received"


def test_each_write_has_one_claims_audit_row(
    client: TestClient, session: Session, org_a: Org
) -> None:
    analyst = h(client, org_a, org_a.analyst)
    r = client.post(
        "/clients",
        headers={**analyst, "X-Request-ID": "audit-create-client"},
        json={"code": "HP-AUD", "name": "Audit (synthetic)"},
    )
    assert r.status_code == 201
    rows = session.execute(
        select(AuditLog.action, AuditLog.user_id, AuditLog.tenant_id).where(
            AuditLog.request_id == "audit-create-client"
        )
    ).all()
    # the claims row only: the platform's generic http.post row is skipped once audited
    assert rows == [("claims.client.created", org_a.analyst.id, org_a.tenant.id)]


def test_platform_is_mounted(client: TestClient, org_a: Org) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    assert b"http_requests_total" in client.get("/metrics").content
    viewer = h(client, org_a, org_a.viewer)
    assert client.get("/approvals", headers=viewer).status_code == 200  # guardrails router
    assert client.get("/auth/me", headers=viewer).json()["role"] == "viewer"
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/clients",
        "/clients/{client_id}/files",
        "/files/{file_id}/transitions",
        "/mappings/{mapping_id}/decision",
        "/files/{file_id}/signoff",
    } <= paths


def test_no_migrations_at_startup_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    import claims_onboarding.app as app_module

    calls: list[str] = []
    for name in ("migrate_auth", "migrate_observability", "migrate_guardrails", "migrate_claims"):
        monkeypatch.setattr(app_module, name, lambda _url, n=name: calls.append(n))
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/none")
    monkeypatch.delenv("RUN_MIGRATIONS_ON_START", raising=False)
    app_module.create_app()
    assert calls == []
    monkeypatch.setenv("RUN_MIGRATIONS_ON_START", "true")
    app_module.create_app()
    assert calls == [
        "migrate_auth",
        "migrate_observability",
        "migrate_guardrails",
        "migrate_claims",
    ]
