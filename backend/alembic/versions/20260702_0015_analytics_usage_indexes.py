"""Add usage record indexes for analytics aggregations."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260702_0015"
down_revision = "20260702_0014"
branch_labels = None
depends_on = None

_TABLE = "usage_records"
_STATUS_CREATED_INDEX = "ix_usage_records_status_created_at"
_TENANT_STATUS_CREATED_INDEX = "ix_usage_records_tenant_status_created_at"


def _index_exists(index_name: str) -> bool:
    indexes = sa.inspect(op.get_bind()).get_indexes(_TABLE)
    return any(index["name"] == index_name for index in indexes)


def upgrade() -> None:
    if not _index_exists(_STATUS_CREATED_INDEX):
        op.create_index(_STATUS_CREATED_INDEX, _TABLE, ["status", "created_at"])
    if not _index_exists(_TENANT_STATUS_CREATED_INDEX):
        op.create_index(
            _TENANT_STATUS_CREATED_INDEX,
            _TABLE,
            ["tenant_id", "status", "created_at"],
        )


def downgrade() -> None:
    if _index_exists(_TENANT_STATUS_CREATED_INDEX):
        op.drop_index(_TENANT_STATUS_CREATED_INDEX, table_name=_TABLE)
    if _index_exists(_STATUS_CREATED_INDEX):
        op.drop_index(_STATUS_CREATED_INDEX, table_name=_TABLE)
