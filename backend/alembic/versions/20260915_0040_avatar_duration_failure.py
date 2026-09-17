"""Bounded, explicitly accepted HeyGen duration failure charges."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20260915_0040"
down_revision = "20260915_0039"
branch_labels = None
depends_on = None

_OLD_STATE = """(
 status = 'in_progress' AND completion_kind IS NULL AND completed_at IS NULL
 AND settled_credits = 0 AND released_credits = 0
) OR (
 status = 'completed' AND completion_kind IN ('succeeded', 'failed', 'rejected')
 AND completed_at IS NOT NULL AND settled_credits + released_credits = requested_credits
)"""
_STATE = _OLD_STATE.replace("'rejected')", "'rejected', 'failed_charged')")
_CHARGED = """
completion_kind IS NULL OR completion_kind <> 'failed_charged' OR
(
 operation = 'video_create' AND duration_policy_version = '145s-no-refund-v1'
 AND error_code = 'HEYGEN_AUDIO_DURATION_EXCEEDED' AND error_http_status = 422
 AND result_type = 'video_task' AND result_id IS NOT NULL
 AND result_payload IS NOT NULL AND error_payload IS NOT NULL
 AND settled_credits = requested_credits AND released_credits = 0
) IS TRUE
"""


def upgrade():
    with op.batch_alter_table("billing_operations") as batch:
        batch.add_column(sa.Column("duration_policy_version", sa.String(64), nullable=True))
        batch.drop_constraint("ck_billing_operations_state", type_="check")
        batch.create_check_constraint("ck_billing_operations_state", f"({_STATE}) IS TRUE")
        batch.create_check_constraint("ck_billing_operations_failed_charged", _CHARGED)
    op.add_column(
        "usage_records",
        sa.Column(
            "duration_failure_evidence",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
        ),
    )


def downgrade():
    connection = op.get_bind()
    if (
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM billing_operations WHERE duration_policy_version IS NOT NULL"
            )
        )
        or connection.scalar(
            sa.text(
                "SELECT count(*) FROM usage_records WHERE duration_failure_evidence IS NOT NULL"
            )
        )
        or connection.scalar(
            sa.text(
                "SELECT count(*) FROM video_tasks "
                "WHERE params ->> 'avatar_duration_acceptance' IS NOT NULL"
            )
        )
    ):
        raise RuntimeError(
            "Accepted avatar duration policies exist; downgrade would erase financial evidence."
        )
    op.drop_column("usage_records", "duration_failure_evidence")
    with op.batch_alter_table("billing_operations") as batch:
        batch.drop_constraint("ck_billing_operations_failed_charged", type_="check")
        batch.drop_constraint("ck_billing_operations_state", type_="check")
        batch.create_check_constraint("ck_billing_operations_state", f"({_OLD_STATE}) IS TRUE")
        batch.drop_column("duration_policy_version")
