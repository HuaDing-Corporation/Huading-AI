"""video storage metadata

Revision ID: 20260610_0002
Revises: 20260609_0001
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260610_0002"
down_revision: str | None = "20260609_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("video_tasks", sa.Column("video_mode", sa.String(length=32), nullable=True))
    op.add_column("video_tasks", sa.Column("progress", sa.Integer(), nullable=True))
    op.add_column("video_tasks", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("video_tasks", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("video_tasks", sa.Column("storage_bucket", sa.String(length=255), nullable=True))
    op.add_column("video_tasks", sa.Column("storage_key", sa.String(length=500), nullable=True))
    op.add_column("video_tasks", sa.Column("thumbnail_key", sa.String(length=500), nullable=True))
    op.add_column("video_tasks", sa.Column("content_type", sa.String(length=100), nullable=True))
    op.add_column("video_tasks", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("video_tasks", sa.Column("duration_sec", sa.Float(), nullable=True))
    op.add_column("video_tasks", sa.Column("local_path", sa.String(length=1000), nullable=True))
    op.execute("UPDATE video_tasks SET video_mode = 'static_template' WHERE video_mode IS NULL")
    op.execute("UPDATE video_tasks SET progress = 0 WHERE progress IS NULL")
    op.execute("UPDATE video_tasks SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")


def downgrade() -> None:
    op.drop_column("video_tasks", "local_path")
    op.drop_column("video_tasks", "duration_sec")
    op.drop_column("video_tasks", "size_bytes")
    op.drop_column("video_tasks", "content_type")
    op.drop_column("video_tasks", "thumbnail_key")
    op.drop_column("video_tasks", "storage_key")
    op.drop_column("video_tasks", "storage_bucket")
    op.drop_column("video_tasks", "created_at")
    op.drop_column("video_tasks", "error")
    op.drop_column("video_tasks", "progress")
    op.drop_column("video_tasks", "video_mode")
