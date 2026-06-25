"""Add copy draft history."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260625_0007"
down_revision = "20260624_0006"
branch_labels = None
depends_on = None

_TABLE = "copy_drafts"
_INDEX = "ix_copy_drafts_tenant_created_at"


def _json_type() -> sa.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    indexes = sa.inspect(op.get_bind()).get_indexes(table_name)
    return any(index["name"] == index_name for index in indexes)


def upgrade() -> None:
    # PostgreSQL smoke environments need pgcrypto for server-side UUID strings.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Store copywriting history separately from video_tasks so synchronous text
    # drafts do not pollute long-running video pipeline semantics.
    if not _table_exists(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column(
                "id",
                sa.String(length=36),
                nullable=False,
                server_default=sa.text("gen_random_uuid()::text"),
            ),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("source_text", sa.Text(), nullable=False),
            sa.Column("result_text", sa.Text(), nullable=False),
            sa.Column("titles", _json_type(), nullable=True),
            sa.Column("topics", _json_type(), nullable=True),
            sa.Column("mode", sa.String(length=16), nullable=False),
            sa.Column("target_platform", sa.String(length=32), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "mode IN ('smart', 'custom', 'auto')",
                name="ck_copy_drafts_mode",
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    # Tenant + created_at index backs the history tab list query.
    if _table_exists(_TABLE) and not _index_exists(_TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, ["tenant_id", "created_at"])


def downgrade() -> None:
    # Drop the list-query index before dropping the table; guards keep local
    # replay/downgrade attempts idempotent.
    if _table_exists(_TABLE):
        if _index_exists(_TABLE, _INDEX):
            op.drop_index(_INDEX, table_name=_TABLE)
        op.drop_table(_TABLE)
