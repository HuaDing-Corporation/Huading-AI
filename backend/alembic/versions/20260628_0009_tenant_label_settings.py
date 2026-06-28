"""Add tenant synthetic label settings."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260628_0009"
down_revision = "20260628_0008"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    if _table_exists("tenant_label_settings"):
        return
    op.create_table(
        "tenant_label_settings",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.String(length=2), nullable=False, server_default="br"),
        sa.Column("text", sa.String(length=20), nullable=False, server_default="AI 生成"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "position IN ('br', 'bl', 'tr', 'tl', 'bc')",
            name="ck_tenant_label_settings_position",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )


def downgrade() -> None:
    if _table_exists("tenant_label_settings"):
        op.drop_table("tenant_label_settings")
