"""The claims onboarding data model (schema ``claims``; ADR 0009).

Every table has ``tenant_id``. A *tenant* is the onboarding organisation using the
platform; a *client* is a health plan being onboarded inside that tenant.

Tenant isolation is enforced twice:

1. in code: every query goes through a ``TenantScope`` taken from the access token
   (``service.py``), so a tenant id never comes from a request body;
2. in the database: every parent has ``UNIQUE (tenant_id, id)`` and every child
   references it with a composite ``(tenant_id, parent_id)`` foreign key, so a row can
   never point at another tenant's row, even through a bug.

Statuses are strings with CHECK constraints (not Postgres enums), so later phases can
add values with a plain constraint change.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "claims"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


# ---------------------------------------------------------------- statuses


class ClientStatus(StrEnum):
    ONBOARDING = "onboarding"
    LIVE = "live"
    ON_HOLD = "on_hold"


class FileStatus(StrEnum):
    """A file's lifecycle; ``status.py`` holds the allowed transitions."""

    RECEIVED = "received"
    MASKED = "masked"
    MAPPED = "mapped"
    UNDER_REVIEW = "under_review"
    RULES_RUN = "rules_run"
    ISSUES_SENT = "issues_sent"
    READY_FOR_SIGNOFF = "ready_for_signoff"
    LIVE = "live"


class DictionaryStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class MappingStatus(StrEnum):
    SUGGESTED = "suggested"
    APPROVED = "approved"
    REJECTED = "rejected"


class RuleKind(StrEnum):
    BUILT_IN = "built_in"
    ANALYST = "analyst"


class RuleStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class Severity(StrEnum):
    BLOCKER = "blocker"
    WARNING = "warning"


class LetterStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"


class SignoffDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


def _in(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


# ---------------------------------------------------------------- helpers


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), nullable=False)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _fk(name: str, column: str, target: str, *, via: str | None = None) -> ForeignKeyConstraint:
    """``(tenant_id, [via,] column) -> target(tenant_id, [via,] id)``.

    No reference can cross tenants; with ``via`` it can't cross that parent either
    (e.g. a mapping's column must belong to the mapping's own file).
    """
    local = ["tenant_id", *([via] if via else []), column]
    remote = [f"{SCHEMA}.{target}.{c}" for c in ("tenant_id", *([via] if via else []), "id")]
    return ForeignKeyConstraint(local, remote, name=name, ondelete="RESTRICT")


# ---------------------------------------------------------------- tables


class Client(Base):
    """A health plan being onboarded."""

    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_clients_tenant_id"),
        UniqueConstraint("tenant_id", "code", name="uq_clients_tenant_code"),
        CheckConstraint(_in("status", ClientStatus), name="ck_clients_status"),
    )

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ClientStatus.ONBOARDING)
    go_live_target: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created_at()


class DataDictionary(Base):
    """Target fields for a client (or the tenant default when ``client_id`` is null)."""

    __tablename__ = "data_dictionaries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_data_dictionaries_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "client_id",
            "version",
            name="uq_data_dictionaries_version",
            postgresql_nulls_not_distinct=True,
        ),  # fmt: skip
        CheckConstraint(_in("status", DictionaryStatus), name="ck_data_dictionaries_status"),
        CheckConstraint("version >= 1", name="ck_data_dictionaries_version"),
        _fk("fk_data_dictionaries_client", "client_id", "clients"),
    )

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DictionaryStatus.DRAFT)
    # [{"name": "claim_id", "description": "...", "required": true}, ...]
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created_at()


class ClaimsFile(Base):
    """One file a client sent. Metadata only in Phase 1 (no contents are stored)."""

    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_files_tenant_id"),
        UniqueConstraint("tenant_id", "client_id", "id", name="uq_files_tenant_client_id"),
        UniqueConstraint("tenant_id", "client_id", "sequence", name="uq_files_sequence"),
        CheckConstraint(_in("status", FileStatus), name="ck_files_status"),
        CheckConstraint("sequence >= 1", name="ck_files_sequence"),
        CheckConstraint("size_bytes >= 0", name="ck_files_size"),
        CheckConstraint("row_count IS NULL OR row_count >= 0", name="ck_files_row_count"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_files_sha256"),
        CheckConstraint(
            "previous_file_id IS NULL OR previous_file_id <> id", name="ck_files_previous_not_self"
        ),  # fmt: skip
        _fk("fk_files_client", "client_id", "clients"),
        # a resubmission follows a file of the same client
        _fk("fk_files_previous", "previous_file_id", "files", via="client_id"),
        _fk("fk_files_dictionary", "dictionary_id", "data_dictionaries"),
        Index("ix_files_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    dictionary_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=FileStatus.RECEIVED)
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created_at()


class FileColumn(Base):
    """A column heading as the client wrote it."""

    __tablename__ = "file_columns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_file_columns_tenant_id"),
        UniqueConstraint("tenant_id", "file_id", "id", name="uq_file_columns_tenant_file_id"),
        UniqueConstraint("tenant_id", "file_id", "position", name="uq_file_columns_position"),
        CheckConstraint("position >= 0", name="ck_file_columns_position"),
        _fk("fk_file_columns_file", "file_id", "files"),
    )

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    header: Mapped[str] = mapped_column(String(300), nullable=False)


class ColumnMapping(Base):
    """A suggestion that a file column holds a target field, and the analyst's decision."""

    __tablename__ = "mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_mappings_tenant_id"),
        UniqueConstraint(
            "tenant_id", "file_column_id", "target_field", name="uq_mappings_column_field"
        ),  # fmt: skip
        CheckConstraint(_in("status", MappingStatus), name="ck_mappings_status"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_mappings_confidence",
        ),  # fmt: skip
        # who decided is recorded, and only for the decision that was made
        CheckConstraint(
            "(status = 'suggested' AND approved_by IS NULL AND rejected_by IS NULL"
            " AND decided_at IS NULL)"
            " OR (status = 'approved' AND approved_by IS NOT NULL AND rejected_by IS NULL"
            " AND decided_at IS NOT NULL)"
            " OR (status = 'rejected' AND rejected_by IS NOT NULL AND approved_by IS NULL"
            " AND decided_at IS NOT NULL)",
            name="ck_mappings_decision",
        ),
        _fk("fk_mappings_file", "file_id", "files"),
        # the column must belong to this mapping's file
        _fk("fk_mappings_file_column", "file_column_id", "file_columns", via="file_id"),
        Index("ix_mappings_file_status", "tenant_id", "file_id", "status"),
    )

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    file_column_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_field: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MappingStatus.SUGGESTED)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    model: Mapped[str | None] = mapped_column(String(200))  # null = suggested by a person
    suggested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class RuleDefinition(Base):
    """A versioned check on one target field. ``client_id`` null = tenant-wide."""

    __tablename__ = "rule_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rule_definitions_tenant_id"),
        UniqueConstraint("tenant_id", "rule_key", "version", name="uq_rule_definitions_version"),
        CheckConstraint(_in("kind", RuleKind), name="ck_rule_definitions_kind"),
        CheckConstraint(_in("status", RuleStatus), name="ck_rule_definitions_status"),
        CheckConstraint(_in("severity", Severity), name="ck_rule_definitions_severity"),
        CheckConstraint("version >= 1", name="ck_rule_definitions_version"),
        _fk("fk_rule_definitions_client", "client_id", "clients"),
    )

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rule_key: Mapped[str] = mapped_column(String(100), nullable=False)  # stable across versions
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target_field: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "exactly 10 digits"
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RuleStatus.DRAFT)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created_at()


class RuleResult(Base):
    """How many rows of a file failed one rule (examples are stored masked)."""

    __tablename__ = "rule_results"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rule_results_tenant_id"),
        UniqueConstraint("tenant_id", "file_id", "rule_definition_id", name="uq_rule_results_rule"),
        CheckConstraint(_in("severity", Severity), name="ck_rule_results_severity"),
        CheckConstraint("failed_rows >= 0", name="ck_rule_results_failed_rows"),
        _fk("fk_rule_results_file", "file_id", "files"),
        _fk("fk_rule_results_rule", "rule_definition_id", "rule_definitions"),
    )

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rule_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # copied from the rule
    failed_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    examples: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = _created_at()


class IssueLetter(Base):
    """A letter to the client listing what to fix in a file."""

    __tablename__ = "issue_letters"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_issue_letters_tenant_id"),
        CheckConstraint(_in("status", LetterStatus), name="ck_issue_letters_status"),
        CheckConstraint(
            "(status = 'sent') = (sent_by IS NOT NULL AND sent_at IS NOT NULL)",
            name="ck_issue_letters_sent",
        ),  # fmt: skip
        _fk("fk_issue_letters_file", "file_id", "files"),
    )

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=LetterStatus.DRAFT)
    body: Mapped[str] = mapped_column(Text, nullable=False)  # stored masked
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sent_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class GoLiveSignoff(Base):
    """A manager's go-live decision on a file (four-eyes: never the mapping approver)."""

    __tablename__ = "go_live_signoffs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_go_live_signoffs_tenant_id"),
        CheckConstraint(_in("decision", SignoffDecision), name="ck_go_live_signoffs_decision"),
        _fk("fk_go_live_signoffs_file", "file_id", "files"),
        # at most one approval per file
        Index(
            "uq_go_live_signoffs_one_approval",
            "tenant_id",
            "file_id",
            unique=True,
            postgresql_where="decision = 'approved'",
        ),  # fmt: skip
    )

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    signed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    signed_at: Mapped[datetime] = _created_at()
