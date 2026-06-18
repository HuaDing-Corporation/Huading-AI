"""db schema 0001 digital human baseline

Revision ID: 20260616_0003
Revises: 20260610_0002
Create Date: 2026-06-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260616_0003"
down_revision: str | None = "20260610_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column[str]:
    return sa.Column(
        "id",
        sa.String(length=36),
        nullable=False,
        server_default=sa.text("gen_random_uuid()::text"),
    )


def _created_at_column() -> sa.Column[object]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def _updated_at_column() -> sa.Column[object]:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def _jsonb_default() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    # New rows can be generated directly in PostgreSQL smoke tests while the
    # application keeps its Python-side UUID string default.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Align existing identity tables additively. Existing slugs, tenant-scoped
    # email uniqueness, and RBAC roles remain untouched for M2 compatibility.
    op.add_column(
        "tenants",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )
    op.add_column("tenants", _updated_at_column())
    op.add_column("tenants", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_tenants_status",
        "tenants",
        "status IN ('active', 'suspended', 'closed')",
    )

    op.add_column("users", sa.Column("phone", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(length=80), nullable=True))
    op.add_column(
        "users",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )
    op.add_column("users", _updated_at_column())
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET display_name = full_name WHERE full_name IS NOT NULL")
    op.execute("UPDATE users SET status = CASE WHEN is_active THEN 'active' ELSE 'disabled' END")
    op.create_check_constraint("ck_users_status", "users", "status IN ('active', 'disabled')")

    # Platform and billing catalogs. These are independent of task execution
    # and are seeded with MVP defaults below.
    op.create_table(
        "plans",
        _id_column(),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("quota_credits", sa.Integer(), nullable=False),
        sa.Column("max_concurrent", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("seat_limit", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=_jsonb_default(),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created_at_column(),
        sa.CheckConstraint("period IN ('monthly', 'yearly')", name="ck_plans_period"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_plans_code"),
    )

    op.create_table(
        "voices",
        _id_column(),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("voice_code", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("gender", sa.String(length=16), nullable=False, server_default="neutral"),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="zh-CN"),
        sa.Column("sample_url", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "gender IN ('male', 'female', 'neutral')",
            name="ck_voices_gender",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "voice_code", name="uq_voices_provider_voice_code"),
    )

    op.create_table(
        "credit_rates",
        _id_column(),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("credits_per_unit", sa.Numeric(12, 4), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "effective_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', 'publish')",
            name="ck_credit_rates_capability",
        ),
        sa.CheckConstraint(
            "unit IN ('second', 'call', 'token', 'image')",
            name="ck_credit_rates_unit",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_credit_rates_tenant_capability_unit",
        "credit_rates",
        ["tenant_id", "capability", "unit"],
    )

    op.create_table(
        "subscriptions",
        _id_column(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quota_credits_total", sa.Integer(), nullable=False),
        sa.Column("quota_credits_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quota_credits_reserved", sa.Integer(), nullable=False, server_default="0"),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "status IN ('active', 'expired', 'canceled')",
            name="ck_subscriptions_status",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subscriptions_tenant_id", "subscriptions", ["tenant_id"])

    # Unified asset storage. `brand_assets` stays as a legacy app table; this
    # table is the new normalized material catalog for generated/uploaded media.
    op.create_table(
        "assets",
        _id_column(),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("storage_key", sa.String(length=400), nullable=False),
        sa.Column("mime_type", sa.String(length=80), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=_jsonb_default(),
        ),
        _created_at_column(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "type IN ('avatar_image', 'audio', 'subtitle', 'video', 'bgm', 'cover', "
            "'product_image')",
            name="ck_assets_type",
        ),
        sa.CheckConstraint(
            "source IN ('upload', 'generated', 'preset')",
            name="ck_assets_source",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed')",
            name="ck_assets_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assets_tenant_type", "assets", ["tenant_id", "type"])

    op.create_table(
        "brands",
        _id_column(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("voice_id", sa.String(length=36), nullable=True),
        sa.Column("color_primary", sa.String(length=16), nullable=True),
        sa.Column("watermark_asset_id", sa.String(length=36), nullable=True),
        sa.Column("tone_prompt", sa.Text(), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["voice_id"], ["voices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["watermark_asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_brands_tenant_id", "brands", ["tenant_id"])

    op.create_table(
        "batch_jobs",
        _id_column(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("source_type", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done_count", sa.Integer(), nullable=False, server_default="0"),
        _created_at_column(),
        sa.CheckConstraint(
            "source_type IN ('manual', 'csv')",
            name="ck_batch_jobs_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name="ck_batch_jobs_status",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_batch_jobs_tenant_id", "batch_jobs", ["tenant_id"])

    op.create_table(
        "provider_configs",
        _id_column(),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=_jsonb_default(),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', 'publish')",
            name="ck_provider_configs_capability",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_provider_configs_tenant_capability",
        "provider_configs",
        ["tenant_id", "capability"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )
    op.create_index(
        "uq_provider_configs_platform_capability",
        "provider_configs",
        ["capability"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
    )

    op.create_table(
        "platform_accounts",
        _id_column(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("account_name", sa.String(length=120), nullable=False),
        sa.Column("credential_ref", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="authorized"),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "platform IN ('douyin', 'xiaohongshu', 'shipinhao', 'kuaishou')",
            name="ck_platform_accounts_platform",
        ),
        sa.CheckConstraint(
            "status IN ('authorized', 'expired', 'revoked')",
            name="ck_platform_accounts_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_accounts_tenant_id", "platform_accounts", ["tenant_id"])

    # Extend existing templates instead of recreating the target singular table.
    # schema_0001 supports platform templates, represented by tenant_id NULL.
    op.alter_column(
        "templates",
        "tenant_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )
    op.add_column(
        "templates",
        sa.Column("type", sa.String(length=32), nullable=False, server_default="visual"),
    )
    op.create_check_constraint(
        "ck_templates_type",
        "templates",
        "type IN ('subtitle', 'cover', 'visual')",
    )
    op.add_column(
        "templates",
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=_jsonb_default(),
        ),
    )
    op.add_column("templates", _created_at_column())

    # Extend the existing task table while retaining current storage/task columns.
    op.execute(
        """
        UPDATE video_tasks
        SET status = CASE UPPER(status)
            WHEN 'PENDING' THEN 'queued'
            WHEN 'STARTED' THEN 'running'
            WHEN 'PROGRESS' THEN 'running'
            WHEN 'RETRY' THEN 'running'
            WHEN 'SUCCESS' THEN 'done'
            WHEN 'DONE' THEN 'done'
            WHEN 'FAILURE' THEN 'failed'
            WHEN 'FAILED' THEN 'failed'
            ELSE status
        END
        """
    )
    op.execute("UPDATE video_tasks SET progress = 0 WHERE progress IS NULL")
    op.add_column("video_tasks", sa.Column("script", sa.Text(), nullable=True))
    op.add_column(
        "video_tasks",
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="static_template"),
    )
    op.execute("UPDATE video_tasks SET mode = COALESCE(video_mode, 'static_template')")
    op.add_column("video_tasks", sa.Column("voice_id", sa.String(length=36), nullable=True))
    op.add_column(
        "video_tasks",
        sa.Column("speed", sa.Numeric(3, 1), nullable=False, server_default="1.0"),
    )
    op.add_column(
        "video_tasks",
        sa.Column("aspect_ratio", sa.String(length=8), nullable=False, server_default="9:16"),
    )
    op.add_column(
        "video_tasks",
        sa.Column("subtitle_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "video_tasks",
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=_jsonb_default(),
        ),
    )
    op.add_column("video_tasks", sa.Column("error_code", sa.String(length=40), nullable=True))
    op.add_column("video_tasks", sa.Column("error_message", sa.Text(), nullable=True))
    op.execute("UPDATE video_tasks SET error_message = error WHERE error IS NOT NULL")
    op.add_column("video_tasks", sa.Column("batch_id", sa.String(length=36), nullable=True))
    op.add_column("video_tasks", sa.Column("brand_id", sa.String(length=36), nullable=True))
    op.add_column("video_tasks", sa.Column("template_id", sa.String(length=36), nullable=True))
    op.add_column("video_tasks", _updated_at_column())
    op.add_column("video_tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "video_tasks",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    # video_tasks is a soft-delete table per schema_0001 §9, so retain rows and
    # hide them at query time instead of physically deleting task history.
    op.add_column("video_tasks", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column(
        "video_tasks",
        "progress",
        existing_type=sa.Integer(),
        type_=sa.SmallInteger(),
        nullable=False,
        server_default="0",
        postgresql_using="progress::smallint",
    )
    op.alter_column(
        "video_tasks",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.alter_column(
        "video_tasks",
        "status",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="queued",
    )
    op.create_foreign_key(
        "fk_video_tasks_voice_id_voices",
        "video_tasks",
        "voices",
        ["voice_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_video_tasks_batch_id_batch_jobs",
        "video_tasks",
        "batch_jobs",
        ["batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_video_tasks_brand_id_brands",
        "video_tasks",
        "brands",
        ["brand_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_video_tasks_template_id_templates",
        "video_tasks",
        "templates",
        ["template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_video_tasks_progress_range",
        "video_tasks",
        "progress >= 0 AND progress <= 100",
    )
    op.create_check_constraint(
        "ck_video_tasks_status",
        "video_tasks",
        "status IN ('queued', 'running', 'done', 'failed')",
    )
    op.create_check_constraint(
        "ck_video_tasks_aspect_ratio",
        "video_tasks",
        "aspect_ratio IN ('9:16', '16:9', '1:1')",
    )
    op.create_index("ix_video_tasks_tenant_created_at", "video_tasks", ["tenant_id", "created_at"])
    op.create_index("ix_video_tasks_tenant_status", "video_tasks", ["tenant_id", "status"])
    op.create_index("ix_video_tasks_batch_id", "video_tasks", ["batch_id"])

    op.create_table(
        "task_assets",
        _id_column(),
        sa.Column("video_task_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        _created_at_column(),
        sa.CheckConstraint(
            "role IN ('input_avatar', 'output_audio', 'output_subtitle', 'output_video')",
            name="ck_task_assets_role",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["video_task_id"], ["video_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "video_task_id",
            "asset_id",
            "role",
            name="uq_task_assets_task_asset_role",
        ),
    )
    op.create_index("ix_task_assets_video_task_id", "task_assets", ["video_task_id"])

    op.create_table(
        "usage_records",
        _id_column(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("subscription_id", sa.String(length=36), nullable=True),
        sa.Column("video_task_id", sa.String(length=36), nullable=True),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("credits", sa.Numeric(12, 2), nullable=False),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="CNY"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="reserved"),
        _created_at_column(),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', 'publish')",
            name="ck_usage_records_capability",
        ),
        sa.CheckConstraint(
            "unit IN ('second', 'call', 'token', 'image')",
            name="ck_usage_records_unit",
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled', 'released')",
            name="ck_usage_records_status",
        ),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["video_task_id"], ["video_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_records_subscription_status",
        "usage_records",
        ["subscription_id", "status"],
    )
    op.create_index(
        "ix_usage_records_tenant_created_at",
        "usage_records",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "publish_jobs",
        _id_column(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("video_task_id", sa.String(length=36), nullable=False),
        sa.Column("platform_account_id", sa.String(length=36), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("result_url", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "status IN ('pending', 'publishing', 'done', 'failed')",
            name="ck_publish_jobs_status",
        ),
        sa.ForeignKeyConstraint(["platform_account_id"], ["platform_accounts.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["video_task_id"], ["video_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_publish_jobs_tenant_id", "publish_jobs", ["tenant_id"])

    op.create_table(
        "payment_orders",
        _id_column(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("subscription_id", sa.String(length=36), nullable=True),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="CNY"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("external_txn_id", sa.String(length=120), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'canceled', 'refunded', 'failed')",
            name="ck_payment_orders_status",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_orders_tenant_id", "payment_orders", ["tenant_id"])

    _seed_defaults()


def _seed_defaults() -> None:
    # Baseline catalog data keeps early environments usable without adding API
    # business logic in this package.
    op.execute(
        """
        INSERT INTO plans (
            id, code, name, price_cents, period, quota_credits,
            max_concurrent, seat_limit, features, is_active, created_at
        )
        VALUES (
            '00000000-0000-0000-0000-000000000101',
            'basic',
            'Basic',
            0,
            'monthly',
            1000,
            1,
            3,
            '{}'::jsonb,
            true,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO voices (id, provider, voice_code, display_name, gender, language, is_active)
        VALUES
            (
                '00000000-0000-0000-0000-000000000201',
                'edge-tts',
                'zh-CN-YunjianNeural',
                'Yunjian',
                'male',
                'zh-CN',
                true
            ),
            (
                '00000000-0000-0000-0000-000000000202',
                'edge-tts',
                'zh-CN-XiaoxiaoNeural',
                'Xiaoxiao',
                'female',
                'zh-CN',
                true
            ),
            (
                '00000000-0000-0000-0000-000000000203',
                'edge-tts',
                'zh-CN-YunxiNeural',
                'Yunxi',
                'male',
                'zh-CN',
                true
            ),
            (
                '00000000-0000-0000-0000-000000000204',
                'edge-tts',
                'zh-CN-XiaoyiNeural',
                'Xiaoyi',
                'female',
                'zh-CN',
                true
            )
        ON CONFLICT (provider, voice_code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO credit_rates (
            id, tenant_id, capability, unit, credits_per_unit, is_active, effective_at
        )
        VALUES
            (
                '00000000-0000-0000-0000-000000000301',
                NULL,
                'avatar',
                'second',
                1.0000,
                true,
                CURRENT_TIMESTAMP
            ),
            (
                '00000000-0000-0000-0000-000000000302',
                NULL,
                'tts',
                'second',
                0.2000,
                true,
                CURRENT_TIMESTAMP
            ),
            (
                '00000000-0000-0000-0000-000000000303',
                NULL,
                'image',
                'image',
                5.0000,
                true,
                CURRENT_TIMESTAMP
            ),
            (
                '00000000-0000-0000-0000-000000000304',
                NULL,
                'video',
                'second',
                2.0000,
                true,
                CURRENT_TIMESTAMP
            )
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO provider_configs (
            id, tenant_id, capability, provider, config, is_active
        )
        VALUES
            (
                '00000000-0000-0000-0000-000000000401',
                NULL,
                'llm',
                'deepseek',
                '{}'::jsonb,
                true
            ),
            (
                '00000000-0000-0000-0000-000000000402',
                NULL,
                'tts',
                'edge-tts',
                '{}'::jsonb,
                true
            ),
            (
                '00000000-0000-0000-0000-000000000403',
                NULL,
                'avatar',
                'omnihuman',
                '{}'::jsonb,
                true
            )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Drop dependent/pre-reserved tables first, then remove added task columns
    # and finally remove independent catalogs.
    op.drop_index("ix_payment_orders_tenant_id", table_name="payment_orders")
    op.drop_table("payment_orders")
    op.drop_index("ix_publish_jobs_tenant_id", table_name="publish_jobs")
    op.drop_table("publish_jobs")
    op.drop_index("ix_usage_records_tenant_created_at", table_name="usage_records")
    op.drop_index("ix_usage_records_subscription_status", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_index("ix_task_assets_video_task_id", table_name="task_assets")
    op.drop_table("task_assets")

    op.drop_index("ix_video_tasks_batch_id", table_name="video_tasks")
    op.drop_index("ix_video_tasks_tenant_status", table_name="video_tasks")
    op.drop_index("ix_video_tasks_tenant_created_at", table_name="video_tasks")
    op.drop_constraint("ck_video_tasks_aspect_ratio", "video_tasks", type_="check")
    op.drop_constraint("ck_video_tasks_status", "video_tasks", type_="check")
    op.drop_constraint("ck_video_tasks_progress_range", "video_tasks", type_="check")
    op.drop_constraint("fk_video_tasks_template_id_templates", "video_tasks", type_="foreignkey")
    op.drop_constraint("fk_video_tasks_brand_id_brands", "video_tasks", type_="foreignkey")
    op.drop_constraint("fk_video_tasks_batch_id_batch_jobs", "video_tasks", type_="foreignkey")
    op.drop_constraint("fk_video_tasks_voice_id_voices", "video_tasks", type_="foreignkey")
    op.alter_column(
        "video_tasks",
        "status",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "video_tasks",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.alter_column(
        "video_tasks",
        "progress",
        existing_type=sa.SmallInteger(),
        type_=sa.Integer(),
        nullable=True,
        server_default=None,
        postgresql_using="progress::integer",
    )
    for column_name in (
        "deleted_at",
        "finished_at",
        "started_at",
        "updated_at",
        "template_id",
        "brand_id",
        "batch_id",
        "error_message",
        "error_code",
        "params",
        "subtitle_enabled",
        "aspect_ratio",
        "speed",
        "voice_id",
        "mode",
        "script",
    ):
        op.drop_column("video_tasks", column_name)

    # This revision was amended before merge. Some dev databases may have an
    # earlier local 0003 without ck_templates_type, so keep downgrade replayable.
    op.execute("ALTER TABLE templates DROP CONSTRAINT IF EXISTS ck_templates_type")
    # Reverting to the pre-schema_0001 table shape cannot represent platform
    # templates, so remove those dev-only rows before restoring NOT NULL.
    op.execute("DELETE FROM templates WHERE tenant_id IS NULL")
    op.alter_column(
        "templates",
        "tenant_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
    op.drop_column("templates", "created_at")
    op.drop_column("templates", "config")
    op.drop_column("templates", "type")

    op.drop_index("ix_platform_accounts_tenant_id", table_name="platform_accounts")
    op.drop_table("platform_accounts")
    op.drop_index("uq_provider_configs_platform_capability", table_name="provider_configs")
    op.drop_index("uq_provider_configs_tenant_capability", table_name="provider_configs")
    op.drop_table("provider_configs")
    op.drop_index("ix_batch_jobs_tenant_id", table_name="batch_jobs")
    op.drop_table("batch_jobs")
    op.drop_index("ix_brands_tenant_id", table_name="brands")
    op.drop_table("brands")
    op.drop_index("ix_assets_tenant_type", table_name="assets")
    op.drop_table("assets")
    op.drop_index("ix_subscriptions_tenant_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_credit_rates_tenant_capability_unit", table_name="credit_rates")
    op.drop_table("credit_rates")
    op.drop_table("voices")
    op.drop_table("plans")

    op.drop_constraint("ck_users_status", "users", type_="check")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "status")
    op.drop_column("users", "display_name")
    op.drop_column("users", "phone")

    op.drop_constraint("ck_tenants_status", "tenants", type_="check")
    op.drop_column("tenants", "deleted_at")
    op.drop_column("tenants", "updated_at")
    op.drop_column("tenants", "status")
