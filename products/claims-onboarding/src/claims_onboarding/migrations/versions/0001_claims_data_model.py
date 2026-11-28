"""Claims onboarding data model: clients, dictionaries, files, columns, mappings, rules,
rule results, issue letters and go-live sign-offs (ADR 0009).

Every table carries tenant_id, and children reference parents by composite
(tenant_id, ..., id) foreign keys so no row can point into another tenant.

Revision ID: 0001
Revises:
Create Date: 2026-11-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("go_live_target", sa.Date(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('onboarding', 'live', 'on_hold')", name="ck_clients_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_clients_tenant_code"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_clients_tenant_id"),
        schema="claims",
    )
    op.create_table(
        "data_dictionaries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'superseded')", name="ck_data_dictionaries_status"
        ),
        sa.CheckConstraint("version >= 1", name="ck_data_dictionaries_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["claims.clients.tenant_id", "claims.clients.id"],
            name="fk_data_dictionaries_client",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "client_id",
            "version",
            name="uq_data_dictionaries_version",
            postgresql_nulls_not_distinct=True,
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_data_dictionaries_tenant_id"),
        schema="claims",
    )
    op.create_table(
        "rule_definitions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("rule_key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("target_field", sa.String(length=100), nullable=False),
        sa.Column("rule_text", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('built_in', 'analyst')", name="ck_rule_definitions_kind"),
        sa.CheckConstraint(
            "severity IN ('blocker', 'warning')", name="ck_rule_definitions_severity"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')", name="ck_rule_definitions_status"
        ),
        sa.CheckConstraint("version >= 1", name="ck_rule_definitions_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["claims.clients.tenant_id", "claims.clients.id"],
            name="fk_rule_definitions_client",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_rule_definitions_tenant_id"),
        sa.UniqueConstraint("tenant_id", "rule_key", "version", name="uq_rule_definitions_version"),
        schema="claims",
    )
    op.create_table(
        "files",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("previous_file_id", sa.UUID(), nullable=True),
        sa.Column("dictionary_id", sa.UUID(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_files_sha256"),
        sa.CheckConstraint(
            "status IN ('received', 'masked', 'mapped', 'under_review', 'rules_run', "
            "'issues_sent', 'ready_for_signoff', 'live')",
            name="ck_files_status",
        ),
        sa.CheckConstraint(
            "previous_file_id IS NULL OR previous_file_id <> id", name="ck_files_previous_not_self"
        ),
        sa.CheckConstraint("row_count IS NULL OR row_count >= 0", name="ck_files_row_count"),
        sa.CheckConstraint("sequence >= 1", name="ck_files_sequence"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_files_size"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id", "previous_file_id"],
            ["claims.files.tenant_id", "claims.files.client_id", "claims.files.id"],
            name="fk_files_previous",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["claims.clients.tenant_id", "claims.clients.id"],
            name="fk_files_client",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dictionary_id"],
            ["claims.data_dictionaries.tenant_id", "claims.data_dictionaries.id"],
            name="fk_files_dictionary",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "client_id", "id", name="uq_files_tenant_client_id"),
        sa.UniqueConstraint("tenant_id", "client_id", "sequence", name="uq_files_sequence"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_files_tenant_id"),
        schema="claims",
    )
    op.create_index(
        "ix_files_tenant_status", "files", ["tenant_id", "status"], unique=False, schema="claims"
    )
    op.create_table(
        "file_columns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("header", sa.String(length=300), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_file_columns_position"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "file_id"],
            ["claims.files.tenant_id", "claims.files.id"],
            name="fk_file_columns_file",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "file_id", "id", name="uq_file_columns_tenant_file_id"),
        sa.UniqueConstraint("tenant_id", "file_id", "position", name="uq_file_columns_position"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_file_columns_tenant_id"),
        schema="claims",
    )
    op.create_table(
        "go_live_signoffs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("signed_by", sa.UUID(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column(
            "signed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name="ck_go_live_signoffs_decision"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "file_id"],
            ["claims.files.tenant_id", "claims.files.id"],
            name="fk_go_live_signoffs_file",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_go_live_signoffs_tenant_id"),
        schema="claims",
    )
    op.create_index(
        "uq_go_live_signoffs_one_approval",
        "go_live_signoffs",
        ["tenant_id", "file_id"],
        unique=True,
        schema="claims",
        postgresql_where="decision = 'approved'",
    )
    op.create_table(
        "issue_letters",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("sent_by", sa.UUID(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'sent') = (sent_by IS NOT NULL AND sent_at IS NOT NULL)",
            name="ck_issue_letters_sent",
        ),
        sa.CheckConstraint("status IN ('draft', 'sent')", name="ck_issue_letters_status"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "file_id"],
            ["claims.files.tenant_id", "claims.files.id"],
            name="fk_issue_letters_file",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_issue_letters_tenant_id"),
        schema="claims",
    )
    op.create_table(
        "rule_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("rule_definition_id", sa.UUID(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("failed_rows", sa.Integer(), nullable=False),
        sa.Column("examples", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("severity IN ('blocker', 'warning')", name="ck_rule_results_severity"),
        sa.CheckConstraint("failed_rows >= 0", name="ck_rule_results_failed_rows"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "file_id"],
            ["claims.files.tenant_id", "claims.files.id"],
            name="fk_rule_results_file",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_definition_id"],
            ["claims.rule_definitions.tenant_id", "claims.rule_definitions.id"],
            name="fk_rule_results_rule",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "file_id", "rule_definition_id", name="uq_rule_results_rule"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_rule_results_tenant_id"),
        schema="claims",
    )
    op.create_table(
        "mappings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("file_column_id", sa.UUID(), nullable=False),
        sa.Column("target_field", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("suggested_by", sa.UUID(), nullable=True),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column("rejected_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'suggested' AND approved_by IS NULL AND rejected_by IS NULL"
            " AND decided_at IS NULL)"
            " OR (status = 'approved' AND approved_by IS NOT NULL AND rejected_by IS NULL"
            " AND decided_at IS NOT NULL)"
            " OR (status = 'rejected' AND rejected_by IS NOT NULL AND approved_by IS NULL"
            " AND decided_at IS NOT NULL)",
            name="ck_mappings_decision",
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'approved', 'rejected')", name="ck_mappings_status"
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_mappings_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "file_id", "file_column_id"],
            [
                "claims.file_columns.tenant_id",
                "claims.file_columns.file_id",
                "claims.file_columns.id",
            ],
            name="fk_mappings_file_column",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "file_id"],
            ["claims.files.tenant_id", "claims.files.id"],
            name="fk_mappings_file",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "file_column_id", "target_field", name="uq_mappings_column_field"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_mappings_tenant_id"),
        schema="claims",
    )
    op.create_index(
        "ix_mappings_file_status",
        "mappings",
        ["tenant_id", "file_id", "status"],
        unique=False,
        schema="claims",
    )


def downgrade() -> None:
    op.drop_index("ix_mappings_file_status", table_name="mappings", schema="claims")
    op.drop_table("mappings", schema="claims")
    op.drop_table("rule_results", schema="claims")
    op.drop_table("issue_letters", schema="claims")
    op.drop_index(
        "uq_go_live_signoffs_one_approval",
        table_name="go_live_signoffs",
        schema="claims",
        postgresql_where="decision = 'approved'",
    )
    op.drop_table("go_live_signoffs", schema="claims")
    op.drop_table("file_columns", schema="claims")
    op.drop_index("ix_files_tenant_status", table_name="files", schema="claims")
    op.drop_table("files", schema="claims")
    op.drop_table("rule_definitions", schema="claims")
    op.drop_table("data_dictionaries", schema="claims")
    op.drop_table("clients", schema="claims")
