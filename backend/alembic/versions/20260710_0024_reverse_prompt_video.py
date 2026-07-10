"""Add asynchronous video reverse-prompt billing linkage."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260710_0024"
down_revision = "20260710_0023"
branch_labels = None
depends_on = None

_CREDIT_RATE_CAPABILITY_WITH_VIDEO_REVERSE = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt', "
    "'reverse_prompt_video')"
)
_CREDIT_RATE_CAPABILITY_LEGACY = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt')"
)
_USAGE_CAPABILITY_WITH_VIDEO_REVERSE = _CREDIT_RATE_CAPABILITY_WITH_VIDEO_REVERSE
_USAGE_CAPABILITY_LEGACY = _CREDIT_RATE_CAPABILITY_LEGACY
_USAGE_JOB_FK = "fk_usage_records_reverse_prompt_job_id"
_USAGE_JOB_INDEX = "ix_usage_records_reverse_prompt_status"


def upgrade() -> None:
    with op.batch_alter_table("credit_rates") as batch_op:
        batch_op.drop_constraint("ck_credit_rates_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_rates_capability",
            _CREDIT_RATE_CAPABILITY_WITH_VIDEO_REVERSE,
        )

    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("ck_usage_records_capability", type_="check")
        batch_op.add_column(sa.Column("reverse_prompt_job_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            _USAGE_JOB_FK,
            "reverse_prompt_jobs",
            ["reverse_prompt_job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_usage_records_capability",
            _USAGE_CAPABILITY_WITH_VIDEO_REVERSE,
        )
    op.create_index(
        _USAGE_JOB_INDEX,
        "usage_records",
        ["reverse_prompt_job_id", "status"],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE usage_records SET capability = 'reverse_prompt' "
            "WHERE capability = 'reverse_prompt_video'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE credit_rates SET capability = 'reverse_prompt' "
            "WHERE capability = 'reverse_prompt_video'"
        )
    )
    op.drop_index(_USAGE_JOB_INDEX, table_name="usage_records")
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("ck_usage_records_capability", type_="check")
        batch_op.drop_constraint(_USAGE_JOB_FK, type_="foreignkey")
        batch_op.drop_column("reverse_prompt_job_id")
        batch_op.create_check_constraint(
            "ck_usage_records_capability",
            _USAGE_CAPABILITY_LEGACY,
        )
    with op.batch_alter_table("credit_rates") as batch_op:
        batch_op.drop_constraint("ck_credit_rates_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_rates_capability",
            _CREDIT_RATE_CAPABILITY_LEGACY,
        )
