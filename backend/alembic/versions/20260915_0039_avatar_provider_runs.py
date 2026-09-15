"""Durable HeyGen avatar checkpoints and fenced leases."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20260915_0039"
down_revision = "20260829_0038"
branch_labels = None
depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "avatar_provider_runs",
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("model", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("owner", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(64)),
        sa.Column("request_body", json_type),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("input_expires_at", sa.DateTime(timezone=True)),
        sa.Column("provider_job_id", sa.String(128)),
        sa.Column("checkpoint", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "next_check_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
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
        sa.ForeignKeyConstraint(["task_id"], ["video_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "model IN ('avatar_iv', 'lipsync_precision')", name="ck_avatar_provider_runs_model"
        ),
        sa.CheckConstraint(
            "state IN ('ready', 'active', 'pending', 'review', 'completed', 'failed')",
            name="ck_avatar_provider_runs_state",
        ),
    )
    op.create_index("ix_avatar_provider_runs_tenant_id", "avatar_provider_runs", ["tenant_id"])
    op.create_index(
        "ix_avatar_provider_runs_due", "avatar_provider_runs", ["state", "next_check_at"]
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM avatar_provider_runs")):
        raise RuntimeError("Avatar checkpoints exist; reconcile/archive before downgrade.")
    op.drop_table("avatar_provider_runs")
