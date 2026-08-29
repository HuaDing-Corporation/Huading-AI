from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.exceptions import AppError
from app.db.models import BillingOperation, CreditRefundGrant, Plan, Subscription, UsageRecord, User
from app.main import app
from app.services import admin_console, quota
from app.services.subscription import activate_subscription


def _billing_operation(
    *,
    tenant_id: str,
    user_id: str,
    operation: str,
    requested_credits: int,
    completed: bool = False,
) -> BillingOperation:
    now = datetime.now(UTC)
    return BillingOperation(
        tenant_id=tenant_id,
        user_id=user_id,
        operation=operation,
        idempotency_key=str(uuid4()),
        request_hash="a" * 64,
        quote_hash="b" * 64,
        pricing_snapshot={},
        requested_credits=Decimal(requested_credits),
        settled_credits=Decimal("0"),
        released_credits=Decimal(requested_credits if completed else 0),
        status="completed" if completed else "in_progress",
        completion_kind="rejected" if completed else None,
        completed_at=now if completed else None,
    )


def test_quota_without_active_subscription_still_reports_cross_period_values(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        subscription.status = "expired"
        subscription.period_end = now - timedelta(seconds=1)
        subscription.quota_credits_reserved = 30_000

        hold_operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=30_000,
        )
        refund_operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_renew",
            requested_credits=30_000,
            completed=True,
        )
        db.add_all([hold_operation, refund_operation])
        db.flush()
        db.add_all(
            [
                UsageRecord(
                    tenant_id=auth_context["tenant_id"],
                    subscription_id=subscription.id,
                    billing_operation_id=hold_operation.id,
                    billing_item_index=0,
                    billing_pricing_line_index=0,
                    capability="voice_clone",
                    provider="manual",
                    unit="call",
                    quantity=Decimal("1"),
                    credits=Decimal("30000"),
                    cost_cents=0,
                    status="reserved",
                ),
                CreditRefundGrant(
                    billing_operation_id=refund_operation.id,
                    tenant_id=auth_context["tenant_id"],
                    user_id=auth_context["user_id"],
                    source_subscription_id=subscription.id,
                    amount_credits=30_000,
                    status="pending",
                ),
            ]
        )
        db.commit()

    response = TestClient(app).get(
        "/api/v1/quota",
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "has_active_subscription": False,
        "active_subscription_id": None,
        "total": 0,
        "used": 0,
        "reserved": 0,
        "remaining": 0,
        "manual_fulfillment_held_credits": 30_000,
        "pending_refund_credits": 30_000,
    }


def test_activation_applies_pending_refund_grants_exactly_once(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        source = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        plan = db.get(Plan, source.plan_id)
        source.status = "expired"
        source.period_end = now - timedelta(seconds=1)
        refund_operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=30_000,
            completed=True,
        )
        db.add(refund_operation)
        db.flush()
        grant = CreditRefundGrant(
            billing_operation_id=refund_operation.id,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            source_subscription_id=source.id,
            amount_credits=30_000,
            status="pending",
        )
        db.add(grant)
        db.flush()

        activated = activate_subscription(
            db,
            tenant_id=auth_context["tenant_id"],
            plan=plan,
            period_start=now,
        )
        repeated = activate_subscription(
            db,
            tenant_id=auth_context["tenant_id"],
            plan=plan,
            period_start=now,
        )
        db.commit()
        activated_id = activated.id
        repeated_id = repeated.id
        grant_id = grant.id
        plan_quota = plan.quota_credits

    with auth_db() as db:
        active = db.get(Subscription, activated_id)
        applied_grant = db.get(CreditRefundGrant, grant_id)
        assert repeated_id == active.id
        assert active.quota_credits_total == plan_quota + 30_000
        assert applied_grant.status == "applied"
        assert applied_grant.target_subscription_id == active.id
        assert applied_grant.applied_at is not None


def test_active_quota_snapshot_keeps_manual_values_explanatory(
    auth_db,
    auth_context,
) -> None:
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        subscription.quota_credits_used = 11
        subscription.quota_credits_reserved = 30_000
        total = subscription.quota_credits_total
        subscription_id = subscription.id
        hold_operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_renew",
            requested_credits=30_000,
        )
        refund_operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=7_000,
            completed=True,
        )
        db.add_all([hold_operation, refund_operation])
        db.flush()
        for index in range(2):
            db.add(
                UsageRecord(
                    tenant_id=auth_context["tenant_id"],
                    subscription_id=subscription.id,
                    billing_operation_id=hold_operation.id,
                    billing_item_index=index,
                    billing_pricing_line_index=0,
                    capability="voice_clone",
                    provider="manual",
                    unit="call",
                    quantity=Decimal("1"),
                    credits=Decimal("15000"),
                    cost_cents=0,
                    status="reserved",
                )
            )
        db.add(
            CreditRefundGrant(
                billing_operation_id=refund_operation.id,
                tenant_id=auth_context["tenant_id"],
                user_id=auth_context["user_id"],
                source_subscription_id=subscription.id,
                amount_credits=7_000,
                status="pending",
            )
        )
        db.commit()

    response = TestClient(app).get("/api/v1/quota", headers=auth_context["headers"])

    assert response.status_code == 200
    assert response.json()["data"] == {
        "has_active_subscription": True,
        "active_subscription_id": subscription_id,
        "total": total,
        "used": 11,
        "reserved": 30_000,
        "remaining": total - 30_011,
        "manual_fulfillment_held_credits": 30_000,
        "pending_refund_credits": 7_000,
    }


def test_charging_without_active_subscription_still_fails_closed(
    auth_db,
    auth_context,
) -> None:
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        subscription.status = "expired"
        db.commit()

    with auth_db() as db, pytest.raises(AppError) as exc_info:
        quota.consume_active_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            credits=1,
        )

    assert exc_info.value.code == "SUBSCRIPTION_NOT_FOUND"


def test_admin_credit_adjustment_preserves_cross_period_holds_and_grants(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        active = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        source = Subscription(
            tenant_id=auth_context["tenant_id"],
            plan_id=active.plan_id,
            status="expired",
            period_start=now - timedelta(days=60),
            period_end=now - timedelta(days=30),
            quota_credits_total=30_000,
            quota_credits_used=0,
            quota_credits_reserved=30_000,
        )
        hold_operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=30_000,
        )
        refund_operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_renew",
            requested_credits=7_000,
            completed=True,
        )
        db.add_all([source, hold_operation, refund_operation])
        db.flush()
        usage = UsageRecord(
            tenant_id=auth_context["tenant_id"],
            subscription_id=source.id,
            billing_operation_id=hold_operation.id,
            billing_item_index=0,
            billing_pricing_line_index=0,
            capability="voice_clone",
            provider="manual",
            unit="call",
            quantity=Decimal("1"),
            credits=Decimal("30000"),
            cost_cents=0,
            status="reserved",
        )
        grant = CreditRefundGrant(
            billing_operation_id=refund_operation.id,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            source_subscription_id=source.id,
            amount_credits=7_000,
            status="pending",
        )
        db.add_all([usage, grant])
        db.commit()
        active_id = active.id
        source_id = source.id
        grant_id = grant.id
        total_before = active.quota_credits_total

    with auth_db() as db:
        actor = db.get(User, auth_context["user_id"])
        admin_console.adjust_credits(
            db,
            actor=actor,
            tenant_id=auth_context["tenant_id"],
            delta=123,
            reason="preserve cross-period values",
        )
        db.commit()

    with auth_db() as db:
        assert db.get(Subscription, active_id).quota_credits_total == total_before + 123
        assert db.get(Subscription, source_id).quota_credits_reserved == 30_000
        stored_grant = db.get(CreditRefundGrant, grant_id)
        assert stored_grant.status == "pending"
        assert stored_grant.target_subscription_id is None
