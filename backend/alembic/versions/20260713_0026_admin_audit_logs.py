"""Add append-only platform admin audit logs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260713_0026"
down_revision: str | None = "20260712_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("actor_tenant_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target_tenant_id", sa.String(length=36), nullable=True),
        sa.Column("target_id", sa.String(length=36), nullable=True),
        sa.Column(
            "before",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "after",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('credits_adjust', 'plan_change', 'status_change', "
            "'voice_slot_assign', 'task_retry')",
            name="ck_admin_audit_logs_action",
        ),
        sa.ForeignKeyConstraint(["actor_tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_audit_logs_created_at",
        "admin_audit_logs",
        [sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_admin_audit_logs_target_tenant_created_at",
        "admin_audit_logs",
        ["target_tenant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_audit_logs_target_tenant_created_at",
        table_name="admin_audit_logs",
    )
    op.drop_index("ix_admin_audit_logs_created_at", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
