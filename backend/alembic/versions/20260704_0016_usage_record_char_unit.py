"""Allow character units for provider cost usage records."""

from __future__ import annotations

from alembic import op

revision = "20260704_0016"
down_revision = "20260702_0015"
branch_labels = None
depends_on = None

_TABLE = "usage_records"
_CONSTRAINT = "ck_usage_records_unit"
_WITH_CHAR = "unit IN ('second', 'call', 'token', 'image', 'char')"
_WITHOUT_CHAR = "unit IN ('second', 'call', 'token', 'image')"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT, _WITH_CHAR)


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT, _WITHOUT_CHAR)
