from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.db.models import Plan, Subscription, UsageRecord, VideoTask


def _seed_subscription(db, tenant_id: str) -> Subscription:
    now = datetime.now(UTC)
    plan = Plan(
        code=f"cost-backfill-{tenant_id}",
        name="Cost Backfill",
        price_cents=0,
        period="monthly",
        quota_credits=100,
        is_active=True,
    )
    db.add(plan)
    db.flush()
    subscription = Subscription(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status="active",
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=30),
        quota_credits_total=100,
        quota_credits_used=20,
        quota_credits_reserved=0,
    )
    db.add(subscription)
    db.flush()
    return subscription


def _usage(
    db,
    *,
    tenant_id: str,
    subscription_id: str,
    task_id: str,
    provider: str,
    model: str,
    capability: str,
    unit: str,
    quantity: Decimal,
    credits: Decimal = Decimal("0.00"),
) -> UsageRecord:
    usage = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        video_task_id=task_id,
        capability=capability,
        provider=provider,
        model=model,
        unit=unit,
        quantity=quantity,
        credits=credits,
        cost_cents=0,
        status="settled",
        settled_at=datetime.now(UTC),
    )
    db.add(usage)
    return usage


def test_cost_backfill_dry_run_previews_apimart_and_omnihuman_without_writing(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import apimart_costs, provider_costs
    from scripts.backfill_apimart_costs import backfill_provider_zero_costs

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.20"))
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_omnihuman_cny_per_sec",
        Decimal("1.0"),
        raising=False,
    )
    tenant_id = auth_context["tenant_id"]
    with auth_db() as db:
        subscription = _seed_subscription(db, tenant_id)
        db.add_all(
            [
                VideoTask(
                    id="backfill-image-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="photo",
                    video_mode="photo",
                    progress=100,
                ),
                VideoTask(
                    id="backfill-video-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="video_gen",
                    video_mode="video_gen",
                    progress=100,
                    duration_sec=5,
                    params={"resolution": "480p", "duration_sec": 5},
                ),
                VideoTask(
                    id="backfill-avatar-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="avatar_talk",
                    video_mode="avatar_talk",
                    progress=100,
                    duration_sec=18,
                ),
                VideoTask(
                    id="backfill-seedance-direct-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="seedance_i2v",
                    video_mode="seedance_i2v",
                    progress=100,
                    duration_sec=5,
                ),
                VideoTask(
                    id="backfill-tts-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="avatar_talk",
                    video_mode="avatar_talk",
                    progress=100,
                ),
                VideoTask(
                    id="backfill-deepseek-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="avatar_talk",
                    video_mode="avatar_talk",
                    progress=100,
                ),
            ]
        )
        db.flush()
        image_usage = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-image-task",
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            unit="image",
            quantity=Decimal("1"),
            credits=Decimal("20.00"),
        )
        video_usage = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-video-task",
            provider="apimart",
            model="doubao-seedance-2.0",
            capability="video_gen",
            unit="second",
            quantity=Decimal("5"),
            credits=Decimal("20.00"),
        )
        avatar_usage = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-avatar-task",
            provider="omnihuman",
            model="jimeng_realman_avatar_picture_omni_v15",
            capability="avatar",
            unit="second",
            quantity=Decimal("18"),
            credits=Decimal("20.00"),
        )
        seedance_direct_usage = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-seedance-direct-task",
            provider="seedance",
            model="doubao-seedance-2-0-260128",
            capability="video",
            unit="second",
            quantity=Decimal("5"),
            credits=Decimal("20.00"),
        )
        tts_usage = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-tts-task",
            provider="doubao-seed-tts",
            model="seed-tts-2.0",
            capability="tts",
            unit="char",
            quantity=Decimal("100"),
        )
        deepseek_token_usage = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-deepseek-task",
            provider="deepseek",
            model="deepseek-v4-flash",
            capability="llm",
            unit="token",
            quantity=Decimal("100000"),
        )
        deepseek_call_usage = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-deepseek-task",
            provider="deepseek",
            model="deepseek-v4-flash",
            capability="llm",
            unit="call",
            quantity=Decimal("1"),
        )
        db.commit()

        summary = backfill_provider_zero_costs(db, apply=False)

        assert summary.matched == 5
        assert summary.updated == 0
        preview = {item["usage_record_id"]: item for item in summary.preview}
        assert preview[image_usage.id]["new_cost_cents"] == 4
        assert preview[video_usage.id]["new_cost_cents"] == 238
        assert preview[avatar_usage.id]["new_cost_cents"] == 1800
        assert preview[tts_usage.id]["new_cost_cents"] == 3
        assert preview[deepseek_token_usage.id]["new_cost_cents"] == 10
        skipped = {item["usage_record_id"]: item for item in summary.skipped}
        assert skipped[deepseek_call_usage.id]["reason"] == "missing_token_quantity"
        assert seedance_direct_usage.id not in preview
        assert db.get(UsageRecord, image_usage.id).cost_cents == 0
        assert db.get(UsageRecord, video_usage.id).cost_cents == 0
        assert db.get(UsageRecord, avatar_usage.id).cost_cents == 0
        assert db.get(UsageRecord, tts_usage.id).cost_cents == 0
        assert db.get(UsageRecord, deepseek_token_usage.id).cost_cents == 0
        assert db.get(UsageRecord, deepseek_call_usage.id).cost_cents == 0


def test_cost_backfill_apply_writes_only_reconstructed_costs(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import provider_costs
    from scripts.backfill_apimart_costs import backfill_provider_zero_costs

    monkeypatch.setattr(
        provider_costs.settings,
        "engine_omnihuman_cny_per_sec",
        Decimal("1.0"),
        raising=False,
    )
    tenant_id = auth_context["tenant_id"]
    with auth_db() as db:
        subscription = _seed_subscription(db, tenant_id)
        db.add(
            VideoTask(
                id="backfill-apply-avatar-task",
                tenant_id=tenant_id,
                status="done",
                mode="avatar_talk",
                video_mode="avatar_talk",
                progress=100,
                duration_sec=18,
            )
        )
        db.flush()
        usage = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-apply-avatar-task",
            provider="omnihuman",
            model="jimeng_realman_avatar_picture_omni_v15",
            capability="avatar",
            unit="second",
            quantity=Decimal("18"),
            credits=Decimal("20.00"),
        )
        db.commit()

        summary = backfill_provider_zero_costs(db, apply=True)

        assert summary.matched == 1
        assert summary.updated == 1
        assert db.get(UsageRecord, usage.id).cost_cents == 1800
