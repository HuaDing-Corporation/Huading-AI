"""Add soft deletion to e-commerce replicate history."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260724_0031"
down_revision = "20260721_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ecom_replicate_jobs",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ecom_replicate_jobs", "deleted_at")
