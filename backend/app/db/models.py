from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _json_type():
    return JSON().with_variant(JSONB(), "postgresql")


class Role(StrEnum):
    ADMIN = "admin"
    OPS = "ops"
    CREATOR = "creator"
    REVIEWER = "reviewer"
    DEVELOPER = "developer"


class TenantScopedMixin:
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'closed')",
            name="ck_tenants_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")
    organizations: Mapped[list["Organization"]] = relationship(back_populates="tenant")


class TenantLabelSettings(Base):
    __tablename__ = "tenant_label_settings"
    __table_args__ = (
        CheckConstraint(
            "position IN ('br', 'bl', 'tr', 'tl', 'bc')",
            name="ck_tenant_label_settings_position",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[str] = mapped_column(String(2), default="br")
    text: Mapped[str] = mapped_column(String(20), default="AI 生成")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(TenantScopedMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_users_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200), default=None)
    display_name: Mapped[str | None] = mapped_column(String(80), default=None)
    role: Mapped[str] = mapped_column(String(32), default=Role.CREATOR.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class Organization(TenantScopedMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    tenant: Mapped[Tenant] = relationship(back_populates="organizations")


class Project(TenantScopedMixin, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    organization_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )


class VideoTask(TenantScopedMixin, Base):
    __tablename__ = "video_tasks"
    __table_args__ = (
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_video_tasks_progress_range",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name="ck_video_tasks_status",
        ),
        CheckConstraint(
            "aspect_ratio IN ('9:16', '16:9', '1:1')",
            name="ck_video_tasks_aspect_ratio",
        ),
        Index("ix_video_tasks_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_video_tasks_tenant_status", "tenant_id", "status"),
        Index("ix_video_tasks_batch_id", "batch_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued")
    topic: Mapped[str | None] = mapped_column(Text, default=None)
    script: Mapped[str | None] = mapped_column(Text, default=None)
    mode: Mapped[str] = mapped_column(String(32), default="static_template")
    video_mode: Mapped[str] = mapped_column(String(32), default="static_template")
    progress: Mapped[int] = mapped_column(SmallInteger, default=0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    error_code: Mapped[str | None] = mapped_column(String(40), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    voice_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("voices.id", ondelete="SET NULL"), nullable=True
    )
    brand_voice_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("brand_voices.id", ondelete="SET NULL"), nullable=True
    )
    speed: Mapped[Decimal] = mapped_column(Numeric(3, 1), default=Decimal("1.0"))
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="9:16")
    subtitle_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    params: Mapped[dict[str, object]] = mapped_column(_json_type(), default=dict)
    batch_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("batch_jobs.id", ondelete="SET NULL"), nullable=True
    )
    brand_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("brands.id", ondelete="SET NULL"), nullable=True
    )
    template_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    storage_bucket: Mapped[str | None] = mapped_column(String(255), default=None)
    storage_key: Mapped[str | None] = mapped_column(String(500), default=None)
    thumbnail_key: Mapped[str | None] = mapped_column(String(500), default=None)
    content_type: Mapped[str | None] = mapped_column(String(100), default=None)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    duration_sec: Mapped[float | None] = mapped_column(Float, default=None)
    local_path: Mapped[str | None] = mapped_column(String(1000), default=None)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class CopyDraft(TenantScopedMixin, Base):
    __tablename__ = "copy_drafts"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('smart', 'custom', 'auto')",
            name="ck_copy_drafts_mode",
        ),
        Index("ix_copy_drafts_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_text: Mapped[str] = mapped_column(Text)
    result_text: Mapped[str] = mapped_column(Text)
    titles: Mapped[list[str] | None] = mapped_column(_json_type(), default=None)
    topics: Mapped[list[str] | None] = mapped_column(_json_type(), default=None)
    mode: Mapped[str] = mapped_column(String(16))
    target_platform: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class BrandAsset(TenantScopedMixin, Base):
    __tablename__ = "brand_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(500))


class Template(TenantScopedMixin, Base):
    __tablename__ = "templates"
    __table_args__ = (
        CheckConstraint(
            "type IN ('subtitle', 'cover', 'visual')",
            name="ck_templates_type",
        ),
    )

    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    type: Mapped[str] = mapped_column(String(32), default="visual")
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict[str, object]] = mapped_column(_json_type(), default=dict)
    path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("period IN ('monthly', 'yearly')", name="ck_plans_period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    price_cents: Mapped[int] = mapped_column(Integer)
    period: Mapped[str] = mapped_column(String(16))
    quota_credits: Mapped[int] = mapped_column(Integer)
    max_concurrent: Mapped[int] = mapped_column(SmallInteger, default=1)
    seat_limit: Mapped[int] = mapped_column(SmallInteger, default=1)
    features: Mapped[dict[str, object]] = mapped_column(_json_type(), default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'expired', 'canceled')",
            name="ck_subscriptions_status",
        ),
        Index("ix_subscriptions_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"))
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("plans.id"))
    status: Mapped[str] = mapped_column(String(32), default="active")
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quota_credits_total: Mapped[int] = mapped_column(Integer)
    quota_credits_used: Mapped[int] = mapped_column(Integer, default=0)
    quota_credits_reserved: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CreditRate(Base):
    __tablename__ = "credit_rates"
    __table_args__ = (
        CheckConstraint(
            "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
            "'publish', 'voice_clone')",
            name="ck_credit_rates_capability",
        ),
        CheckConstraint(
            "unit IN ('second', 'call', 'token', 'image')",
            name="ck_credit_rates_unit",
        ),
        Index("ix_credit_rates_tenant_capability_unit", "tenant_id", "capability", "unit"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=True
    )
    capability: Mapped[str] = mapped_column(String(32))
    unit: Mapped[str] = mapped_column(String(32))
    credits_per_unit: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Voice(Base):
    __tablename__ = "voices"
    __table_args__ = (
        UniqueConstraint("provider", "voice_code", name="uq_voices_provider_voice_code"),
        CheckConstraint(
            "gender IN ('male', 'female', 'neutral')",
            name="ck_voices_gender",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(40))
    voice_code: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(120))
    gender: Mapped[str] = mapped_column(String(16), default="neutral")
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    sample_url: Mapped[str | None] = mapped_column(String(500), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BrandVoice(TenantScopedMixin, Base):
    __tablename__ = "brand_voices"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'ready', 'failed')",
            name="ck_brand_voices_status",
        ),
        Index("ix_brand_voices_tenant_status", "tenant_id", "status"),
        Index("ix_brand_voices_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(30))
    source_audio_asset_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(40), default="doubao-voice-clone")
    speaker_id: Mapped[str | None] = mapped_column(String(160), default=None)
    status: Mapped[str] = mapped_column(String(32), default="processing")
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    error_code: Mapped[str | None] = mapped_column(String(40), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "type IN ('avatar_image', 'audio', 'subtitle', 'video', 'bgm', 'cover', "
            "'product_image', 'generated_image')",
            name="ck_assets_type",
        ),
        CheckConstraint(
            "source IN ('upload', 'generated', 'preset')",
            name="ck_assets_source",
        ),
        CheckConstraint(
            "status IN ('pending', 'ready', 'failed')",
            name="ck_assets_status",
        ),
        Index("ix_assets_tenant_type", "tenant_id", "type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(40), default=None)
    storage_key: Mapped[str] = mapped_column(String(400))
    mime_type: Mapped[str | None] = mapped_column(String(80), default=None)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    metadata_: Mapped[dict[str, object]] = mapped_column("metadata", _json_type(), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (Index("ix_brands_tenant_id", "tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(120))
    voice_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("voices.id", ondelete="SET NULL"), nullable=True
    )
    color_primary: Mapped[str | None] = mapped_column(String(16), default=None)
    watermark_asset_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    tone_prompt: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class BatchJob(Base):
    __tablename__ = "batch_jobs"
    __table_args__ = (
        CheckConstraint("source_type IN ('manual', 'csv')", name="ck_batch_jobs_source_type"),
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name="ck_batch_jobs_status",
        ),
        Index("ix_batch_jobs_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"))
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    done_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TaskAsset(Base):
    __tablename__ = "task_assets"
    __table_args__ = (
        UniqueConstraint(
            "video_task_id",
            "asset_id",
            "role",
            name="uq_task_assets_task_asset_role",
        ),
        CheckConstraint(
            "role IN ('input_avatar', 'output_audio', 'output_subtitle', 'output_video', "
            "'output_image')",
            name="ck_task_assets_role",
        ),
        Index("ix_task_assets_video_task_id", "video_task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    video_task_id: Mapped[str] = mapped_column(String(36), ForeignKey("video_tasks.id"))
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id"))
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProviderConfig(Base):
    __tablename__ = "provider_configs"
    __table_args__ = (
        CheckConstraint(
            "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
            "'publish', 'voice_clone')",
            name="ck_provider_configs_capability",
        ),
        Index(
            "uq_provider_configs_tenant_capability",
            "tenant_id",
            "capability",
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
            sqlite_where=text("tenant_id IS NOT NULL"),
        ),
        Index(
            "uq_provider_configs_platform_capability",
            "capability",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
            sqlite_where=text("tenant_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=True
    )
    capability: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict[str, object]] = mapped_column(_json_type(), default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        CheckConstraint(
            "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
            "'publish', 'voice_clone')",
            name="ck_usage_records_capability",
        ),
        CheckConstraint(
            "unit IN ('second', 'call', 'token', 'image')",
            name="ck_usage_records_unit",
        ),
        CheckConstraint(
            "status IN ('reserved', 'settled', 'released')",
            name="ck_usage_records_status",
        ),
        Index("ix_usage_records_subscription_status", "subscription_id", "status"),
        Index("ix_usage_records_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"))
    subscription_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("subscriptions.id"), nullable=True
    )
    video_task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("video_tasks.id"), nullable=True
    )
    capability: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(80), default=None)
    unit: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    credits: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cost_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    status: Mapped[str] = mapped_column(String(32), default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class PlatformAccount(Base):
    __tablename__ = "platform_accounts"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('douyin', 'xiaohongshu', 'shipinhao', 'kuaishou')",
            name="ck_platform_accounts_platform",
        ),
        CheckConstraint(
            "status IN ('authorized', 'expired', 'revoked')",
            name="ck_platform_accounts_status",
        ),
        Index("ix_platform_accounts_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"))
    platform: Mapped[str] = mapped_column(String(32))
    account_name: Mapped[str] = mapped_column(String(120))
    credential_ref: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="authorized")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PublishJob(Base):
    __tablename__ = "publish_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'publishing', 'done', 'failed')",
            name="ck_publish_jobs_status",
        ),
        Index("ix_publish_jobs_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"))
    video_task_id: Mapped[str] = mapped_column(String(36), ForeignKey("video_tasks.id"))
    platform_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("platform_accounts.id")
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    result_url: Mapped[str | None] = mapped_column(String(500), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaymentOrder(Base):
    __tablename__ = "payment_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'paid', 'canceled', 'refunded', 'failed')",
            name="ck_payment_orders_status",
        ),
        Index("ix_payment_orders_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"))
    subscription_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("subscriptions.id"), nullable=True
    )
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("plans.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    external_txn_id: Mapped[str | None] = mapped_column(String(120), default=None)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
