"""llm_calls: one row per AI call (no prompt or response text).

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

S = "observability"


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True)),
        sa.Column("user_id", pg.UUID(as_uuid=True)),
        sa.Column("product", sa.String(100)),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("alias", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(50)),
        sa.Column("model", sa.String(200)),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("fallback_used", sa.Boolean, nullable=False),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("cost_usd", sa.Numeric(14, 8)),
        sa.Column("cost_unknown", sa.Boolean, nullable=False),
        sa.Column("latency_ms", sa.Float, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error_type", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="llm_calls_pkey"),
        schema=S,
    )  # fmt: skip
    op.create_index("ix_llm_calls_request_id", "llm_calls", ["request_id"], schema=S)
    op.create_index("ix_llm_calls_product_created", "llm_calls", ["product", "created_at"],
                    schema=S)  # fmt: skip
    op.create_index("ix_llm_calls_provider_model_created", "llm_calls",
                    ["provider", "model", "created_at"], schema=S)  # fmt: skip


def downgrade() -> None:
    op.drop_table("llm_calls", schema=S)
