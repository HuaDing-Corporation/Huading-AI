"""Persist AIBrain cooldowns once per real user."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260805_0034"
down_revision: str | None = "20260805_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aibrain_user_cooldowns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column(
            "source_message_id",
            sa.String(length=36),
            sa.ForeignKey("chat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            name="uq_aibrain_user_cooldowns_user_id",
        ),
    )
    op.create_index(
        "ix_aibrain_user_cooldowns_tenant_id",
        "aibrain_user_cooldowns",
        ["tenant_id"],
    )
    op.create_index(
        "ix_aibrain_user_cooldowns_tenant_expires",
        "aibrain_user_cooldowns",
        ["tenant_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_aibrain_user_cooldowns_tenant_expires",
        table_name="aibrain_user_cooldowns",
    )
    op.drop_index(
        "ix_aibrain_user_cooldowns_tenant_id",
        table_name="aibrain_user_cooldowns",
    )
    op.drop_table("aibrain_user_cooldowns")
