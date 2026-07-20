"""Allow video generation tasks to link reference-video assets."""

from __future__ import annotations

from alembic import op

revision = "20260721_0030"
down_revision = "20260719_0029"
branch_labels = None
depends_on = None

_ROLE_CHECK_WITH_REFERENCE_VIDEO = (
    "role IN ('input_avatar', 'output_audio', 'output_subtitle', 'output_video', "
    "'output_image', 'input_reference_image', 'input_reference_video', 'input_bgm')"
)
_ROLE_CHECK_LEGACY = (
    "role IN ('input_avatar', 'output_audio', 'output_subtitle', 'output_video', "
    "'output_image', 'input_reference_image', 'input_bgm')"
)


def _replace_role_check(condition: str) -> None:
    with op.batch_alter_table("task_assets") as batch_op:
        batch_op.drop_constraint("ck_task_assets_role", type_="check")
        batch_op.create_check_constraint("ck_task_assets_role", condition)


def upgrade() -> None:
    _replace_role_check(_ROLE_CHECK_WITH_REFERENCE_VIDEO)


def downgrade() -> None:
    _replace_role_check(_ROLE_CHECK_LEGACY)
