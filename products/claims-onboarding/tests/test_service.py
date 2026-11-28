"""Service rules on a real Postgres: guards, four-eyes, audit and tenant isolation."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from ai_platform_auth import Conflict, NotFound, PermissionDenied, User
from ai_platform_auth.models import AuditLog
from claims_onboarding import service
from claims_onboarding.models import (
    Base,
    ClaimsFile,
    ClientStatus,
    ColumnMapping,
    FileColumn,
    FileStatus,
    GoLiveSignoff,
    RuleKind,
    RuleResult,
    Severity,
)
from claims_onboarding.service import FourEyesViolation, GuardFailed
from claims_onboarding.status import InvalidTransition
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .conftest import Org

HEADERS = ["CLM_NBR", "MBR_ID", "SVC_DT", "BILLED_AMT"]


def _sha(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _client(session: Session, org: Org, code: str = "HP-NORTH") -> uuid.UUID:
    return service.create_client(
        session, org.scope, org.analyst, code=code, name="Northwind Health Plan (synthetic)"
    ).id


def _file(session: Session, org: Org, client_id: uuid.UUID, **kw: Any) -> ClaimsFile:
    return service.register_file(
        session,
        org.scope,
        org.analyst,
        client_id,
        filename=kw.pop("filename", "claims_jan.csv"),
        sha256=kw.pop("sha256", _sha("jan")),
        size_bytes=1024,
        headers=kw.pop("headers", HEADERS),
        row_count=200,
        **kw,
    )


def _move(
    session: Session,
    org: Org,
    file: ClaimsFile,
    *targets: FileStatus,
    actor: User | None = None,
    reason: str | None = None,
) -> None:
    for target in targets:
        service.transition(session, org.scope, actor or org.analyst, file.id, target, reason=reason)


def _approve_all(session: Session, org: Org, file: ClaimsFile, approver: User) -> None:
    columns = service.file_columns(session, org.scope, file.id)
    for column, field in zip(
        columns, ["claim_id", "member_id", "service_date", "billed_amount"], strict=True
    ):
        m = service.suggest_mapping(
            session,
            org.scope,
            org.analyst,
            file.id,
            file_column_id=column.id,
            target_field=field,
            confidence=0.91,
            model="heuristic",
        )
        service.decide_mapping(session, org.scope, approver, m.id, approve=True)


def _to_rules_run(session: Session, org: Org, file: ClaimsFile, approver: User) -> None:
    _move(session, org, file, FileStatus.MASKED, FileStatus.MAPPED)
    _approve_all(session, org, file, approver)
    _move(session, org, file, FileStatus.UNDER_REVIEW, FileStatus.RULES_RUN)


def _rule(session: Session, org: Org, severity: Severity, key: str = "npi_10_digits") -> uuid.UUID:
    return service.add_rule_definition(
        session,
        org.scope,
        org.analyst,
        rule_key=key,
        target_field="provider_npi",
        rule_text="exactly 10 digits",
        severity=severity,
        kind=RuleKind.BUILT_IN,
    ).id


def _audit_actions(session: Session, org: Org) -> list[str]:
    return list(
        session.scalars(
            select(AuditLog.action)
            .where(AuditLog.tenant_id == org.tenant.id, AuditLog.action.like("claims.%"))
            .order_by(AuditLog.id)
        )
    )


# ---------------------------------------------------------------- happy path and audit


def test_full_lifecycle_to_live_is_audited_step_by_step(session: Session, org_a: Org) -> None:
    client_id = _client(session, org_a)
    file = _file(session, org_a, client_id)
    assert file.status == FileStatus.RECEIVED and file.sequence == 1
    assert [c.header for c in service.file_columns(session, org_a.scope, file.id)] == HEADERS
    _to_rules_run(session, org_a, file, approver=org_a.analyst)
    rule_id = _rule(session, org_a, Severity.WARNING)
    service.record_rule_result(
        session, org_a.scope, org_a.analyst, file.id, rule_definition_id=rule_id, failed_rows=3
    )
    _move(session, org_a, file, FileStatus.READY_FOR_SIGNOFF)
    signoff = service.sign_off(session, org_a.scope, org_a.manager, file.id, approve=True)
    assert signoff.decision == "approved" and file.status == FileStatus.LIVE
    assert service.get_client(session, org_a.scope, client_id).status == ClientStatus.LIVE

    actions = _audit_actions(session, org_a)
    assert actions[:2] == ["claims.client.created", "claims.file.registered"]
    assert actions.count("claims.mapping.suggested") == 4
    assert actions.count("claims.mapping.decided") == 4
    assert actions[-3:] == [
        "claims.signoff.recorded",
        "claims.file.status_changed",
        "claims.client.live",
    ]
    moves = session.execute(
        select(AuditLog.details, AuditLog.user_id)
        .where(
            AuditLog.tenant_id == org_a.tenant.id,
            AuditLog.action == "claims.file.status_changed",
            AuditLog.resource_id == str(file.id),
        )
        .order_by(AuditLog.id)
    ).all()
    assert [(d["from"], d["to"]) for d, _ in moves] == [
        ("received", "masked"),
        ("masked", "mapped"),
        ("mapped", "under_review"),
        ("under_review", "rules_run"),
        ("rules_run", "ready_for_signoff"),
        ("ready_for_signoff", "live"),
    ]
    assert moves[0][1] == org_a.analyst.id and moves[-1][1] == org_a.manager.id


def test_audit_rows_roll_back_with_the_change(session: Session, org_a: Org) -> None:
    file = _file(session, org_a, _client(session, org_a))
    session.commit()
    before = _audit_actions(session, org_a)
    _move(session, org_a, file, FileStatus.MASKED)
    session.rollback()
    assert _audit_actions(session, org_a) == before
    assert service.get_file(session, org_a.scope, file.id).status == FileStatus.RECEIVED


def test_refused_changes_write_no_audit_row(session: Session, org_a: Org) -> None:
    file = _file(session, org_a, _client(session, org_a))
    before = _audit_actions(session, org_a)
    with pytest.raises(InvalidTransition):
        _move(session, org_a, file, FileStatus.MAPPED)
    assert _audit_actions(session, org_a) == before


# ---------------------------------------------------------------- status machine in the service


def test_invalid_transitions_raise(session: Session, org_a: Org) -> None:
    file = _file(session, org_a, _client(session, org_a))
    with pytest.raises(InvalidTransition, match="from received to rules_run"):
        _move(session, org_a, file, FileStatus.RULES_RUN)
    with pytest.raises(InvalidTransition, match="only through a go-live sign-off"):
        _move(session, org_a, file, FileStatus.LIVE)


def test_send_back_needs_a_reason_and_is_audited(session: Session, org_a: Org) -> None:
    file = _file(session, org_a, _client(session, org_a))
    _move(session, org_a, file, FileStatus.MASKED, FileStatus.MAPPED, FileStatus.UNDER_REVIEW)
    for blank in (None, "", "   "):
        with pytest.raises(InvalidTransition, match="needs a reason"):
            _move(session, org_a, file, FileStatus.MAPPED, reason=blank)
    with pytest.raises(Conflict, match="longer than"):
        _move(session, org_a, file, FileStatus.MAPPED, reason="x" * 1001)
    _move(
        session,
        org_a,
        file,
        FileStatus.MAPPED,
        actor=org_a.analyst2,
        reason="  BILLED_AMT looks like allowed amount; check with the client  ",
    )
    details = session.scalar(
        select(AuditLog.details)
        .where(
            AuditLog.action == "claims.file.status_changed", AuditLog.resource_id == str(file.id)
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    assert details == {
        "from": "under_review",
        "to": "mapped",
        "reason": "BILLED_AMT looks like allowed amount; check with the client",
    }


def test_rules_run_needs_every_mapping_decided(session: Session, org_a: Org) -> None:
    file = _file(session, org_a, _client(session, org_a))
    _move(session, org_a, file, FileStatus.MASKED, FileStatus.MAPPED, FileStatus.UNDER_REVIEW)
    with pytest.raises(GuardFailed, match="no approved mappings"):
        _move(session, org_a, file, FileStatus.RULES_RUN)
    column = service.file_columns(session, org_a.scope, file.id)[0]
    m = service.suggest_mapping(
        session,
        org_a.scope,
        org_a.analyst,
        file.id,
        file_column_id=column.id,
        target_field="claim_id",
    )
    with pytest.raises(GuardFailed, match="1 mapping suggestion"):
        _move(session, org_a, file, FileStatus.RULES_RUN)
    service.decide_mapping(session, org_a.scope, org_a.analyst, m.id, approve=True)
    _move(session, org_a, file, FileStatus.RULES_RUN)


def test_blockers_allow_only_issues_sent(session: Session, org_a: Org) -> None:
    file = _file(session, org_a, _client(session, org_a))
    _to_rules_run(session, org_a, file, approver=org_a.analyst)
    with pytest.raises(GuardFailed, match="no rule results"):
        _move(session, org_a, file, FileStatus.READY_FOR_SIGNOFF)
    blocker = _rule(session, org_a, Severity.BLOCKER)
    service.record_rule_result(
        session,
        org_a.scope,
        org_a.analyst,
        file.id,
        rule_definition_id=blocker,
        failed_rows=6,
        examples=[{"line": 14, "value": "<NPI_1>"}],
    )
    with pytest.raises(GuardFailed, match="1 blocker rule\\(s\\) failed on 6 row"):
        _move(session, org_a, file, FileStatus.READY_FOR_SIGNOFF)
    _move(session, org_a, file, FileStatus.ISSUES_SENT)
    assert file.status == FileStatus.ISSUES_SENT


def test_blocker_rules_that_passed_dont_block(session: Session, org_a: Org) -> None:
    file = _file(session, org_a, _client(session, org_a))
    _to_rules_run(session, org_a, file, approver=org_a.analyst)
    service.record_rule_result(
        session,
        org_a.scope,
        org_a.analyst,
        file.id,
        rule_definition_id=_rule(session, org_a, Severity.BLOCKER),
        failed_rows=0,
    )
    _move(session, org_a, file, FileStatus.READY_FOR_SIGNOFF)


@pytest.mark.xfail(
    strict=True,
    reason="Spec 1 Phase 4: leaving masked will need a privacy "
    "report for the file; until then masked is a status-only placeholder",
)
def test_cannot_leave_masked_without_a_privacy_report(session: Session, org_a: Org) -> None:
    """Phase 4 makes this pass and must then remove the xfail marker."""
    file = _file(session, org_a, _client(session, org_a))
    _move(session, org_a, file, FileStatus.MASKED)
    with pytest.raises(GuardFailed, match="privacy report"):
        _move(session, org_a, file, FileStatus.MAPPED)


# ---------------------------------------------------------------- resubmissions


def test_resubmission_is_a_new_file_after_issues_sent(session: Session, org_a: Org) -> None:
    client_id = _client(session, org_a)
    first = _file(session, org_a, client_id)
    with pytest.raises(Conflict, match="must follow a file whose issues were sent"):
        _file(session, org_a, client_id, previous_file_id=first.id, sha256=_sha("feb"))
    _to_rules_run(session, org_a, first, approver=org_a.analyst)
    _move(session, org_a, first, FileStatus.ISSUES_SENT)
    second = _file(session, org_a, client_id, previous_file_id=first.id, sha256=_sha("jan-v2"))
    assert second.sequence == 2 and second.status == FileStatus.RECEIVED
    assert second.previous_file_id == first.id
    other_client = _client(session, org_a, code="HP-SOUTH")
    with pytest.raises(NotFound):  # another client's file is never accepted as "previous"
        _file(session, org_a, other_client, previous_file_id=first.id)


# ---------------------------------------------------------------- mappings


def test_mapping_rules(session: Session, org_a: Org) -> None:
    file = _file(session, org_a, _client(session, org_a))
    column = service.file_columns(session, org_a.scope, file.id)[0]
    with pytest.raises(Conflict, match="can't be added to a file at received"):
        service.suggest_mapping(
            session,
            org_a.scope,
            org_a.analyst,
            file.id,
            file_column_id=column.id,
            target_field="claim_id",
        )
    _move(session, org_a, file, FileStatus.MASKED)
    m = service.suggest_mapping(
        session,
        org_a.scope,
        org_a.analyst,
        file.id,
        file_column_id=column.id,
        target_field="claim_id",
        confidence=0.42,
        model="gemini-flash",
    )
    assert m.status == "suggested" and m.confidence == Decimal("0.42")
    with pytest.raises(Conflict, match="already has a 'claim_id' mapping"):
        service.suggest_mapping(
            session,
            org_a.scope,
            org_a.analyst,
            file.id,
            file_column_id=column.id,
            target_field="claim_id",
        )
    service.decide_mapping(session, org_a.scope, org_a.analyst, m.id, approve=False)
    assert m.rejected_by == org_a.analyst.id and m.approved_by is None
    with pytest.raises(Conflict, match="already rejected"):
        service.decide_mapping(session, org_a.scope, org_a.analyst, m.id, approve=False)
    service.decide_mapping(session, org_a.scope, org_a.analyst2, m.id, approve=True)
    assert m.approved_by == org_a.analyst2.id and m.rejected_by is None
    other_file = _file(session, org_a, file.client_id, sha256=_sha("other"))
    _move(session, org_a, other_file, FileStatus.MASKED)
    with pytest.raises(NotFound, match="file column"):  # a column of another file
        service.suggest_mapping(
            session,
            org_a.scope,
            org_a.analyst,
            other_file.id,
            file_column_id=column.id,
            target_field="member_id",
        )


def test_mapping_decision_columns_are_enforced_by_the_database(
    session: Session, org_a: Org
) -> None:
    file = _file(session, org_a, _client(session, org_a))
    column = service.file_columns(session, org_a.scope, file.id)[0]
    with pytest.raises(IntegrityError, match="ck_mappings_decision"), session.begin_nested():
        session.add(ColumnMapping(tenant_id=org_a.tenant.id, file_id=file.id,
                                  file_column_id=column.id, target_field="claim_id",
                                  status="approved"))  # fmt: skip  # approved without approver
        session.flush()


# ---------------------------------------------------------------- four-eyes and roles


@pytest.mark.parametrize("approver", ["analyst", "manager"])
def test_four_eyes_mapping_approver_cannot_sign_off(
    session: Session, org_a: Org, approver: str
) -> None:
    """An admin may approve mappings (admins rank above analysts), but then a different
    manager must sign off."""
    file = _file(session, org_a, _client(session, org_a))
    approving_user = getattr(org_a, approver)
    _to_rules_run(session, org_a, file, approver=approving_user)
    service.record_rule_result(
        session,
        org_a.scope,
        org_a.analyst,
        file.id,
        rule_definition_id=_rule(session, org_a, Severity.BLOCKER),
        failed_rows=0,
    )
    _move(session, org_a, file, FileStatus.READY_FOR_SIGNOFF)
    if approver == "manager":
        with pytest.raises(FourEyesViolation, match="four-eyes"):
            service.sign_off(session, org_a.scope, org_a.manager, file.id, approve=True)
        signer = org_a.manager2
    else:
        signer = org_a.manager
    assert file.status == FileStatus.READY_FOR_SIGNOFF
    service.sign_off(session, org_a.scope, signer, file.id, approve=True)
    assert file.status == FileStatus.LIVE


def _ready(session: Session, org: Org) -> ClaimsFile:
    """A file at ready_for_signoff whose mappings the analyst approved."""
    file = _file(session, org, _client(session, org))
    _to_rules_run(session, org, file, approver=org.analyst)
    service.record_rule_result(
        session,
        org.scope,
        org.analyst,
        file.id,
        rule_definition_id=_rule(session, org, Severity.WARNING),
        failed_rows=1,
    )
    _move(session, org, file, FileStatus.READY_FOR_SIGNOFF)
    return file


def test_only_managers_sign_off_files_that_are_ready(session: Session, org_a: Org) -> None:
    file = _file(session, org_a, _client(session, org_a))
    with pytest.raises(PermissionDenied, match="only a manager"):
        service.sign_off(session, org_a.scope, org_a.analyst2, file.id, approve=True)
    with pytest.raises(InvalidTransition, match="only a file at ready_for_signoff"):
        service.sign_off(session, org_a.scope, org_a.manager, file.id, approve=True)


@pytest.mark.parametrize("return_to", [FileStatus.UNDER_REVIEW, FileStatus.ISSUES_SENT])
def test_rejected_signoff_needs_a_reason_and_a_target(
    session: Session, org_a: Org, return_to: FileStatus
) -> None:
    file = _ready(session, org_a)
    m = org_a.manager
    with pytest.raises(Conflict, match="needs a reason"):
        service.sign_off(session, org_a.scope, m, file.id, approve=False, return_to=return_to)
    with pytest.raises(Conflict, match="needs a reason"):
        service.sign_off(
            session, org_a.scope, m, file.id, approve=False, reason="   ", return_to=return_to
        )
    for bad_target in (None, FileStatus.MAPPED, FileStatus.LIVE):
        with pytest.raises(Conflict, match="needs return_to"):
            service.sign_off(
                session, org_a.scope, m, file.id, approve=False, reason="why", return_to=bad_target
            )
    with pytest.raises(Conflict, match="longer than"):
        service.sign_off(
            session, org_a.scope, m, file.id, approve=False, reason="x" * 1001, return_to=return_to
        )
    with pytest.raises(Conflict, match="don't give return_to"):
        service.sign_off(session, org_a.scope, m, file.id, approve=True, return_to=return_to)
    assert file.status == FileStatus.READY_FOR_SIGNOFF  # nothing above changed anything

    reason = "  Paid amounts look shifted by one column; confirm before go-live  "
    signoff = service.sign_off(
        session, org_a.scope, m, file.id, approve=False, reason=reason, return_to=return_to
    )
    assert (signoff.decision, signoff.returned_to) == ("rejected", return_to)
    assert signoff.reason == reason.strip() and file.status == return_to

    rows = session.execute(
        select(AuditLog.action, AuditLog.details, AuditLog.user_id)
        .where(AuditLog.resource_id == str(file.id))
        .order_by(AuditLog.id.desc())
        .limit(2)
    ).all()
    assert [(a, d, u) for a, d, u in reversed(rows)] == [
        (
            "claims.signoff.recorded",
            {"decision": "rejected", "reason": reason.strip(), "return_to": str(return_to)},
            m.id,
        ),
        (
            "claims.file.status_changed",
            {"from": "ready_for_signoff", "to": str(return_to), "reason": reason.strip()},
            m.id,
        ),
    ]


def test_moves_out_of_ready_for_signoff_only_via_signoff(session: Session, org_a: Org) -> None:
    file = _ready(session, org_a)
    for target in (FileStatus.UNDER_REVIEW, FileStatus.ISSUES_SENT, FileStatus.LIVE):
        with pytest.raises(InvalidTransition, match="only through a go-live sign-off"):
            _move(session, org_a, file, target, reason="trying to skip the sign-off")
    assert file.status == FileStatus.READY_FOR_SIGNOFF


def test_rejected_back_to_review_can_be_fixed_rerun_and_approved(
    session: Session, org_a: Org
) -> None:
    file = _ready(session, org_a)
    service.sign_off(
        session,
        org_a.scope,
        org_a.manager,
        file.id,
        approve=False,
        reason="Re-check the member ID mapping",
        return_to=FileStatus.UNDER_REVIEW,
    )
    _move(session, org_a, file, FileStatus.MAPPED, reason="manager asked for a re-check")
    _move(session, org_a, file, FileStatus.UNDER_REVIEW, FileStatus.RULES_RUN)
    rule = session.scalar(
        select(RuleResult.rule_definition_id).where(RuleResult.file_id == file.id)
    )
    assert rule is not None
    service.record_rule_result(
        session, org_a.scope, org_a.analyst, file.id, rule_definition_id=rule, failed_rows=0
    )  # re-run replaces
    results = session.scalars(select(RuleResult).where(RuleResult.file_id == file.id)).all()
    assert [r.failed_rows for r in results] == [0]
    replaced = session.scalar(
        select(AuditLog.details)
        .where(AuditLog.action == "claims.rule_result.recorded")
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    assert replaced is not None and replaced["replaced"] is True
    _move(session, org_a, file, FileStatus.READY_FOR_SIGNOFF)
    service.sign_off(session, org_a.scope, org_a.manager, file.id, approve=True)
    assert file.status == FileStatus.LIVE
    decisions = session.scalars(
        select(GoLiveSignoff.decision)
        .where(GoLiveSignoff.file_id == file.id)
        .order_by(GoLiveSignoff.signed_at, GoLiveSignoff.decision.desc())
    ).all()
    assert sorted(decisions) == ["approved", "rejected"]


def test_rejected_to_issues_sent_allows_a_resubmission(session: Session, org_a: Org) -> None:
    file = _ready(session, org_a)
    service.sign_off(
        session,
        org_a.scope,
        org_a.manager,
        file.id,
        approve=False,
        reason="Client must resend with 2025 dates",
        return_to=FileStatus.ISSUES_SENT,
    )
    again = _file(session, org_a, file.client_id, previous_file_id=file.id, sha256=_sha("v2"))
    assert again.previous_file_id == file.id and again.status == FileStatus.RECEIVED


def test_signoff_outcome_is_enforced_by_the_database(session: Session, org_a: Org) -> None:
    file = _file(session, org_a, _client(session, org_a))
    bad: list[dict[str, Any]] = [
        {"decision": "rejected", "reason": "", "returned_to": "under_review"},
        {"decision": "rejected", "reason": "why", "returned_to": None},
        {"decision": "rejected", "reason": "why", "returned_to": "mapped"},
        {"decision": "approved", "reason": "", "returned_to": "issues_sent"},
    ]
    for values in bad:
        with (
            pytest.raises(IntegrityError, match="ck_go_live_signoffs_outcome"),
            session.begin_nested(),
        ):
            session.add(
                GoLiveSignoff(
                    tenant_id=org_a.tenant.id, file_id=file.id, signed_by=org_a.manager.id, **values
                )
            )
            session.flush()


# ---------------------------------------------------------------- rules


def test_rule_versions_and_applicability(session: Session, org_a: Org) -> None:
    v1 = _rule(session, org_a, Severity.WARNING, key="pos_2_digits")
    v2 = _rule(session, org_a, Severity.BLOCKER, key="pos_2_digits")
    rows = session.execute(
        text(
            "SELECT id, version, status FROM claims.rule_definitions "
            "WHERE rule_key = 'pos_2_digits' ORDER BY version"
        )
    ).all()
    assert [(r.id, r.version, r.status) for r in rows] == [(v1, 1, "retired"), (v2, 2, "active")]
    client_id = _client(session, org_a)
    file = _file(session, org_a, client_id)
    _to_rules_run(session, org_a, file, approver=org_a.analyst)
    with pytest.raises(Conflict, match="doesn't apply"):  # retired version
        service.record_rule_result(
            session, org_a.scope, org_a.analyst, file.id, rule_definition_id=v1, failed_rows=0
        )
    other_client = _client(session, org_a, code="HP-EAST")
    scoped = service.add_rule_definition(
        session,
        org_a.scope,
        org_a.analyst,
        rule_key="east_only",
        target_field="claim_status",
        rule_text="one of paid / denied",
        severity=Severity.WARNING,
        client_id=other_client,
    )
    with pytest.raises(Conflict, match="doesn't apply"):  # another client's rule
        service.record_rule_result(
            session,
            org_a.scope,
            org_a.analyst,
            file.id,
            rule_definition_id=scoped.id,
            failed_rows=0,
        )
    new_file = _file(session, org_a, client_id, sha256=_sha("new"))
    with pytest.raises(Conflict, match="only be recorded at rules_run"):
        service.record_rule_result(
            session, org_a.scope, org_a.analyst, new_file.id, rule_definition_id=v2, failed_rows=0
        )


# ---------------------------------------------------------------- tenant isolation


def test_other_tenants_ids_are_not_found(session: Session, org_a: Org, org_b: Org) -> None:
    client_a = _client(session, org_a)
    file_a = _file(session, org_a, client_a)
    _move(session, org_a, file_a, FileStatus.MASKED)
    column_a = service.file_columns(session, org_a.scope, file_a.id)[0]
    mapping_a = service.suggest_mapping(
        session,
        org_a.scope,
        org_a.analyst,
        file_a.id,
        file_column_id=column_a.id,
        target_field="claim_id",
    )
    b = org_b.scope
    assert service.list_clients(session, b) == []
    for call in (
        lambda: service.get_client(session, b, client_a),
        lambda: service.get_file(session, b, file_a.id),
        lambda: _file(session, org_b, client_a),
        lambda: service.transition(session, b, org_b.analyst, file_a.id, FileStatus.MAPPED),
        lambda: service.decide_mapping(session, b, org_b.analyst, mapping_a.id, approve=True),
        lambda: service.sign_off(session, b, org_b.manager, file_a.id, approve=True),
    ):
        with pytest.raises(NotFound):
            call()
    assert service.file_columns(session, b, file_a.id) == []
    assert service.file_mappings(session, b, file_a.id) == []
    # the same client code can exist in both tenants
    _client(session, org_b)
    assert [c.code for c in service.list_clients(session, b)] == ["HP-NORTH"]
    assert mapping_a.status == "suggested" and file_a.status == FileStatus.MASKED


def test_database_rejects_cross_tenant_references(session: Session, org_a: Org, org_b: Org) -> None:
    """Even code that bypasses the service can't link rows across tenants."""
    client_a = _client(session, org_a)
    file_a = _file(session, org_a, client_a)
    column_a = service.file_columns(session, org_a.scope, file_a.id)[0]
    client_b = _client(session, org_b)
    attempts: dict[str, Callable[[], Base]] = {
        "fk_files_client": lambda: ClaimsFile(
            tenant_id=org_b.tenant.id,
            client_id=client_a,
            sequence=9,
            filename="x.csv",
            sha256=_sha("x"),
            size_bytes=1,
            status="received",
        ),
        "fk_file_columns_file": lambda: FileColumn(
            tenant_id=org_b.tenant.id, file_id=file_a.id, position=99, header="X"
        ),
        "fk_mappings_file_column": lambda: ColumnMapping(
            tenant_id=org_b.tenant.id,
            file_id=file_a.id,
            file_column_id=column_a.id,
            target_field="claim_id",
            status="suggested",
        ),
        "fk_files_previous": lambda: ClaimsFile(  # same tenant, but another client's file
            tenant_id=org_b.tenant.id,
            client_id=client_b,
            sequence=9,
            filename="y.csv",
            sha256=_sha("y"),
            size_bytes=1,
            status="received",
            previous_file_id=file_a.id,
        ),
    }
    for constraint, make in attempts.items():
        with pytest.raises(IntegrityError, match=constraint), session.begin_nested():
            session.add(make())
            session.flush()


def test_database_rejects_a_mapping_to_another_files_column(session: Session, org_a: Org) -> None:
    client_id = _client(session, org_a)
    file_1 = _file(session, org_a, client_id)
    file_2 = _file(session, org_a, client_id, sha256=_sha("two"))
    column_1 = service.file_columns(session, org_a.scope, file_1.id)[0]
    with pytest.raises(IntegrityError, match="fk_mappings_file_column"), session.begin_nested():
        session.add(
            ColumnMapping(
                tenant_id=org_a.tenant.id,
                file_id=file_2.id,
                file_column_id=column_1.id,
                target_field="claim_id",
                status="suggested",
            )
        )
        session.flush()


def test_duplicate_client_code_in_a_tenant(session: Session, org_a: Org) -> None:
    _client(session, org_a)
    with pytest.raises(Conflict, match="already exists"):
        _client(session, org_a)
