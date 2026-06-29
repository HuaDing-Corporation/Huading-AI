"""Add publish records."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260629_0010"
down_revision = "20260628_0009"
branch_labels = None
depends_on = None

_TABLE = "publish_records"
_TENANT_CREATED_INDEX = "ix_publish_records_tenant_created_at"
_TENANT_SOURCE_INDEX = "ix_publish_records_tenant_source"


def _json_type() -> sa.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    indexes = sa.inspect(op.get_bind()).get_indexes(table_name)
    return any(index["name"] == index_name for index in indexes)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
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
            sa.Column("source_kind", sa.String(length=16), nullable=False),
            sa.Column("source_task_id", sa.String(length=36), nullable=False),
            sa.Column("items", _json_type(), nullable=False),
            sa.Column("platforms", _json_type(), nullable=False),
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
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "source_kind IN ('video', 'image')",
                name="ck_publish_records_source_kind",
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["source_task_id"], ["video_tasks.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if _table_exists(_TABLE) and not _index_exists(_TABLE, _TENANT_CREATED_INDEX):
        op.create_index(_TENANT_CREATED_INDEX, _TABLE, ["tenant_id", "created_at"])
    if _table_exists(_TABLE) and not _index_exists(_TABLE, _TENANT_SOURCE_INDEX):
        op.create_index(_TENANT_SOURCE_INDEX, _TABLE, ["tenant_id", "source_task_id"])


def downgrade() -> None:
    if _table_exists(_TABLE):
        if _index_exists(_TABLE, _TENANT_SOURCE_INDEX):
            op.drop_index(_TENANT_SOURCE_INDEX, table_name=_TABLE)
        if _index_exists(_TABLE, _TENANT_CREATED_INDEX):
            op.drop_index(_TENANT_CREATED_INDEX, table_name=_TABLE)
        op.drop_table(_TABLE)
