from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import select

from app.db.models import CreditRate, Plan, Subscription, UsageRecord, VideoTask
from app.services import quota


def _migration_module():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260706_0018_credit_rate_reprice.py"
    )
    spec = importlib.util.spec_from_file_location("credit_rate_reprice_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _quota_scale_migration_module():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260706_0019_scale_plan_quota.py"
    )
    spec = importlib.util.spec_from_file_location("plan_quota_scale_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _seed_repriced_platform_rates(db) -> None:
    db.add_all(
        [
            CreditRate(capability="avatar", unit="second", credits_per_unit=Decimal("150")),
            CreditRate(capability="tts", unit="character", credits_per_unit=Decimal("0.1")),
            CreditRate(capability="video", unit="second", credits_per_unit=Decimal("80")),
            CreditRate(capability="video_gen", unit="second", credits_per_unit=Decimal("80")),
            CreditRate(capability="image", unit="image", credits_per_unit=Decimal("10")),
            CreditRate(capability="llm", unit="call", credits_per_unit=Decimal("1")),
            CreditRate(capability="reverse_prompt", unit="call", credits_per_unit=Decimal("30")),
        ]
    )


def test_repriced_credit_rates_match_bearing_estimates(auth_db, auth_context):
    script = "x" * 60
    with auth_db() as db:
        _seed_repriced_platform_rates(db)
        db.flush()

        avatar = quota.estimate_avatar_talk_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            script=script,
            speed=Decimal("0.95"),
        )
        ecom_720p = quota.estimate_seedance_i2v_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            script=script,
            speed=1,
            estimated_seconds=5,
            resolution="720p",
        )
        video_gen_720p = quota.estimate_video_gen_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            duration_sec=5,
            resolution="720p",
        )
        video_gen_1080p = quota.estimate_video_gen_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            duration_sec=5,
            resolution="1080p",
        )
        image = quota.estimate_image_generation_quota(
            db,
            tenant_id=auth_context["tenant_id"],
        )
        copy = quota.estimate_copy_quota(db, tenant_id=auth_context["tenant_id"])
        reverse = quota.estimate_reverse_prompt_quota(
            db,
            tenant_id=auth_context["tenant_id"],
        )

    assert avatar.estimated_seconds == 13
    assert avatar.estimated_credits == Decimal("1956.00")
    assert avatar.reservation_units == 1956
    assert ecom_720p.estimated_credits == Decimal("656.00")
    assert ecom_720p.reservation_units == 656
    assert video_gen_720p.estimated_credits == Decimal("650.00")
    assert video_gen_720p.reservation_units == 650
    assert video_gen_1080p.estimated_credits == Decimal("1400.00")
    assert video_gen_1080p.reservation_units == 1400
    assert image.estimated_credits == Decimal("10.00")
    assert image.reservation_units == 10
    assert copy.estimated_credits == Decimal("1.00")
    assert copy.reservation_units == 1
    assert reverse.estimated_credits == Decimal("30.00")
    assert reverse.reservation_units == 30


def test_basic_plan_new_subscription_defaults_to_scaled_quota(auth_db, auth_context):
    with auth_db() as db:
        plan = db.scalar(select(Plan).where(Plan.code == "basic"))
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )

    assert plan is not None
    assert plan.quota_credits == 10_000_000
    assert subscription is not None
    assert subscription.quota_credits_total == 10_000_000


def test_scaled_basic_quota_can_reserve_repriced_avatar(auth_db, auth_context):
    script = "x" * 60
    with auth_db() as db:
        _seed_repriced_platform_rates(db)
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        assert subscription is not None
        task = VideoTask(
            id="avatar-reprice-reserve",
            tenant_id=auth_context["tenant_id"],
            status="queued",
            mode="avatar_talk",
            video_mode="avatar_talk",
            script=script,
        )
        db.add(task)
        db.flush()

        reservation = quota.reserve_avatar_talk_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            video_task_id=task.id,
            script=script,
            speed=Decimal("0.95"),
        )
        db.flush()

        assert subscription.quota_credits_total == 10_000_000
        assert reservation.usage_record.credits == Decimal("1956.00")
        assert reservation.usage_record.status == "reserved"
        assert subscription.quota_credits_reserved == 1956


def test_tenant_custom_rates_still_override_platform_defaults(auth_db, auth_context):
    script = "x" * 60
    with auth_db() as db:
        _seed_repriced_platform_rates(db)
        db.add_all(
            [
                CreditRate(
                    tenant_id=auth_context["tenant_id"],
                    capability="video",
                    unit="second",
                    credits_per_unit=Decimal("3"),
                ),
                CreditRate(
                    tenant_id=auth_context["tenant_id"],
                    capability="tts",
                    unit="character",
                    credits_per_unit=Decimal("0.5"),
                ),
            ]
        )
        db.flush()

        ecom_720p = quota.estimate_seedance_i2v_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            script=script,
            speed=1,
            estimated_seconds=5,
            resolution="720p",
        )

    assert ecom_720p.estimated_credits == Decimal("54.38")
    assert ecom_720p.reservation_units == 55


def test_settle_avatar_keeps_tts_character_component_when_duration_changes(
    auth_db,
    auth_context,
):
    script = "x" * 60
    with auth_db() as db:
        _seed_repriced_platform_rates(db)
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        assert subscription is not None
        subscription.quota_credits_reserved = 1956
        task = VideoTask(
            id="avatar-reprice-settle",
            tenant_id=auth_context["tenant_id"],
            status="queued",
            mode="avatar_talk",
            video_mode="avatar_talk",
            script=script,
        )
        db.add(task)
        db.flush()
        usage = UsageRecord(
            tenant_id=auth_context["tenant_id"],
            subscription_id=subscription.id,
            video_task_id=task.id,
            capability="avatar",
            provider="omnihuman",
            model="jimeng_realman_avatar_picture_omni_v15",
            unit="second",
            quantity=Decimal("13"),
            credits=Decimal("1956.00"),
            cost_cents=0,
            status="reserved",
        )
        db.add(usage)
        db.flush()

        quota.settle_reserved_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            video_task_id=task.id,
            actual_seconds=10,
            cost_cents=1000,
        )
        db.flush()

        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 1506
        assert usage.quantity == Decimal("10.000")
        assert usage.credits == Decimal("1506.00")


def test_settle_seedance_keeps_tts_character_component_when_duration_changes(
    auth_db,
    auth_context,
):
    script = "x" * 60
    with auth_db() as db:
        _seed_repriced_platform_rates(db)
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        assert subscription is not None
        subscription.quota_credits_reserved = 656
        task = VideoTask(
            id="seedance-reprice-settle",
            tenant_id=auth_context["tenant_id"],
            status="queued",
            mode="seedance_i2v",
            video_mode="seedance_i2v",
            script=script,
            params={"resolution": "720p"},
        )
        db.add(task)
        db.flush()
        usage = UsageRecord(
            tenant_id=auth_context["tenant_id"],
            subscription_id=subscription.id,
            video_task_id=task.id,
            capability="video",
            provider="apimart",
            model="doubao-seedance-2.0",
            unit="second",
            quantity=Decimal("5"),
            credits=Decimal("656.00"),
            cost_cents=0,
            status="reserved",
        )
        db.add(usage)
        db.flush()

        quota.settle_reserved_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            video_task_id=task.id,
            actual_seconds=4,
            cost_cents=900,
        )
        db.flush()

        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 526
        assert usage.quantity == Decimal("4.000")
        assert usage.credits == Decimal("526.00")


def test_reprice_migration_updates_only_platform_default_rates():
    migration = _migration_module()

    class _Batch:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def drop_constraint(self, *args, **kwargs):
            return None

        def create_check_constraint(self, *args, **kwargs):
            return None

    class _Op:
        def __init__(self):
            self.executed: list[str] = []

        def batch_alter_table(self, *args, **kwargs):
            return _Batch()

        def execute(self, statement):
            self.executed.append(str(statement))

    fake_op = _Op()
    migration.op = fake_op
    migration.upgrade()

    updates = [statement for statement in fake_op.executed if "UPDATE credit_rates" in statement]
    platform_updates = [statement for statement in updates if "tenant_id IS NULL" in statement]
    tenant_tts_updates = [
        statement
        for statement in updates
        if "tenant_id IS NOT NULL" in statement and "capability = 'tts'" in statement
    ]
    assert {
        capability for statement in platform_updates for capability in _capabilities(statement)
    } == {
        "avatar",
        "tts",
        "video",
        "video_gen",
        "image",
    }
    assert len(tenant_tts_updates) == 1
    assert "SET unit = 'character'" in tenant_tts_updates[0]
    assert "credits_per_unit" not in tenant_tts_updates[0]
    assert not any("voice_clone" in statement for statement in updates)
    assert not any("reverse_prompt" in statement for statement in updates)
    assert not any("capability = 'llm'" in statement for statement in updates)


def test_plan_quota_scale_migration_updates_basic_and_active_subscriptions_only():
    migration = _quota_scale_migration_module()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    metadata = sa.MetaData()
    plans = sa.Table(
        "plans",
        metadata,
        sa.Column("code", sa.String, primary_key=True),
        sa.Column("quota_credits", sa.Integer, nullable=False),
    )
    subscriptions = sa.Table(
        "subscriptions",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("quota_credits_total", sa.Integer, nullable=False),
        sa.Column("quota_credits_used", sa.Integer, nullable=False),
        sa.Column("quota_credits_reserved", sa.Integer, nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            plans.insert(),
            [
                {"code": "basic", "quota_credits": 1000},
                {"code": "pro", "quota_credits": 5000},
            ],
        )
        conn.execute(
            subscriptions.insert(),
            [
                {
                    "id": 1,
                    "status": "active",
                    "quota_credits_total": 1000,
                    "quota_credits_used": 123,
                    "quota_credits_reserved": 45,
                },
                {
                    "id": 2,
                    "status": "canceled",
                    "quota_credits_total": 1000,
                    "quota_credits_used": 10,
                    "quota_credits_reserved": 5,
                },
            ],
        )

        class _Op:
            def execute(self, statement):
                conn.execute(statement)

        migration.op = _Op()
        migration.upgrade()

        assert conn.scalar(
            sa.select(plans.c.quota_credits).where(plans.c.code == "basic")
        ) == 10_000_000
        assert conn.scalar(
            sa.select(plans.c.quota_credits).where(plans.c.code == "pro")
        ) == 5000
        active = conn.execute(
            sa.select(subscriptions).where(subscriptions.c.id == 1)
        ).mappings().one()
        canceled = conn.execute(
            sa.select(subscriptions).where(subscriptions.c.id == 2)
        ).mappings().one()

    assert active["quota_credits_total"] == 10_000_000
    assert active["quota_credits_used"] == 123
    assert active["quota_credits_reserved"] == 45
    assert canceled["quota_credits_total"] == 1000
    assert canceled["quota_credits_used"] == 10
    assert canceled["quota_credits_reserved"] == 5


def _capabilities(statement: str) -> list[str]:
    capabilities = []
    for capability in ("avatar", "tts", "video", "video_gen", "image"):
        if f"capability = '{capability}'" in statement:
            capabilities.append(capability)
    return capabilities
