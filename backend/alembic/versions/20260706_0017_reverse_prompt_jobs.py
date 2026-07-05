"""Add reverse prompt jobs and provider capability."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260706_0017"
down_revision = "20260704_0016"
branch_labels = None
depends_on = None

_PROVIDER_CAPABILITY_WITH_REVERSE = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'reverse_prompt')"
)
_PROVIDER_CAPABILITY_LEGACY = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone')"
)
_USAGE_CAPABILITY_WITH_REVERSE = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt')"
)
_USAGE_CAPABILITY_LEGACY = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen')"
)
_CREDIT_RATE_CAPABILITY_WITH_REVERSE = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt')"
)
_CREDIT_RATE_CAPABILITY_LEGACY = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen')"
)
_PROVIDER_ID = "reverse-prompt-apimart-gemini"
_CREDIT_RATE_ID = "reverse-prompt-call-rate"


def upgrade() -> None:
    op.create_table(
        "reverse_prompt_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_kind", sa.String(length=16), nullable=False, server_default="image"),
        sa.Column(
            "source_asset_id",
            sa.String(length=36),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_video_task_id",
            sa.String(length=36),
            sa.ForeignKey("video_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_storage_key", sa.String(length=500), nullable=True),
        sa.Column(
            "target_format",
            sa.String(length=32),
            nullable=False,
            server_default="seedance_2_0",
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("raw_model_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credits", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source_kind IN ('image', 'video')",
            name="ck_reverse_prompt_jobs_source_kind",
        ),
        sa.CheckConstraint(
            "target_format IN ('seedance_2_0')",
            name="ck_reverse_prompt_jobs_target_format",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'saved')",
            name="ck_reverse_prompt_jobs_status",
        ),
    )
    op.create_index(
        "ix_reverse_prompt_jobs_tenant_created_at",
        "reverse_prompt_jobs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_reverse_prompt_jobs_tenant_status",
        "reverse_prompt_jobs",
        ["tenant_id", "status"],
    )

    with op.batch_alter_table("provider_configs") as batch_op:
        batch_op.drop_constraint("ck_provider_configs_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_provider_configs_capability",
            _PROVIDER_CAPABILITY_WITH_REVERSE,
        )
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("ck_usage_records_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_usage_records_capability",
            _USAGE_CAPABILITY_WITH_REVERSE,
        )
    with op.batch_alter_table("credit_rates") as batch_op:
        batch_op.drop_constraint("ck_credit_rates_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_rates_capability",
            _CREDIT_RATE_CAPABILITY_WITH_REVERSE,
        )

    provider_configs = sa.table(
        "provider_configs",
        sa.column("id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("capability", sa.String),
        sa.column("provider", sa.String),
        sa.column("config", sa.JSON),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        provider_configs,
        [
            {
                "id": _PROVIDER_ID,
                "tenant_id": None,
                "capability": "reverse_prompt",
                "provider": "apimart-gemini",
                "config": {},
                "is_active": True,
            }
        ],
    )
    credit_rates = sa.table(
        "credit_rates",
        sa.column("id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("capability", sa.String),
        sa.column("unit", sa.String),
        sa.column("credits_per_unit", sa.Numeric),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        credit_rates,
        [
            {
                "id": _CREDIT_RATE_ID,
                "tenant_id": None,
                "capability": "reverse_prompt",
                "unit": "call",
                "credits_per_unit": 1,
                "is_active": True,
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM credit_rates WHERE id = :rate_id").bindparams(
            rate_id=_CREDIT_RATE_ID
        )
    )
    op.execute(
        sa.text("DELETE FROM provider_configs WHERE id = :provider_id").bindparams(
            provider_id=_PROVIDER_ID
        )
    )
    with op.batch_alter_table("credit_rates") as batch_op:
        batch_op.drop_constraint("ck_credit_rates_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_rates_capability",
            _CREDIT_RATE_CAPABILITY_LEGACY,
        )
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("ck_usage_records_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_usage_records_capability",
            _USAGE_CAPABILITY_LEGACY,
        )
    with op.batch_alter_table("provider_configs") as batch_op:
        batch_op.drop_constraint("ck_provider_configs_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_provider_configs_capability",
            _PROVIDER_CAPABILITY_LEGACY,
        )
    op.drop_index("ix_reverse_prompt_jobs_tenant_status", table_name="reverse_prompt_jobs")
    op.drop_index("ix_reverse_prompt_jobs_tenant_created_at", table_name="reverse_prompt_jobs")
    op.drop_table("reverse_prompt_jobs")
