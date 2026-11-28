"""Claims onboarding operations: tenant-scoped, guarded and audited (ADR 0009).

Rules every function follows:

- **Tenant scope:** every read goes through ``TenantScope`` (from the access token), and
  another tenant's ids raise ``NotFound`` (404), so existence never leaks.
- **Audit:** every change writes an audit row *in the same transaction*. If the change
  rolls back, so does its audit row. The caller commits.
- **Roles** are checked by the HTTP layer and again here for the go-live sign-off
  (manager = platform ``admin``) and four-eyes, so the rule holds for any caller.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from ai_platform_auth import Conflict, NotFound, PermissionDenied, Role, TenantScope, User, audit
from ai_platform_auth.service import role_allows
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from claims_onboarding.models import (
    Base,
    ClaimsFile,
    Client,
    ColumnMapping,
    FileColumn,
    FileStatus,
    GoLiveSignoff,
    MappingStatus,
    RuleDefinition,
    RuleKind,
    RuleResult,
    RuleStatus,
    Severity,
    SignoffDecision,
)
from claims_onboarding.models import ClientStatus as CS
from claims_onboarding.status import (
    NEEDS_REASON,
    SIGNOFF_ONLY,
    InvalidTransition,
    check_transition,
)

MAX_REASON = 1000
# Files can receive mapping suggestions and decisions only while being mapped/reviewed.
MAPPING_STATUSES = frozenset({FileStatus.MASKED, FileStatus.MAPPED, FileStatus.UNDER_REVIEW})


class FourEyesViolation(PermissionDenied):
    """The person who approved a file's mappings may not sign off its go-live."""

    def __init__(self) -> None:
        super().__init__("four-eyes: you approved mappings on this file, so another manager "
                         "must sign off its go-live")  # fmt: skip


class GuardFailed(Conflict):
    """The file's data doesn't allow this status change yet (409)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _get[M: Base](session: Session, scope: TenantScope, model: type[M], id_: uuid.UUID, *,
                 lock: bool = False) -> M:  # fmt: skip
    stmt = scope.apply(select(model), model).where(model.id == id_)  # type: ignore[attr-defined]
    if lock:
        stmt = stmt.with_for_update()
    row = session.scalar(stmt)
    if row is None:
        raise NotFound(f"{model.__tablename__.removesuffix('s').replace('_', ' ')} not found")
    return row


def _audit(session: Session, scope: TenantScope, actor: User, action: str, resource_type: str,
           resource_id: uuid.UUID, **details: Any) -> None:  # noqa: ANN401  # fmt: skip
    audit(session, action, tenant_id=scope.tenant_id, user_id=actor.id,
          resource_type=resource_type, resource_id=str(resource_id),
          details={k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in details.items()
                   if v is not None})  # fmt: skip


# ---------------------------------------------------------------- clients


def create_client(session: Session, scope: TenantScope, actor: User, *, code: str, name: str,
                  go_live_target: date | None = None) -> Client:  # fmt: skip
    exists = session.scalar(scope.apply(select(Client.id), Client).where(Client.code == code))
    if exists is not None:
        raise Conflict(f"a client with code {code!r} already exists")
    client = Client(id=uuid.uuid4(), tenant_id=scope.tenant_id, code=code, name=name,
                    status=CS.ONBOARDING, go_live_target=go_live_target,
                    created_by=actor.id)  # fmt: skip
    session.add(client)
    session.flush()
    _audit(session, scope, actor, "claims.client.created", "claims_client", client.id, code=code)
    return client


def list_clients(session: Session, scope: TenantScope) -> Sequence[Client]:
    return session.scalars(scope.apply(select(Client), Client).order_by(Client.code)).all()


def get_client(session: Session, scope: TenantScope, client_id: uuid.UUID) -> Client:
    return _get(session, scope, Client, client_id)


# ---------------------------------------------------------------- files


def register_file(
    session: Session,
    scope: TenantScope,
    actor: User,
    client_id: uuid.UUID,
    *,
    filename: str,
    sha256: str,
    size_bytes: int,
    headers: Sequence[str],
    row_count: int | None = None,
    previous_file_id: uuid.UUID | None = None,
) -> ClaimsFile:
    """Record a file the client sent (metadata and headings only) at status ``received``.

    A resubmission names ``previous_file_id``: a file of the same client that ended at
    ``issues_sent``.
    """
    client = _get(session, scope, Client, client_id, lock=True)  # serialises sequence numbers
    if previous_file_id is not None:
        previous = _get(session, scope, ClaimsFile, previous_file_id)
        if previous.client_id != client.id:
            raise NotFound("previous file not found")  # never confirm another client's file
        if previous.status != FileStatus.ISSUES_SENT:
            raise Conflict("a resubmission must follow a file whose issues were sent "
                           f"(that file is {previous.status})")  # fmt: skip
    last = session.scalar(
        scope.apply(select(func.max(ClaimsFile.sequence)), ClaimsFile)
        .where(ClaimsFile.client_id == client.id)
    )  # fmt: skip
    file = ClaimsFile(
        id=uuid.uuid4(), tenant_id=scope.tenant_id, client_id=client.id,
        sequence=(last or 0) + 1, previous_file_id=previous_file_id, filename=filename,
        sha256=sha256.lower(), size_bytes=size_bytes, row_count=row_count,
        status=FileStatus.RECEIVED, uploaded_by=actor.id,
    )  # fmt: skip
    session.add(file)
    session.flush()
    session.add_all(FileColumn(id=uuid.uuid4(), tenant_id=scope.tenant_id, file_id=file.id,
                               position=i, header=h) for i, h in enumerate(headers))  # fmt: skip
    session.flush()
    _audit(session, scope, actor, "claims.file.registered", "claims_file", file.id,
           client_id=client.id, sequence=file.sequence, columns=len(headers),
           previous_file_id=previous_file_id, status=file.status)  # fmt: skip
    return file


def get_file(session: Session, scope: TenantScope, file_id: uuid.UUID) -> ClaimsFile:
    return _get(session, scope, ClaimsFile, file_id)


def file_columns(session: Session, scope: TenantScope, file_id: uuid.UUID) -> Sequence[FileColumn]:
    return session.scalars(scope.apply(select(FileColumn), FileColumn)
                           .where(FileColumn.file_id == file_id)
                           .order_by(FileColumn.position)).all()  # fmt: skip


def file_mappings(session: Session, scope: TenantScope,
                  file_id: uuid.UUID) -> Sequence[ColumnMapping]:  # fmt: skip
    return session.scalars(scope.apply(select(ColumnMapping), ColumnMapping)
                           .where(ColumnMapping.file_id == file_id)
                           .order_by(ColumnMapping.created_at)).all()  # fmt: skip


def transition(
    session: Session,
    scope: TenantScope,
    actor: User,
    file_id: uuid.UUID,
    target: FileStatus,
    *,
    reason: str | None = None,
) -> ClaimsFile:
    """Move a file to ``target`` if the machine and the data allow it; audited."""
    file = _get(session, scope, ClaimsFile, file_id, lock=True)
    current = FileStatus(file.status)
    if target in SIGNOFF_ONLY:
        raise InvalidTransition(current, target, "a file goes live only through a go-live "
                                "sign-off (POST /files/{id}/signoff)")  # fmt: skip
    check_transition(current, target)
    reason = (reason or "").strip() or None
    if (current, target) in NEEDS_REASON and reason is None:
        raise InvalidTransition(current, target, f"moving a file from {current} back to "
                                f"{target} needs a reason")  # fmt: skip
    if reason is not None and len(reason) > MAX_REASON:
        raise Conflict(f"reason is longer than {MAX_REASON} characters")
    _check_guards(session, scope, file, target)
    return _set_status(session, scope, actor, file, target, reason=reason)


def _set_status(session: Session, scope: TenantScope, actor: User, file: ClaimsFile,
                target: FileStatus, *, reason: str | None = None) -> ClaimsFile:  # fmt: skip
    previous = file.status
    file.status = target
    file.status_changed_at = _now()
    session.flush()
    _audit(session, scope, actor, "claims.file.status_changed", "claims_file", file.id,
           **{"from": previous, "to": str(target), "reason": reason})  # fmt: skip
    return file


def _check_guards(session: Session, scope: TenantScope, file: ClaimsFile,
                  target: FileStatus) -> None:  # fmt: skip
    if target == FileStatus.RULES_RUN:
        rows = session.execute(
            select(ColumnMapping.status, func.count())
            .where(ColumnMapping.tenant_id == scope.tenant_id, ColumnMapping.file_id == file.id)
            .group_by(ColumnMapping.status)
        ).all()  # fmt: skip
        counts: dict[str, int] = {status: n for status, n in rows}
        if counts.get(MappingStatus.SUGGESTED, 0):
            raise GuardFailed(f"{counts[MappingStatus.SUGGESTED]} mapping suggestion(s) are "
                              "still undecided: approve or reject each one first")  # fmt: skip
        if not counts.get(MappingStatus.APPROVED, 0):
            raise GuardFailed("no approved mappings: approve at least one before running rules")
    elif target == FileStatus.READY_FOR_SIGNOFF:
        results: list[tuple[str, int]] = [(sev, n) for sev, n in session.execute(
            select(RuleResult.severity, RuleResult.failed_rows)
            .where(RuleResult.tenant_id == scope.tenant_id, RuleResult.file_id == file.id)
        ).all()]  # fmt: skip
        if not results:
            raise GuardFailed("no rule results recorded for this file: run the rules first")
        blockers = [n for severity, n in results if severity == Severity.BLOCKER and n > 0]
        if blockers:
            raise GuardFailed(f"{len(blockers)} blocker rule(s) failed on {sum(blockers)} "
                              "row(s): the file can only move to issues_sent")  # fmt: skip


# ---------------------------------------------------------------- mappings


def suggest_mapping(
    session: Session,
    scope: TenantScope,
    actor: User,
    file_id: uuid.UUID,
    *,
    file_column_id: uuid.UUID,
    target_field: str,
    confidence: Decimal | float | None = None,
    model: str | None = None,
) -> ColumnMapping:
    """Record a suggestion that a column holds ``target_field`` (from a model or a person)."""
    file = _get(session, scope, ClaimsFile, file_id)
    if file.status not in MAPPING_STATUSES:
        raise Conflict(f"mappings can't be added to a file at {file.status}")
    column = _get(session, scope, FileColumn, file_column_id)
    if column.file_id != file.id:
        raise NotFound("file column not found")
    duplicate = session.scalar(
        scope.apply(select(ColumnMapping.id), ColumnMapping)
        .where(ColumnMapping.file_column_id == column.id,
               ColumnMapping.target_field == target_field)
    )  # fmt: skip
    if duplicate is not None:
        raise Conflict(f"column {column.header!r} already has a {target_field!r} mapping")
    mapping = ColumnMapping(
        id=uuid.uuid4(), tenant_id=scope.tenant_id, file_id=file.id, file_column_id=column.id,
        target_field=target_field, status=MappingStatus.SUGGESTED,
        confidence=None if confidence is None else Decimal(str(confidence)), model=model,
        suggested_by=actor.id,
    )  # fmt: skip
    session.add(mapping)
    session.flush()
    _audit(session, scope, actor, "claims.mapping.suggested", "claims_mapping", mapping.id,
           file_id=file.id, target_field=target_field, model=model,
           confidence=None if confidence is None else float(confidence))  # fmt: skip
    return mapping


def decide_mapping(session: Session, scope: TenantScope, actor: User, mapping_id: uuid.UUID, *,
                   approve: bool) -> ColumnMapping:  # fmt: skip
    """Approve or reject a mapping. A decision can be changed while the file is still being
    mapped or reviewed (e.g. after a send-back); every decision is audited."""
    mapping = _get(session, scope, ColumnMapping, mapping_id, lock=True)
    file = _get(session, scope, ClaimsFile, mapping.file_id)
    if file.status not in MAPPING_STATUSES:
        raise Conflict(f"mappings on a file at {file.status} can no longer be changed")
    decision = MappingStatus.APPROVED if approve else MappingStatus.REJECTED
    if mapping.status == decision:
        raise Conflict(f"mapping is already {decision}")
    mapping.status = decision
    mapping.approved_by = actor.id if approve else None
    mapping.rejected_by = None if approve else actor.id
    mapping.decided_at = _now()
    session.flush()
    _audit(session, scope, actor, "claims.mapping.decided", "claims_mapping", mapping.id,
           file_id=file.id, target_field=mapping.target_field, decision=str(decision))  # fmt: skip
    return mapping


# ---------------------------------------------------------------- rules (Phase 6 fills these)


def add_rule_definition(
    session: Session,
    scope: TenantScope,
    actor: User,
    *,
    rule_key: str,
    target_field: str,
    rule_text: str,
    severity: Severity,
    kind: RuleKind = RuleKind.ANALYST,
    client_id: uuid.UUID | None = None,
) -> RuleDefinition:
    """Add the next version of a rule as ``active``, retiring the previous active version."""
    if client_id is not None:
        _get(session, scope, Client, client_id)
    versions = session.scalars(scope.apply(select(RuleDefinition), RuleDefinition)
                               .where(RuleDefinition.rule_key == rule_key)
                               .with_for_update()).all()  # fmt: skip
    for old in versions:
        if old.status == RuleStatus.ACTIVE:
            old.status = RuleStatus.RETIRED
    rule = RuleDefinition(
        id=uuid.uuid4(), tenant_id=scope.tenant_id, client_id=client_id, rule_key=rule_key,
        version=max((v.version for v in versions), default=0) + 1, kind=kind,
        target_field=target_field, rule_text=rule_text, severity=severity,
        status=RuleStatus.ACTIVE, created_by=actor.id,
    )  # fmt: skip
    session.add(rule)
    session.flush()
    _audit(session, scope, actor, "claims.rule.defined", "claims_rule", rule.id,
           rule_key=rule_key, version=rule.version, kind=str(kind),
           severity=str(severity))  # fmt: skip
    return rule


def record_rule_result(
    session: Session,
    scope: TenantScope,
    actor: User,
    file_id: uuid.UUID,
    *,
    rule_definition_id: uuid.UUID,
    failed_rows: int,
    examples: Sequence[dict[str, Any]] = (),
) -> RuleResult:
    """Store one rule's outcome for a file at ``rules_run`` (examples must be masked)."""
    file = _get(session, scope, ClaimsFile, file_id)
    if file.status != FileStatus.RULES_RUN:
        raise Conflict(f"rule results can only be recorded at rules_run (file is {file.status})")
    rule = _get(session, scope, RuleDefinition, rule_definition_id)
    if rule.status != RuleStatus.ACTIVE or rule.client_id not in (None, file.client_id):
        raise Conflict("that rule doesn't apply to this file")
    result = RuleResult(id=uuid.uuid4(), tenant_id=scope.tenant_id, file_id=file.id,
                        rule_definition_id=rule.id, severity=rule.severity,
                        failed_rows=failed_rows, examples=list(examples))  # fmt: skip
    session.add(result)
    session.flush()
    _audit(session, scope, actor, "claims.rule_result.recorded", "claims_rule_result", result.id,
           file_id=file.id, rule_key=rule.rule_key, severity=rule.severity,
           failed_rows=failed_rows)  # fmt: skip
    return result


# ---------------------------------------------------------------- go-live sign-off


def sign_off(session: Session, scope: TenantScope, actor: User, file_id: uuid.UUID, *,
             approve: bool, comment: str = "") -> GoLiveSignoff:  # fmt: skip
    """A manager's go-live decision. Approval makes the file (and its client) live.

    Four-eyes: anyone who approved a mapping on this file may not sign it off.
    """
    if not role_allows(actor.role, Role.ADMIN):
        raise PermissionDenied("only a manager (admin) can sign off go-live")
    file = _get(session, scope, ClaimsFile, file_id, lock=True)
    current = FileStatus(file.status)
    if current != FileStatus.READY_FOR_SIGNOFF:
        raise InvalidTransition(current, FileStatus.LIVE, "only a file at ready_for_signoff can "
                                f"be signed off (this one is {current})")  # fmt: skip
    approved_mapping = session.scalar(
        scope.apply(select(ColumnMapping.id), ColumnMapping)
        .where(ColumnMapping.file_id == file.id, ColumnMapping.approved_by == actor.id).limit(1)
    )  # fmt: skip
    if approved_mapping is not None:
        raise FourEyesViolation
    comment = comment.strip()
    if not approve and not comment:
        raise Conflict("a rejected sign-off needs a comment")
    decision = SignoffDecision.APPROVED if approve else SignoffDecision.REJECTED
    signoff = GoLiveSignoff(id=uuid.uuid4(), tenant_id=scope.tenant_id, file_id=file.id,
                            signed_by=actor.id, decision=decision, comment=comment)  # fmt: skip
    session.add(signoff)
    session.flush()
    _audit(session, scope, actor, "claims.signoff.recorded", "claims_file", file.id,
           decision=str(decision), comment=comment or None)  # fmt: skip
    if approve:
        check_transition(current, FileStatus.LIVE)
        _set_status(session, scope, actor, file, FileStatus.LIVE)
        client = _get(session, scope, Client, file.client_id, lock=True)
        if client.status != CS.LIVE:
            client.status = CS.LIVE
            session.flush()
            _audit(session, scope, actor, "claims.client.live", "claims_client", client.id,
                   file_id=file.id)  # fmt: skip
    return signoff
