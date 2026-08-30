from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.exceptions import AppError
from app.db.models import (
    Asset,
    Plan,
    ProviderConfig,
    Subscription,
    Tenant,
    UsageRecord,
    User,
    VideoTask,
)
from app.main import app


def _load_plan_migration():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260712_0025_admin_vip_plans.py"
    )
    spec = importlib.util.spec_from_file_location("admin_vip_plan_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_admin_vip_plan_migration_upserts_idempotently(auth_db) -> None:
    migration = _load_plan_migration()
    assert migration.revision == "20260712_0025"
    assert migration.down_revision == "20260710_0024"

    with auth_db() as db:
        db.add_all(
            [
                Plan(
                    code="basic",
                    name="Basic",
                    price_cents=100,
                    period="monthly",
                    quota_credits=10_000_000,
                    is_active=True,
                ),
                Plan(
                    code="free",
                    name="Stale Free",
                    price_cents=999,
                    period="yearly",
                    quota_credits=999,
                    is_active=False,
                ),
            ]
        )
        db.commit()

        migration.op = type("MigrationOp", (), {"get_bind": lambda _self: db.connection()})()
        migration.upgrade()
        migration.upgrade()
        db.expire_all()

        plans = list(
            db.scalars(select(Plan).where(Plan.code.in_(["free", "huading"])).order_by(Plan.code))
        )
        assert [plan.code for plan in plans] == ["free", "huading"]
        assert [
            (
                plan.name,
                plan.price_cents,
                plan.period,
                plan.quota_credits,
                plan.is_active,
            )
            for plan in plans
        ] == [
            ("免费版", 0, "monthly", 0, True),
            ("Huading Plan", 0, "monthly", 0, True),
        ]
        basic = db.scalar(select(Plan).where(Plan.code == "basic"))
        assert (basic.price_cents, basic.quota_credits, basic.is_active) == (
            100,
            10_000_000,
            True,
        )


def test_new_registration_uses_free_plan_without_changing_existing_snapshot(auth_db) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        basic = Plan(
            code="basic",
            name="Basic",
            price_cents=0,
            period="monthly",
            quota_credits=10_000_000,
            is_active=True,
        )
        free = Plan(
            code="free",
            name="免费版",
            price_cents=0,
            period="monthly",
            quota_credits=0,
            is_active=True,
        )
        old_tenant = Tenant(slug="existing-studio", name="Existing Studio")
        db.add_all([basic, free, old_tenant])
        db.flush()
        old_subscription = Subscription(
            tenant_id=old_tenant.id,
            plan_id=basic.id,
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=29),
            quota_credits_total=10_000_000,
            quota_credits_used=123,
            quota_credits_reserved=45,
        )
        db.add(old_subscription)
        db.commit()
        old_subscription_id = old_subscription.id

    response = TestClient(app).post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": "new-free-studio",
            "tenant_name": "New Free Studio",
            "email": "free-owner@example.com",
            "password": "secret-pass",
        },
    )
    assert response.status_code == 201
    tenant_id = response.json()["data"]["tenant"]["id"]

    with auth_db() as db:
        new_subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        assert new_subscription is not None
        assert db.get(Plan, new_subscription.plan_id).code == "free"
        assert new_subscription.quota_credits_total == 0
        assert new_subscription.quota_credits_used == 0
        assert new_subscription.quota_credits_reserved == 0

        old_subscription = db.get(Subscription, old_subscription_id)
        assert db.get(Plan, old_subscription.plan_id).code == "basic"
        assert old_subscription.quota_credits_total == 10_000_000
        assert old_subscription.quota_credits_used == 123
        assert old_subscription.quota_credits_reserved == 45


def test_billing_reset_requires_recognizable_pg_dump_backup(tmp_path: Path) -> None:
    from scripts.ops.reset_billing_and_admin import BackupGateError, validate_pg_dump_backup

    missing = tmp_path / "missing.dump"
    empty = tmp_path / "empty.dump"
    invalid = tmp_path / "invalid.dump"
    valid = tmp_path / "valid.dump"
    empty.write_bytes(b"")
    invalid.write_bytes(b"not a postgres backup" * 100)
    valid.write_bytes(b"PGDMP" + b"\x00" * 2048)

    for backup_path in (missing, empty, invalid):
        with pytest.raises(BackupGateError):
            validate_pg_dump_backup(backup_path)

    assert validate_pg_dump_backup(valid) == valid.resolve()


def test_billing_reset_dry_run_apply_and_repeat_preserve_content(auth_db) -> None:
    from scripts.ops.reset_billing_and_admin import reset_billing_and_admin

    now = datetime.now(UTC)
    with auth_db() as db:
        basic = Plan(
            code="basic",
            name="Basic",
            price_cents=0,
            period="monthly",
            quota_credits=1000,
            is_active=True,
        )
        huading = Plan(
            code="huading",
            name="Huading Plan",
            price_cents=0,
            period="monthly",
            quota_credits=0,
            is_active=True,
        )
        target_tenant = Tenant(slug="huading-ai", name="华鼎AI")
        other_tenant = Tenant(slug="other-studio", name="Other Studio")
        db.add_all([basic, huading, target_tenant, other_tenant])
        db.flush()
        target_user = User(
            tenant_id=target_tenant.id,
            email="owner@huading.example",
            password_hash="not-used",
            role="creator",
        )
        target_subscription = Subscription(
            tenant_id=target_tenant.id,
            plan_id=basic.id,
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=29),
            quota_credits_total=500,
            quota_credits_used=120,
            quota_credits_reserved=30,
        )
        other_subscription = Subscription(
            tenant_id=other_tenant.id,
            plan_id=basic.id,
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=29),
            quota_credits_total=777,
            quota_credits_used=70,
            quota_credits_reserved=5,
        )
        task = VideoTask(
            tenant_id=target_tenant.id,
            status="done",
            mode="photo",
            video_mode="photo",
            progress=100,
        )
        asset = Asset(
            tenant_id=target_tenant.id,
            type="generated_image",
            source="generated",
            storage_key="tenants/huading/generated/keep.png",
            mime_type="image/png",
            size_bytes=123,
            status="ready",
        )
        db.add_all([target_user, target_subscription, other_subscription, task, asset])
        db.flush()
        db.add_all(
            [
                UsageRecord(
                    tenant_id=target_tenant.id,
                    subscription_id=target_subscription.id,
                    video_task_id=task.id,
                    capability="image",
                    provider="apimart",
                    model="gpt-image-2",
                    unit="image",
                    quantity=Decimal("1"),
                    credits=Decimal("10"),
                    cost_cents=4,
                    status="settled",
                ),
                UsageRecord(
                    tenant_id=other_tenant.id,
                    subscription_id=other_subscription.id,
                    capability="llm",
                    provider="deepseek",
                    model="deepseek-chat",
                    unit="token",
                    quantity=Decimal("100"),
                    credits=Decimal("1"),
                    cost_cents=1,
                    status="settled",
                ),
            ]
        )
        db.commit()
        target_user_id = target_user.id
        target_subscription_id = target_subscription.id
        other_subscription_id = other_subscription.id
        task_id = task.id
        asset_id = asset.id

        preview = reset_billing_and_admin(db, tenant_slug="huading-ai", apply=False)
        assert preview.apply is False
        assert preview.usage_records_deleted == 2
        assert preview.subscriptions_reset == 2
        assert preview.before.usage_records == 2
        assert preview.after.usage_records == 0
        assert preview.after.target.role == "admin"
        assert preview.after.target.plan_code == "huading"
        assert preview.after.target.quota_credits_total == 10_000_000
        db.expire_all()
        assert len(list(db.scalars(select(UsageRecord)))) == 2
        assert db.get(Subscription, target_subscription_id).quota_credits_used == 120

        applied = reset_billing_and_admin(db, tenant_slug="huading-ai", apply=True)
        db.commit()
        assert applied.apply is True
        assert applied.usage_records_deleted == 2
        assert applied.subscriptions_reset == 2
        db.expire_all()
        assert list(db.scalars(select(UsageRecord))) == []
        target_user = db.get(User, target_user_id)
        target_subscription = db.get(Subscription, target_subscription_id)
        other_subscription = db.get(Subscription, other_subscription_id)
        assert target_user.role == "admin"
        assert db.get(Plan, target_subscription.plan_id).code == "huading"
        assert (
            target_subscription.quota_credits_total,
            target_subscription.quota_credits_used,
            target_subscription.quota_credits_reserved,
        ) == (10_000_000, 0, 0)
        assert (
            other_subscription.quota_credits_total,
            other_subscription.quota_credits_used,
            other_subscription.quota_credits_reserved,
        ) == (777, 0, 0)
        assert db.get(VideoTask, task_id) is not None
        assert db.get(Asset, asset_id).storage_key == "tenants/huading/generated/keep.png"

        repeated = reset_billing_and_admin(db, tenant_slug="huading-ai", apply=True)
        db.commit()
        assert repeated.usage_records_deleted == 0
        assert repeated.subscriptions_reset == 0
        assert repeated.after == applied.after


def test_billing_reset_cli_requires_backup_and_defaults_to_dry_run(
    auth_db,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from scripts.ops import reset_billing_and_admin as ops

    missing_backup = tmp_path / "missing.dump"

    def unexpected_session():
        raise AssertionError("database must not be opened before backup validation")

    monkeypatch.setattr(ops, "SessionLocal", unexpected_session, raising=False)
    assert (
        ops.main(
            [
                "--backup",
                str(missing_backup),
                "--tenant-slug",
                "huading-ai",
            ]
        )
        == 2
    )
    assert "backup" in capsys.readouterr().err.lower()

    now = datetime.now(UTC)
    with auth_db() as db:
        huading = Plan(
            code="huading",
            name="Huading Plan",
            price_cents=0,
            period="monthly",
            quota_credits=0,
            is_active=True,
        )
        tenant = Tenant(slug="huading-ai", name="华鼎AI")
        db.add_all([huading, tenant])
        db.flush()
        user = User(
            tenant_id=tenant.id,
            email="admin@huading.example",
            password_hash="not-used",
            role="creator",
        )
        subscription = Subscription(
            tenant_id=tenant.id,
            plan_id=huading.id,
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=29),
            quota_credits_total=500,
            quota_credits_used=7,
            quota_credits_reserved=3,
        )
        db.add_all([user, subscription])
        db.commit()
        tenant_id = tenant.id
        user_id = user.id
        subscription_id = subscription.id

    valid_backup = tmp_path / "valid.dump"
    valid_backup.write_bytes(b"PGDMP" + b"\x00" * 2048)
    monkeypatch.setattr(ops, "SessionLocal", auth_db, raising=False)
    assert (
        ops.main(
            [
                "--backup",
                str(valid_backup),
                "--email",
                "admin@huading.example",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["apply"] is False
    assert output["before"]["target"]["quota_credits_used"] == 7
    assert output["after"]["target"] == {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "email": "admin@huading.example",
        "role": "admin",
        "plan_code": "huading",
        "quota_credits_total": 10_000_000,
        "quota_credits_used": 0,
        "quota_credits_reserved": 0,
    }
    with auth_db() as db:
        assert db.get(User, user_id).role == "creator"
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_total == 500
        assert subscription.quota_credits_used == 7
        assert subscription.quota_credits_reserved == 3

    assert (
        ops.main(
            [
                "--backup",
                str(valid_backup),
                "--tenant-slug",
                "huading-ai",
                "--email",
                "admin@huading.example",
                "--apply",
            ]
        )
        == 0
    )
    applied_output = json.loads(capsys.readouterr().out)
    assert applied_output["apply"] is True
    assert applied_output["subscriptions_reset"] == 1
    with auth_db() as db:
        assert db.get(User, user_id).role == "admin"
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_total == 10_000_000
        assert subscription.quota_credits_used == 0
        assert subscription.quota_credits_reserved == 0


def test_assign_speaker_slot_apply_is_retired_and_does_not_touch_provider_config(auth_db) -> None:
    from scripts.ops.reset_billing_and_admin import (
        assign_speaker_slot,
    )

    with auth_db() as db:
        tenant = Tenant(slug="vip-studio", name="VIP Studio")
        platform_config = ProviderConfig(
            tenant_id=None,
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={
                "speaker_ids": ["S_platform_001"],
                "used_speaker_ids": {"S_platform_001": "platform-brand"},
            },
            is_active=True,
        )
        db.add_all([tenant, platform_config])
        db.commit()
        tenant_id = tenant.id

        preview = assign_speaker_slot(
            db,
            tenant_slug="vip-studio",
            speaker_id="S_tenant_exclusive_001",
            apply=False,
        )
        assert preview.apply is False
        assert preview.changed is True
        assert preview.config_created is True
        assert preview.speaker_ids == ("S_tenant_exclusive_001",)
        assert db.scalar(
            select(ProviderConfig).where(ProviderConfig.tenant_id == tenant_id)
        ) is None

        with pytest.raises(AppError) as exc:
            assign_speaker_slot(
                db,
                tenant_slug="vip-studio",
                speaker_id="S_tenant_exclusive_001",
                apply=True,
            )
        assert exc.value.code == "VOICE_SLOT_ASSIGNMENT_RETIRED"

        tenant_config = db.scalar(
            select(ProviderConfig).where(
                ProviderConfig.tenant_id == tenant_id,
                ProviderConfig.capability == "voice_clone",
                ProviderConfig.provider == "doubao-voice-clone",
            )
        )
        assert tenant_config is None
        db.refresh(platform_config)
        assert platform_config.config == {
            "speaker_ids": ["S_platform_001"],
            "used_speaker_ids": {"S_platform_001": "platform-brand"},
        }

        other_tenant = Tenant(slug="other-vip-studio", name="Other VIP Studio")
        db.add(other_tenant)
        db.commit()
        with pytest.raises(AppError) as other_exc:
            assign_speaker_slot(
                db,
                tenant_slug="other-vip-studio",
                speaker_id="S_tenant_exclusive_001",
                apply=True,
            )
        assert other_exc.value.code == "VOICE_SLOT_ASSIGNMENT_RETIRED"
        assert db.scalar(
            select(ProviderConfig).where(
                ProviderConfig.tenant_id == other_tenant.id,
                ProviderConfig.capability == "voice_clone",
            )
        ) is None


def test_assign_speaker_slot_apply_retires_before_env_platform_pool_scan(
    auth_db,
    monkeypatch,
) -> None:
    from scripts.ops import reset_billing_and_admin as ops

    monkeypatch.setattr(
        ops,
        "settings",
        SimpleNamespace(
            engine_doubao_voice_clone_speaker_ids=["S_env_platform_001"]
        ),
        raising=False,
    )
    with auth_db() as db:
        tenant = Tenant(slug="env-pool-vip", name="Env Pool VIP")
        db.add(tenant)
        db.commit()

        with pytest.raises(AppError) as exc:
            ops.assign_speaker_slot(
                db,
                tenant_slug="env-pool-vip",
                speaker_id="S_env_platform_001",
                apply=True,
            )

        assert exc.value.code == "VOICE_SLOT_ASSIGNMENT_RETIRED"
        assert db.scalar(
            select(ProviderConfig).where(ProviderConfig.tenant_id == tenant.id)
        ) is None


def test_assign_speaker_slot_cli_defaults_to_dry_run_and_requires_apply(
    auth_db,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from scripts.ops import reset_billing_and_admin as ops

    with auth_db() as db:
        tenant = Tenant(slug="cli-vip-studio", name="CLI VIP Studio")
        db.add(tenant)
        db.commit()
        tenant_id = tenant.id

    backup = tmp_path / "valid.dump"
    backup.write_bytes(b"PGDMP" + b"\x00" * 2048)
    monkeypatch.setattr(ops, "SessionLocal", auth_db, raising=False)
    base_args = [
        "assign-speaker-slot",
        "--backup",
        str(backup),
        "--tenant-slug",
        "cli-vip-studio",
        "--speaker-id",
        "S_cli_exclusive_001",
    ]

    assert ops.main(base_args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["operation"] == "assign-speaker-slot"
    assert preview["apply"] is False
    assert preview["changed"] is True
    with auth_db() as db:
        assert db.scalar(
            select(ProviderConfig).where(ProviderConfig.tenant_id == tenant_id)
        ) is None

    assert ops.main([*base_args, "--apply"]) != 0
    assert "VOICE_SLOT_ASSIGNMENT_RETIRED" in capsys.readouterr().err
    with auth_db() as db:
        config = db.scalar(
            select(ProviderConfig).where(ProviderConfig.tenant_id == tenant_id)
        )
        assert config is None
