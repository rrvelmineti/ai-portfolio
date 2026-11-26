"""Initial auth schema: tenants, users, refresh tokens, append-only audit log.

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

S = "auth"


def upgrade() -> None:
    role = pg.ENUM("admin", "analyst", "viewer", name="role", schema=S, create_type=False)
    role.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "tenants",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
        schema=S,
    )  # fmt: skip
    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", role, nullable=False),
        sa.Column("is_superadmin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("failed_logins", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{S}.tenants.id"],
                                name="fk_users_tenant_id_tenants", ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "username", name="uq_users_tenant_id_username"),
        schema=S,
    )  # fmt: skip
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"], schema=S)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("replaced_by", pg.UUID(as_uuid=True)),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.ForeignKeyConstraint(["user_id"], [f"{S}.users.id"],
                                name="fk_refresh_tokens_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["replaced_by"], [f"{S}.refresh_tokens.id"],
                                name="fk_refresh_tokens_replaced_by_refresh_tokens",
                                ondelete="SET NULL"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        schema=S,
    )  # fmt: skip
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], schema=S)
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"], schema=S)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True)),
        sa.Column("user_id", pg.UUID(as_uuid=True)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100)),
        sa.Column("resource_id", sa.String(200)),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("request_id", sa.String(100)),
        sa.Column("details", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        schema=S,
    )  # fmt: skip
    op.create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"],
                    schema=S)  # fmt: skip
    op.create_index("ix_audit_log_request_id", "audit_log", ["request_id"], schema=S)

    # Append-only: block UPDATE, DELETE and TRUNCATE at the database level.
    op.execute(
        f"""
        CREATE FUNCTION {S}.audit_log_block_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER audit_log_no_update_delete
        BEFORE UPDATE OR DELETE ON {S}.audit_log
        FOR EACH ROW EXECUTE FUNCTION {S}.audit_log_block_mutation();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER audit_log_no_truncate
        BEFORE TRUNCATE ON {S}.audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION {S}.audit_log_block_mutation();
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS audit_log_no_truncate ON {S}.audit_log")
    op.execute(f"DROP TRIGGER IF EXISTS audit_log_no_update_delete ON {S}.audit_log")
    op.execute(f"DROP FUNCTION IF EXISTS {S}.audit_log_block_mutation()")
    op.drop_table("audit_log", schema=S)
    op.drop_table("refresh_tokens", schema=S)
    op.drop_table("users", schema=S)
    op.drop_table("tenants", schema=S)
    pg.ENUM(name="role", schema=S).drop(op.get_bind(), checkfirst=True)
