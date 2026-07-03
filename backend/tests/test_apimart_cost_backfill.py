from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.db.models import Plan, Subscription, UsageRecord, VideoTask


def _seed_settled_apimart_usage(db, tenant_id: str) -> str:
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
    task = VideoTask(
        id="apimart-cost-backfill-task",
        tenant_id=tenant_id,
        status="done",
        mode="photo",
        video_mode="photo",
        progress=100,
    )
    db.add_all([subscription, task])
    db.flush()
    usage = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=task.id,
        capability="image",
        provider="apimart",
        model="gpt-image-2",
        unit="image",
        quantity=Decimal("1"),
        credits=Decimal("2.50"),
        cost_cents=0,
        status="settled",
        settled_at=now,
    )
    db.add(usage)
    db.commit()
    return usage.id


def test_apimart_cost_backfill_dry_run_does_not_write_and_apply_is_idempotent(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import apimart_costs
    from scripts.backfill_apimart_costs import backfill_apimart_zero_costs

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.20"))
    with auth_db() as db:
        usage_id = _seed_settled_apimart_usage(db, auth_context["tenant_id"])
        dry_run = backfill_apimart_zero_costs(db, apply=False)
        usage = db.get(UsageRecord, usage_id)
        assert usage is not None
        assert usage.cost_cents == 0

        applied = backfill_apimart_zero_costs(db, apply=True)
        db.commit()
        usage = db.get(UsageRecord, usage_id)
        assert usage is not None
        assert usage.cost_cents == 180

        second = backfill_apimart_zero_costs(db, apply=True)
        db.commit()

    assert dry_run.matched == 1
    assert dry_run.updated == 0
    assert dry_run.preview[0]["new_cost_cents"] == 180
    assert applied.matched == 1
    assert applied.updated == 1
    assert second.matched == 0
    assert second.updated == 0
