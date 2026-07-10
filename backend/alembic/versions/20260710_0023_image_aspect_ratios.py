"""Allow image aspect ratios on photo tasks."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260710_0023"
down_revision = "20260706_0022"
branch_labels = None
depends_on = None

_TABLE = "video_tasks"
_CONSTRAINT = "ck_video_tasks_aspect_ratio"
_IMAGE_RATIOS = (
    "aspect_ratio IN ('1:1', '4:3', '3:2', '16:9', '21:9', "
    "'3:4', '2:3', '9:16', 'auto')"
)
_VIDEO_RATIOS = "aspect_ratio IN ('9:16', '16:9', '1:1')"


def _replace_check(condition: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_constraint(_CONSTRAINT, type_="check")
            batch_op.create_check_constraint(_CONSTRAINT, condition)
        return
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, condition)


def upgrade() -> None:
    _replace_check(_IMAGE_RATIOS)


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE video_tasks SET aspect_ratio = '1:1' "
            "WHERE aspect_ratio NOT IN ('9:16', '16:9', '1:1')"
        )
    )
    _replace_check(_VIDEO_RATIOS)
