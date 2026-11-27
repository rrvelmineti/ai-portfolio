"""Audit log retention: the table owner may delete rows older than 7 days (ADR 0008).

The audit log stays append-only for everyone else. A DELETE now passes the trigger only
when ALL of these hold:

1. the current role is the table's owner (the migrations/owner role, never the app role,
   which also has no DELETE privilege at all);
2. the transaction has set ``ai_portfolio.audit_retention = 'on'`` (the retention job
   does this with ``SET LOCAL``, so an ordinary owner session can't delete by accident);
3. the row is older than 7 days.

UPDATE and TRUNCATE remain blocked for every role.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

S = "auth"
RETENTION_DAYS = 7


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {S}.audit_log_block_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('ai_portfolio.audit_retention', true) = 'on'
               AND current_user = (SELECT pg_get_userbyid(relowner)
                                   FROM pg_class WHERE oid = TG_RELID)
               AND OLD.created_at < now() - interval '{RETENTION_DAYS} days'
            THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {S}.audit_log_block_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$;
        """
    )
