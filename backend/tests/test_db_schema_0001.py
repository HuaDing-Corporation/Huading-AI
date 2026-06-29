from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import CheckConstraint, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Asset,
    Base,
    CreditRate,
    Plan,
    Subscription,
    TaskAsset,
    Template,
    Tenant,
    UsageRecord,
    User,
    VideoTask,
    Voice,
)


def test_db_schema_0001_metadata_contains_target_tables() -> None:
    expected_tables = {
        "tenants",
        "users",
        "plans",
        "subscriptions",
        "credit_rates",
        "voices",
        "assets",
        "brands",
        "batch_jobs",
        "templates",
        "video_tasks",
        "task_assets",
        "provider_configs",
        "usage_records",
        "platform_accounts",
        "publish_jobs",
        "publish_records",
        "bgm_library_tracks",
        "payment_orders",
        "copy_drafts",
    }

    assert expected_tables <= set(Base.metadata.tables)


def test_db_schema_0001_extends_existing_contract_tables() -> None:
    tenants = Base.metadata.tables["tenants"]
    users = Base.metadata.tables["users"]
    video_tasks = Base.metadata.tables["video_tasks"]
    templates = Base.metadata.tables["templates"]

    assert {"status", "updated_at", "deleted_at"} <= set(tenants.c.keys())
    assert {"phone", "display_name", "status", "updated_at", "deleted_at"} <= set(
        users.c.keys()
    )
    assert {
        "mode",
        "script",
        "voice_id",
        "speed",
        "aspect_ratio",
        "subtitle_enabled",
        "params",
        "error_code",
        "error_message",
        "batch_id",
        "brand_id",
        "template_id",
        "updated_at",
        "started_at",
        "finished_at",
        "deleted_at",
    } <= set(video_tasks.c.keys())
    assert {"type", "config", "created_at"} <= set(templates.c.keys())
    assert templates.c.tenant_id.nullable is True


def test_db_schema_0001_core_indexes_and_constraints_are_declared() -> None:
    video_tasks = Base.metadata.tables["video_tasks"]
    assets = Base.metadata.tables["assets"]
    task_assets = Base.metadata.tables["task_assets"]
    usage_records = Base.metadata.tables["usage_records"]
    copy_drafts = Base.metadata.tables["copy_drafts"]
    publish_records = Base.metadata.tables["publish_records"]
    bgm_library_tracks = Base.metadata.tables["bgm_library_tracks"]

    assert {"ix_video_tasks_tenant_created_at", "ix_video_tasks_tenant_status"} <= {
        index.name for index in video_tasks.indexes
    }
    assert "ix_assets_tenant_type" in {index.name for index in assets.indexes}
    assert "ix_usage_records_tenant_created_at" in {index.name for index in usage_records.indexes}
    assert "uq_task_assets_task_asset_role" in {
        constraint.name for constraint in task_assets.constraints
    }
    assert "ck_video_tasks_progress_range" in {
        constraint.name
        for constraint in video_tasks.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_templates_type" in {
        constraint.name
        for constraint in Base.metadata.tables["templates"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ix_copy_drafts_tenant_created_at" in {index.name for index in copy_drafts.indexes}
    assert "ck_copy_drafts_mode" in {
        constraint.name
        for constraint in copy_drafts.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ix_publish_records_tenant_created_at" in {
        index.name for index in publish_records.indexes
    }
    assert "ck_publish_records_source_kind" in {
        constraint.name
        for constraint in publish_records.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ix_bgm_library_tracks_active" in {
        index.name for index in bgm_library_tracks.indexes
    }


def test_db_schema_0001_copy_drafts_contract() -> None:
    copy_drafts = Base.metadata.tables["copy_drafts"]

    assert {
        "id",
        "tenant_id",
        "source_text",
        "result_text",
        "titles",
        "topics",
        "mode",
        "target_platform",
        "created_at",
        "deleted_at",
    } <= set(copy_drafts.c.keys())
    assert copy_drafts.c.tenant_id.nullable is False
    assert copy_drafts.c.source_text.nullable is False
    assert copy_drafts.c.result_text.nullable is False


def test_db_schema_0001_publish_records_contract() -> None:
    publish_records = Base.metadata.tables["publish_records"]

    assert {
        "id",
        "tenant_id",
        "source_kind",
        "source_task_id",
        "items",
        "platforms",
        "created_at",
        "updated_at",
        "deleted_at",
    } <= set(publish_records.c.keys())
    assert publish_records.c.tenant_id.nullable is False
    assert publish_records.c.source_kind.nullable is False
    assert publish_records.c.source_task_id.nullable is False


def test_db_schema_0001_bgm_library_tracks_contract() -> None:
    bgm_library_tracks = Base.metadata.tables["bgm_library_tracks"]

    assert {
        "track_id",
        "name",
        "duration_sec",
        "storage_key",
        "preview_storage_key",
        "license",
        "is_active",
        "created_at",
    } <= set(bgm_library_tracks.c.keys())
    assert bgm_library_tracks.c.track_id.primary_key is True
    assert bgm_library_tracks.c.name.nullable is False
    assert bgm_library_tracks.c.license.nullable is False


def test_db_schema_0001_video_gen_capability_and_task_asset_roles() -> None:
    credit_rates = Base.metadata.tables["credit_rates"]
    usage_records = Base.metadata.tables["usage_records"]
    task_assets = Base.metadata.tables["task_assets"]

    assert any(
        constraint.name == "ck_credit_rates_capability"
        and "video_gen" in str(constraint.sqltext)
        for constraint in credit_rates.constraints
        if isinstance(constraint, CheckConstraint)
    )
    assert any(
        constraint.name == "ck_usage_records_capability"
        and "video_gen" in str(constraint.sqltext)
        for constraint in usage_records.constraints
        if isinstance(constraint, CheckConstraint)
    )
    assert any(
        constraint.name == "ck_task_assets_role"
        and "input_reference_image" in str(constraint.sqltext)
        and "input_bgm" in str(constraint.sqltext)
        for constraint in task_assets.constraints
        if isinstance(constraint, CheckConstraint)
    )


def test_db_schema_0001_usage_record_money_and_credit_columns() -> None:
    usage_records = Base.metadata.tables["usage_records"]

    assert {
        "quantity",
        "credits",
        "cost_cents",
        "currency",
        "status",
        "settled_at",
    } <= set(usage_records.c.keys())
    assert usage_records.c.cost_cents.type.python_type is int


def test_db_schema_0001_mapped_smoke_flow() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    now = datetime.now(UTC)

    try:
        with SessionTesting() as db:
            tenant = Tenant(
                id="11111111-1111-1111-1111-111111111111",
                slug="acme",
                name="Acme Studio",
            )
            user = User(
                id="22222222-2222-2222-2222-222222222222",
                tenant_id=tenant.id,
                email="owner@example.com",
                password_hash="hash",
                role="admin",
            )
            plan = Plan(
                id="33333333-3333-3333-3333-333333333333",
                code="basic-test",
                name="Basic Test",
                price_cents=0,
                period="monthly",
                quota_credits=1000,
                max_concurrent=1,
                seat_limit=3,
            )
            subscription = Subscription(
                id="44444444-4444-4444-4444-444444444444",
                tenant_id=tenant.id,
                plan_id=plan.id,
                status="active",
                period_start=now,
                period_end=now + timedelta(days=30),
                quota_credits_total=1000,
            )
            voice = Voice(
                id="55555555-5555-5555-5555-555555555555",
                provider="edge-tts",
                voice_code="zh-CN-XiaoxiaoNeural",
                display_name="Xiaoxiao",
                gender="female",
            )
            avatar = Asset(
                id="66666666-6666-6666-6666-666666666666",
                tenant_id=tenant.id,
                type="avatar_image",
                source="upload",
                storage_key=f"tenants/{tenant.id}/uploads/avatar.png",
                status="ready",
            )
            output = Asset(
                id="77777777-7777-7777-7777-777777777777",
                tenant_id=tenant.id,
                type="video",
                source="generated",
                storage_key=f"tenants/{tenant.id}/videos/unit-1/output.mp4",
                status="ready",
            )
            task = VideoTask(
                id="88888888-8888-8888-8888-888888888888",
                tenant_id=tenant.id,
                created_by_user_id=user.id,
                status="done",
                mode="avatar_talk",
                video_mode="avatar_talk",
                progress=100,
                topic="Launch",
                script="Hello from Acme.",
                voice_id=voice.id,
            )
            db.add_all(
                [
                    tenant,
                    user,
                    plan,
                    subscription,
                    voice,
                    CreditRate(
                        capability="avatar",
                        unit="second",
                        credits_per_unit=Decimal("1.0000"),
                    ),
                    avatar,
                    output,
                    task,
                    TaskAsset(video_task_id=task.id, asset_id=avatar.id, role="input_avatar"),
                    TaskAsset(video_task_id=task.id, asset_id=output.id, role="output_video"),
                    UsageRecord(
                        tenant_id=tenant.id,
                        subscription_id=subscription.id,
                        video_task_id=task.id,
                        capability="avatar",
                        provider="omnihuman",
                        model="mvp",
                        unit="second",
                        quantity=Decimal("30.000"),
                        credits=Decimal("30.00"),
                        cost_cents=120,
                        status="settled",
                        settled_at=now,
                    ),
                ]
            )
            subscription.quota_credits_used = 30
            subscription.quota_credits_reserved = 0
            db.commit()

            task_asset_rows = db.scalar(
                select(func.count())
                .select_from(TaskAsset)
                .where(TaskAsset.video_task_id == task.id)
            )
            remaining = (
                subscription.quota_credits_total
                - subscription.quota_credits_used
                - subscription.quota_credits_reserved
            )
            usage_rows = db.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.tenant_id == tenant.id)
            )
            cross_tenant_rows = db.scalar(
                select(func.count())
                .select_from(VideoTask)
                .where(VideoTask.tenant_id == "99999999-9999-9999-9999-999999999999")
            )

            assert task_asset_rows == 2
            assert subscription.quota_credits_used == 30
            assert subscription.quota_credits_reserved == 0
            assert remaining == 970
            assert usage_rows == 1
            assert cross_tenant_rows == 0
    finally:
        Base.metadata.drop_all(engine)


def test_db_schema_0001_templates_support_platform_rows_and_validate_type() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    try:
        with SessionTesting() as db:
            platform_template = Template(
                id="99999999-9999-9999-9999-999999999901",
                tenant_id=None,
                type="visual",
                name="Platform Visual",
                path="1080x1920/static_default.html",
            )
            db.add(platform_template)
            db.commit()

            assert db.get(Template, platform_template.id).tenant_id is None

            db.add(
                Template(
                    id="99999999-9999-9999-9999-999999999902",
                    tenant_id=None,
                    type="invalid",
                    name="Invalid Platform Template",
                    path="1080x1920/static_default.html",
                )
            )
            with pytest.raises(IntegrityError):
                db.commit()
    finally:
        Base.metadata.drop_all(engine)
