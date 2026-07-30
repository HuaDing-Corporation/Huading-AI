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
    cost_cents: int = 0,
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
        cost_cents=cost_cents,
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
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))
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
        assert preview[image_usage.id]["new_cost_cents"] == 6
        assert preview[video_usage.id]["new_cost_cents"] == 231
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


def test_cost_backfill_skips_legacy_ecom_i2v_without_resolution_but_recomputes_recorded_resolution(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import apimart_costs
    from scripts.backfill_apimart_costs import backfill_provider_zero_costs

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))
    tenant_id = auth_context["tenant_id"]
    with auth_db() as db:
        subscription = _seed_subscription(db, tenant_id)
        db.add_all(
            [
                VideoTask(
                    id="backfill-ecom-legacy-no-resolution",
                    tenant_id=tenant_id,
                    status="done",
                    mode="seedance_i2v",
                    video_mode="seedance_i2v",
                    progress=100,
                    duration_sec=5,
                    params={"duration_sec": 5},
                ),
                VideoTask(
                    id="backfill-ecom-recorded-1080p",
                    tenant_id=tenant_id,
                    status="done",
                    mode="seedance_i2v",
                    video_mode="seedance_i2v",
                    progress=100,
                    duration_sec=5,
                    params={"resolution": "1080p", "duration_sec": 5},
                ),
            ]
        )
        db.flush()
        legacy_usage = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-ecom-legacy-no-resolution",
            provider="apimart",
            model="doubao-seedance-2.0",
            capability="video",
            unit="second",
            quantity=Decimal("5"),
            cost_cents=511,
        )
        recorded_usage = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-ecom-recorded-1080p",
            provider="apimart",
            model="doubao-seedance-2.0",
            capability="video",
            unit="second",
            quantity=Decimal("5"),
        )
        db.commit()

        summary = backfill_provider_zero_costs(db, apply=False)

        skipped = {item["usage_record_id"]: item for item in summary.skipped}
        assert skipped[legacy_usage.id]["reason"] == "ecom_i2v_no_resolution"
        preview = {item["usage_record_id"]: item for item in summary.preview}
        assert preview[recorded_usage.id]["new_cost_cents"] == 1240
        assert legacy_usage.id not in preview
        assert db.get(UsageRecord, legacy_usage.id).cost_cents == 511
        assert db.get(UsageRecord, recorded_usage.id).cost_cents == 0


def test_cost_backfill_dry_run_recomputes_only_apimart_nonzero_mismatches(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import apimart_costs
    from scripts.backfill_apimart_costs import backfill_provider_zero_costs

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))
    tenant_id = auth_context["tenant_id"]
    with auth_db() as db:
        subscription = _seed_subscription(db, tenant_id)
        db.add_all(
            [
                VideoTask(
                    id="backfill-wrong-image-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="photo",
                    video_mode="photo",
                    progress=100,
                ),
                VideoTask(
                    id="backfill-wrong-video-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="video_gen",
                    video_mode="video_gen",
                    progress=100,
                    duration_sec=5,
                    params={"resolution": "480p", "duration_sec": 5},
                ),
                VideoTask(
                    id="backfill-correct-image-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="photo",
                    video_mode="photo",
                    progress=100,
                ),
                VideoTask(
                    id="backfill-tolerated-video-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="video_gen",
                    video_mode="video_gen",
                    progress=100,
                    duration_sec=5,
                    params={"resolution": "480p", "duration_sec": 5},
                ),
                VideoTask(
                    id="backfill-non-apimart-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="avatar_talk",
                    video_mode="avatar_talk",
                    progress=100,
                    duration_sec=18,
                ),
                VideoTask(
                    id="backfill-direct-seedance-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="seedance_i2v",
                    video_mode="seedance_i2v",
                    progress=100,
                    duration_sec=5,
                ),
            ]
        )
        db.flush()
        wrong_image = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-wrong-image-task",
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            unit="image",
            quantity=Decimal("1"),
            cost_cents=1440,
        )
        wrong_video = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-wrong-video-task",
            provider="apimart",
            model="doubao-seedance-2.0",
            capability="video_gen",
            unit="second",
            quantity=Decimal("5"),
            cost_cents=24,
        )
        correct_image = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-correct-image-task",
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            unit="image",
            quantity=Decimal("1"),
            cost_cents=6,
        )
        tolerated_video = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-tolerated-video-task",
            provider="apimart",
            model="doubao-seedance-2.0",
            capability="video_gen",
            unit="second",
            quantity=Decimal("5"),
            cost_cents=232,
        )
        non_apimart_nonzero = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-non-apimart-task",
            provider="omnihuman",
            model="jimeng_realman_avatar_picture_omni_v15",
            capability="avatar",
            unit="second",
            quantity=Decimal("18"),
            cost_cents=1800,
        )
        direct_seedance = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-direct-seedance-task",
            provider="seedance",
            model="doubao-seedance-2-0-260128",
            capability="video",
            unit="second",
            quantity=Decimal("5"),
            cost_cents=999,
        )
        db.commit()

        summary = backfill_provider_zero_costs(db, apply=False)

        preview = {item["usage_record_id"]: item for item in summary.preview}
        assert preview[wrong_image.id]["old_cost_cents"] == 1440
        assert preview[wrong_image.id]["new_cost_cents"] == 6
        assert preview[wrong_video.id]["old_cost_cents"] == 24
        assert preview[wrong_video.id]["new_cost_cents"] == 231
        unchanged = {item["usage_record_id"]: item for item in summary.unchanged}
        assert unchanged[correct_image.id]["old_cost_cents"] == 6
        assert unchanged[correct_image.id]["new_cost_cents"] == 6
        assert unchanged[tolerated_video.id]["old_cost_cents"] == 232
        assert unchanged[tolerated_video.id]["new_cost_cents"] == 231
        anomalies = {item["usage_record_id"]: item for item in summary.anomalies}
        assert anomalies[wrong_image.id]["old_cost_cents"] == 1440
        assert anomalies[wrong_image.id]["new_cost_cents"] == 6
        assert anomalies[wrong_video.id]["old_cost_cents"] == 24
        assert anomalies[wrong_video.id]["new_cost_cents"] == 231
        assert non_apimart_nonzero.id not in preview
        assert non_apimart_nonzero.id not in unchanged
        assert direct_seedance.id not in preview
        assert direct_seedance.id not in unchanged
        assert db.get(UsageRecord, wrong_image.id).cost_cents == 1440
        assert db.get(UsageRecord, wrong_video.id).cost_cents == 24


def test_cost_backfill_apply_rewrites_apimart_mismatch_but_protects_others(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import apimart_costs
    from scripts.backfill_apimart_costs import backfill_provider_zero_costs

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))
    tenant_id = auth_context["tenant_id"]
    with auth_db() as db:
        subscription = _seed_subscription(db, tenant_id)
        db.add_all(
            [
                VideoTask(
                    id="backfill-apply-wrong-image-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="photo",
                    video_mode="photo",
                    progress=100,
                ),
                VideoTask(
                    id="backfill-apply-correct-image-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="photo",
                    video_mode="photo",
                    progress=100,
                ),
                VideoTask(
                    id="backfill-apply-omni-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="avatar_talk",
                    video_mode="avatar_talk",
                    progress=100,
                    duration_sec=18,
                ),
            ]
        )
        db.flush()
        wrong_image = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-apply-wrong-image-task",
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            unit="image",
            quantity=Decimal("1"),
            cost_cents=1440,
        )
        correct_image = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-apply-correct-image-task",
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            unit="image",
            quantity=Decimal("1"),
            cost_cents=6,
        )
        non_apimart_nonzero = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-apply-omni-task",
            provider="omnihuman",
            model="jimeng_realman_avatar_picture_omni_v15",
            capability="avatar",
            unit="second",
            quantity=Decimal("18"),
            cost_cents=1800,
        )
        db.commit()

        summary = backfill_provider_zero_costs(db, apply=True)

        assert summary.matched == 1
        assert summary.updated == 1
        assert db.get(UsageRecord, wrong_image.id).cost_cents == 6
        assert db.get(UsageRecord, correct_image.id).cost_cents == 6
        assert db.get(UsageRecord, non_apimart_nonzero.id).cost_cents == 1800


def test_cost_backfill_limit_counts_actionable_rows_not_unchanged_candidates(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import apimart_costs
    from scripts.backfill_apimart_costs import backfill_provider_zero_costs

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))
    tenant_id = auth_context["tenant_id"]
    with auth_db() as db:
        subscription = _seed_subscription(db, tenant_id)
        db.add_all(
            [
                VideoTask(
                    id="backfill-limit-correct-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="photo",
                    video_mode="photo",
                    progress=100,
                ),
                VideoTask(
                    id="backfill-limit-wrong-task",
                    tenant_id=tenant_id,
                    status="done",
                    mode="photo",
                    video_mode="photo",
                    progress=100,
                ),
            ]
        )
        db.flush()
        correct_image = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-limit-correct-task",
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            unit="image",
            quantity=Decimal("1"),
            cost_cents=6,
        )
        wrong_image = _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            task_id="backfill-limit-wrong-task",
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            unit="image",
            quantity=Decimal("1"),
            cost_cents=1440,
        )
        db.commit()

        summary = backfill_provider_zero_costs(db, apply=False, limit=1)

        preview_ids = {item["usage_record_id"] for item in summary.preview}
        unchanged_ids = {item["usage_record_id"] for item in summary.unchanged}
        assert preview_ids == {wrong_image.id}
        assert unchanged_ids == {correct_image.id}
