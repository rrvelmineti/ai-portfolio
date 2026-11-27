"""approval_requests: human approval for risky AI actions.

Revision ID: 0001
Revises:
Create Date: 2026-11-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

S = "guardrails"


def upgrade() -> None:
    status = pg.ENUM("pending", "approved", "rejected", "expired", name="approval_status",
                     schema=S, create_type=False)  # fmt: skip
    status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "approval_requests",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("payload_summary", sa.Text, nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("decided_by", pg.UUID(as_uuid=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.Text),
        sa.Column("request_id", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="approval_requests_pkey"),
        schema=S,
    )  # fmt: skip
    op.create_index("ix_approval_requests_tenant_status", "approval_requests",
                    ["tenant_id", "status"], schema=S)  # fmt: skip
    op.create_index("ix_approval_requests_requester", "approval_requests", ["requester_id"],
                    schema=S)  # fmt: skip


def downgrade() -> None:
    op.drop_table("approval_requests", schema=S)
    pg.ENUM(name="approval_status", schema=S).drop(op.get_bind(), checkfirst=True)
