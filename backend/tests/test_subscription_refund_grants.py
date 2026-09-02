from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import AppError
from app.db.models import (
    Asset,
    BillingOperation,
    BrandVoiceOrder,
    CreditRefundGrant,
    Plan,
    Subscription,
    UsageRecord,
    User,
)
from app.main import app
from app.services import admin_console, quota
from app.services import subscription as subscription_service
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


def _decide_refund(
    db,
    *,
    tenant_id: str,
    user_id: str,
    operation_id: str,
    source_subscription_id: str,
    amount_credits: int,
    now: datetime,
):
    subscription_service.lock_tenant_for_subscription_lifecycle(
        db,
        tenant_id=tenant_id,
    )
    locked_operation = db.scalar(
        select(BillingOperation).where(BillingOperation.id == operation_id).with_for_update()
    )
    context = subscription_service.refund_subscriptions_for_update(
        db,
        tenant_id=tenant_id,
        source_subscription_id=source_subscription_id,
        now=now,
    )
    return subscription_service.decide_credit_refund(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        billing_operation=locked_operation,
        subscriptions=context,
        amount_credits=amount_credits,
        decided_at=now,
    )


def test_rejected_order_read_requeries_pending_grant_after_activation(db_session) -> None:
    from app.services.brand_voice_orders import brand_voice_order_read

    now = datetime(2026, 8, 29, tzinfo=UTC)
    source = db_session.get(Subscription, "subscription-a")
    source.status = "expired"
    source.period_end = now - timedelta(seconds=1)
    operation = _billing_operation(
        tenant_id="tenant-a",
        user_id="user-a",
        operation="doubao_brand_voice_order_create",
        requested_credits=30_000,
        completed=True,
    )
    operation.completed_at = now
    order_id = str(uuid4())
    operation.result_type = "brand_voice_order"
    operation.result_id = order_id
    operation.result_payload = {}
    asset = Asset(
        id="refund-order-audio",
        tenant_id="tenant-a",
        type="audio",
        source="upload",
        storage_key="tenants/tenant-a/uploads/refund-order.wav",
        mime_type="audio/wav",
        status="ready",
    )
    db_session.add_all([operation, asset])
    db_session.flush()
    order = BrandVoiceOrder(
        id=order_id,
        tenant_id="tenant-a",
        user_id="user-a",
        order_type="create",
        requested_name="Refund order",
        source_audio_asset_id=asset.id,
        source_metadata_snapshot={},
        consent_confirmed_at=now,
        billing_operation_id=operation.id,
        status="rejected",
        resolver_user_id="user-a",
        rejected_at=now,
        rejection_reason="not deliverable",
    )
    grant = CreditRefundGrant(
        billing_operation_id=operation.id,
        tenant_id="tenant-a",
        user_id="user-a",
        source_subscription_id=source.id,
        amount_credits=30_000,
        status="pending",
    )
    usage = UsageRecord(
        tenant_id="tenant-a",
        subscription_id=source.id,
        billing_operation_id=operation.id,
        billing_item_index=0,
        billing_pricing_line_index=0,
        capability="voice_clone",
        provider="doubao-voice-clone",
        model="manual_fulfillment",
        unit="call",
        quantity=Decimal("1"),
        credits=Decimal("30000"),
        cost_cents=0,
        status="released",
        settled_at=now,
    )
    db_session.add_all([order, grant, usage])
    db_session.commit()

    pending = brand_voice_order_read(db_session, order=order)
    assert pending.refund_disposition == "pending_next_subscription"
    assert pending.refund_grant_status == "pending"
    assert pending.refund_applied_at is None

    plan = db_session.get(Plan, source.plan_id)
    activated = activate_subscription(
        db_session,
        tenant_id="tenant-a",
        plan=plan,
        period_start=now,
    )
    db_session.commit()
    db_session.refresh(order)
    applied = brand_voice_order_read(db_session, order=order)
    assert applied.refund_disposition == "current_subscription_credited"
    assert applied.refund_grant_status == "applied"
    assert applied.refund_applied_at is not None
    assert activated.quota_credits_total == plan.quota_credits + 30_000


def test_quota_without_active_subscription_still_reports_cross_period_values(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
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
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
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


def test_refund_decision_skips_grant_while_source_is_current(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        source = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        total_before = source.quota_credits_total
        operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=30_000,
            completed=True,
        )
        db.add(operation)
        db.flush()

        disposition = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        replay = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        db.commit()

        assert disposition.kind == "not_required"
        assert replay == disposition
        assert disposition.grant_id is None
        assert source.quota_credits_total == total_before
        assert db.scalar(select(func.count()).select_from(CreditRefundGrant)) == 0


def test_refund_decision_creates_pending_grant_without_current_target(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        source = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        source.status = "expired"
        source.period_end = now - timedelta(seconds=1)
        operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_renew",
            requested_credits=30_000,
            completed=True,
        )
        db.add(operation)
        db.flush()

        disposition = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        replay = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        db.commit()

        grant = db.get(CreditRefundGrant, disposition.grant_id)
        assert disposition.kind == "pending"
        assert replay == disposition
        assert disposition.target_subscription_id is None
        assert disposition.amount_credits == 30_000
        assert type(disposition.amount_credits) is int
        assert type(replay.amount_credits) is int
        assert grant.status == "pending"
        assert grant.source_subscription_id == source.id
        assert grant.target_subscription_id is None
        assert grant.applied_at is None


def test_refund_decision_applies_immediately_to_current_target(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        target = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        target_total_before = target.quota_credits_total
        source = Subscription(
            tenant_id=auth_context["tenant_id"],
            plan_id=target.plan_id,
            status="expired",
            period_start=now - timedelta(days=60),
            period_end=now - timedelta(days=30),
            quota_credits_total=30_000,
            quota_credits_used=0,
            quota_credits_reserved=0,
        )
        operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=30_000,
            completed=True,
        )
        db.add_all([source, operation])
        db.flush()

        disposition = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        replay = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        db.commit()

        grant = db.get(CreditRefundGrant, disposition.grant_id)
        refreshed_target = db.get(Subscription, target.id)
        assert disposition.kind == "applied"
        assert replay == disposition
        assert disposition.target_subscription_id == target.id
        assert disposition.amount_credits == 30_000
        assert type(disposition.amount_credits) is int
        assert type(replay.amount_credits) is int
        assert refreshed_target.quota_credits_total == target_total_before + 30_000
        assert grant.status == "applied"
        assert grant.target_subscription_id == target.id
        assert grant.applied_at.replace(tzinfo=UTC) == now


@pytest.mark.parametrize(
    ("user_id", "amount_credits"),
    (
        ("wrong-user", 30_000),
        (None, True),
        (None, Decimal("30000")),
        (None, 1.5),
        (None, 0),
        (None, 29_999),
    ),
)
def test_refund_decision_fails_closed_for_ownership_or_amount_mismatch(
    auth_db,
    auth_context,
    user_id,
    amount_credits,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        source = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=30_000,
            completed=True,
        )
        db.add(operation)
        db.flush()

        with pytest.raises(AppError) as exc_info:
            _decide_refund(
                db,
                tenant_id=auth_context["tenant_id"],
                user_id=user_id or auth_context["user_id"],
                operation_id=operation.id,
                source_subscription_id=source.id,
                amount_credits=amount_credits,
                now=now,
            )

        assert exc_info.value.code == "BILLING_INVARIANT_VIOLATION"
        assert db.scalar(select(func.count()).select_from(CreditRefundGrant)) == 0


def test_refund_decision_fails_closed_on_conflicting_existing_grant(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        source = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        source.status = "expired"
        operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=30_000,
            completed=True,
        )
        db.add(operation)
        db.flush()
        conflicting = CreditRefundGrant(
            billing_operation_id=operation.id,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            source_subscription_id=source.id,
            amount_credits=7_000,
            status="pending",
        )
        db.add(conflicting)
        db.flush()

        with pytest.raises(AppError) as exc_info:
            _decide_refund(
                db,
                tenant_id=auth_context["tenant_id"],
                user_id=auth_context["user_id"],
                operation_id=operation.id,
                source_subscription_id=source.id,
                amount_credits=30_000,
                now=now,
            )

        assert exc_info.value.code == "BILLING_INVARIANT_VIOLATION"
        assert conflicting.amount_credits == 7_000
        assert conflicting.status == "pending"


def test_refund_decision_replay_returns_same_pending_grant(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        source = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        source.status = "expired"
        operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=30_000,
            completed=True,
        )
        db.add(operation)
        db.flush()
        first = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        replay = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        db.commit()

        assert replay == first
        assert replay.kind == "pending"
        assert (
            db.scalar(
                select(func.count())
                .select_from(CreditRefundGrant)
                .where(CreditRefundGrant.billing_operation_id == operation.id)
            )
            == 1
        )


def test_refund_decision_promotes_pending_grant_when_target_appears(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        source = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        source.status = "expired"
        plan = db.get(Plan, source.plan_id)
        operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_renew",
            requested_credits=30_000,
            completed=True,
        )
        db.add(operation)
        db.flush()
        pending = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        target = Subscription(
            tenant_id=auth_context["tenant_id"],
            plan_id=plan.id,
            status="active",
            period_start=now,
            period_end=now + timedelta(days=30),
            quota_credits_total=plan.quota_credits,
            quota_credits_used=0,
            quota_credits_reserved=0,
        )
        db.add(target)
        db.flush()
        applied = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        replay = _decide_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation_id=operation.id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            now=now,
        )
        db.commit()

        assert pending.kind == "pending"
        assert applied.kind == "applied"
        assert replay == applied
        assert applied.grant_id == pending.grant_id
        assert target.quota_credits_total == plan.quota_credits + 30_000
        assert (
            db.scalar(
                select(func.count())
                .select_from(CreditRefundGrant)
                .where(CreditRefundGrant.billing_operation_id == operation.id)
            )
            == 1
        )


def test_activation_orchestrator_retries_with_fresh_sessions_and_applies_grant_once(
    auth_db,
    auth_context,
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        source = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        source.status = "expired"
        source.period_end = now - timedelta(seconds=1)
        plan_id = source.plan_id
        plan_quota = db.get(Plan, plan_id).quota_credits
        operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=30_000,
            completed=True,
        )
        db.add(operation)
        db.flush()
        grant = CreditRefundGrant(
            billing_operation_id=operation.id,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            source_subscription_id=source.id,
            amount_credits=30_000,
            status="pending",
        )
        db.add(grant)
        db.commit()
        grant_id = grant.id

    failures = ["40P01", "40001"]
    opened: list[int] = []
    rollbacks: list[int] = []
    closes: list[int] = []
    commits: list[int] = []

    class FakePostgresError(Exception):
        def __init__(self, sqlstate: str) -> None:
            self.sqlstate = sqlstate

    class FaultInjectingSession(Session):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.attempt_id = len(opened)
            opened.append(self.attempt_id)

        def scalar(self, statement, *args, **kwargs):
            if failures and "FROM tenants" in str(statement):
                raise OperationalError(
                    "activation",
                    {},
                    FakePostgresError(failures.pop(0)),
                )
            return super().scalar(statement, *args, **kwargs)

        def rollback(self) -> None:
            rollbacks.append(self.attempt_id)
            super().rollback()

        def commit(self) -> None:
            commits.append(self.attempt_id)
            super().commit()

        def close(self) -> None:
            closes.append(self.attempt_id)
            super().close()

    retry_factory = sessionmaker(
        bind=auth_db.kw["bind"],
        class_=FaultInjectingSession,
        autoflush=False,
        autocommit=False,
    )
    monkeypatch.setattr("app.services.transaction_retry.time.sleep", lambda _delay: None)

    activated = subscription_service.activate_subscription_with_retry(
        retry_factory,
        tenant_id=auth_context["tenant_id"],
        plan_id=plan_id,
        period_start=now,
    )

    assert opened == [0, 1, 2]
    assert rollbacks == [0, 1]
    assert commits == [2]
    assert closes == [0, 1, 2]
    with auth_db() as db:
        stored_grant = db.get(CreditRefundGrant, grant_id)
        stored_subscription = db.get(Subscription, activated.id)
        assert stored_grant.status == "applied"
        assert stored_grant.target_subscription_id == stored_subscription.id
        assert stored_subscription.quota_credits_total == plan_quota + 30_000


def test_activation_orchestrator_surfaces_third_serialization_failure(
    auth_db,
    auth_context,
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        plan_id = db.scalar(
            select(Subscription.plan_id).where(Subscription.tenant_id == auth_context["tenant_id"])
        )

    attempts: list[int] = []
    rollbacks: list[int] = []
    closes: list[int] = []
    commits: list[int] = []

    class SerializationFailure(Exception):
        sqlstate = "40001"

    class AlwaysFailingSession(Session):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.attempt_id = len(attempts)
            attempts.append(self.attempt_id)

        def scalar(self, statement, *args, **kwargs):
            if "FROM tenants" in str(statement):
                raise OperationalError("activation", {}, SerializationFailure())
            return super().scalar(statement, *args, **kwargs)

        def rollback(self) -> None:
            rollbacks.append(self.attempt_id)
            super().rollback()

        def commit(self) -> None:
            commits.append(self.attempt_id)
            super().commit()

        def close(self) -> None:
            closes.append(self.attempt_id)
            super().close()

    retry_factory = sessionmaker(
        bind=auth_db.kw["bind"],
        class_=AlwaysFailingSession,
        autoflush=False,
        autocommit=False,
    )
    monkeypatch.setattr("app.services.transaction_retry.time.sleep", lambda _delay: None)

    with pytest.raises(OperationalError):
        subscription_service.activate_subscription_with_retry(
            retry_factory,
            tenant_id=auth_context["tenant_id"],
            plan_id=plan_id,
            period_start=now,
        )

    assert attempts == [0, 1, 2]
    assert rollbacks == [0, 1, 2]
    assert closes == [0, 1, 2]
    assert commits == []


def test_active_quota_snapshot_keeps_manual_values_explanatory(
    auth_db,
    auth_context,
) -> None:
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
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
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
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
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
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


def test_change_plan_preserves_manual_reservation_and_all_refund_grants(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        active = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        free_plan = Plan(
            code="free",
            name="Free",
            price_cents=0,
            period="monthly",
            quota_credits=123,
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
        pending_operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_renew",
            requested_credits=7_000,
            completed=True,
        )
        applied_operation = _billing_operation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            requested_credits=5_000,
            completed=True,
        )
        db.add_all(
            [
                free_plan,
                source,
                hold_operation,
                pending_operation,
                applied_operation,
            ]
        )
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
        pending_grant = CreditRefundGrant(
            billing_operation_id=pending_operation.id,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            source_subscription_id=source.id,
            amount_credits=7_000,
            status="pending",
        )
        applied_at = now - timedelta(hours=1)
        applied_grant = CreditRefundGrant(
            billing_operation_id=applied_operation.id,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            source_subscription_id=source.id,
            target_subscription_id=active.id,
            amount_credits=5_000,
            status="applied",
            applied_at=applied_at,
        )
        active.quota_credits_total += 5_000
        db.add_all([usage, pending_grant, applied_grant])
        db.commit()
        ids = {
            "active": active.id,
            "free_plan": free_plan.id,
            "source": source.id,
            "usage": usage.id,
            "hold_operation": hold_operation.id,
            "pending_grant": pending_grant.id,
            "applied_grant": applied_grant.id,
        }
        active_total_before = active.quota_credits_total

    with auth_db() as db:
        actor = db.get(User, auth_context["user_id"])
        admin_console.change_plan(
            db,
            actor=actor,
            tenant_id=auth_context["tenant_id"],
            plan_code="free",
            reason="preserve manual cross-period state",
        )
        db.commit()

    with auth_db() as db:
        stored_active = db.get(Subscription, ids["active"])
        stored_source = db.get(Subscription, ids["source"])
        stored_usage = db.get(UsageRecord, ids["usage"])
        stored_operation = db.get(BillingOperation, ids["hold_operation"])
        stored_pending = db.get(CreditRefundGrant, ids["pending_grant"])
        stored_applied = db.get(CreditRefundGrant, ids["applied_grant"])
        assert stored_active.plan_id == ids["free_plan"]
        assert stored_active.quota_credits_total == active_total_before
        assert stored_source.quota_credits_reserved == 30_000
        assert stored_usage.subscription_id == stored_source.id
        assert stored_usage.billing_operation_id == stored_operation.id
        assert stored_usage.status == "reserved"
        assert stored_operation.status == "in_progress"
        assert stored_operation.requested_credits == Decimal("30000")
        assert (
            stored_pending.status,
            stored_pending.target_subscription_id,
            stored_pending.applied_at,
        ) == ("pending", None, None)
        assert (
            stored_applied.status,
            stored_applied.target_subscription_id,
            stored_applied.applied_at.replace(tzinfo=UTC),
        ) == ("applied", stored_active.id, applied_at)
        assert (
            db.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.tenant_id == auth_context["tenant_id"])
            )
            == 2
        )
