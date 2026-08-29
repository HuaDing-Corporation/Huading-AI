from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import AppError
from app.db.models import BillingOperation, CreditRefundGrant, Plan, Subscription, Tenant
from app.services.transaction_retry import run_db_transaction_with_retry

logger = structlog.get_logger(__name__)

_DEFAULT_PLAN_CODE = "free"


@dataclass(frozen=True)
class RefundSubscriptionContext:
    source_subscription: Subscription
    current_subscription: Subscription | None


@dataclass(frozen=True)
class CreditRefundDisposition:
    kind: Literal["not_required", "pending", "applied"]
    grant_id: str | None
    target_subscription_id: str | None
    amount_credits: int


def _period_end(start: datetime, period: str) -> datetime:
    """Calendar-correct period end: +1 month or +1 year, day clamped to month length."""
    if period == "yearly":
        year, month = start.year + 1, start.month
    else:  # monthly — Plan.period CheckConstraint only allows monthly|yearly
        month = start.month + 1
        year = start.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def default_plan(db: Session) -> Plan | None:
    """The plan a new tenant subscribes to: 'free' if present, else cheapest active."""
    plan = db.scalar(select(Plan).where(Plan.code == _DEFAULT_PLAN_CODE, Plan.is_active.is_(True)))
    if plan is not None:
        return plan
    return db.scalar(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_cents.asc()))


def lock_tenant_for_subscription_lifecycle(
    db: Session,
    *,
    tenant_id: str,
) -> Tenant:
    tenant = db.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if tenant is None:
        raise ValueError(f"Tenant not found: {tenant_id}")
    return tenant


def current_active_subscription_for_update(
    db: Session,
    *,
    tenant_id: str,
    now: datetime,
) -> Subscription | None:
    return db.scalar(
        select(Subscription)
        .where(
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
            Subscription.period_start <= now,
            Subscription.period_end >= now,
        )
        .order_by(Subscription.id)
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _billing_invariant(message: str) -> AppError:
    return AppError(
        message,
        code="BILLING_INVARIANT_VIOLATION",
        status_code=500,
    )


def refund_subscriptions_for_update(
    db: Session,
    *,
    tenant_id: str,
    source_subscription_id: str,
    now: datetime,
) -> RefundSubscriptionContext:
    """Lock source/current subscriptions by PK after Tenant and BillingOperation.

    Manual resolve callers must invoke this before locking UsageRecords. The
    returned context stays stable because the caller already owns the Tenant
    lifecycle lock.
    """
    current_subscription_id = db.scalar(
        select(Subscription.id)
        .where(
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
            Subscription.period_start <= now,
            Subscription.period_end >= now,
        )
        .order_by(Subscription.id)
        .limit(1)
    )
    subscription_ids = sorted(
        {source_subscription_id, current_subscription_id} - {None}
    )
    locked = list(
        db.scalars(
            select(Subscription)
            .where(
                Subscription.tenant_id == tenant_id,
                Subscription.id.in_(subscription_ids),
            )
            .order_by(Subscription.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    by_id = {subscription.id: subscription for subscription in locked}
    source_subscription = by_id.get(source_subscription_id)
    if source_subscription is None:
        raise _billing_invariant("Refund source subscription is invalid.")
    return RefundSubscriptionContext(
        source_subscription=source_subscription,
        current_subscription=(
            by_id.get(current_subscription_id)
            if current_subscription_id is not None
            else None
        ),
    )


def decide_credit_refund(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    billing_operation: BillingOperation,
    subscriptions: RefundSubscriptionContext,
    amount_credits: int,
    decided_at: datetime,
) -> CreditRefundDisposition:
    """Return the authoritative refund disposition and lock its grant by PK.

    The caller must already hold Tenant, BillingOperation, the context's
    Subscriptions, and any UsageRecords, in that order. This helper performs no
    order resolution or usage release, so Task 11 can call it at the RefundGrant
    stage without introducing a reverse lock edge.
    """
    if (
        isinstance(amount_credits, bool)
        or not isinstance(amount_credits, int)
        or amount_credits <= 0
        or Decimal(amount_credits) != billing_operation.requested_credits
    ):
        raise _billing_invariant("Refund amount is invalid.")
    if (
        billing_operation.tenant_id != tenant_id
        or billing_operation.user_id != user_id
        or billing_operation.operation
        not in {
            "doubao_brand_voice_order_create",
            "doubao_brand_voice_order_renew",
        }
        or subscriptions.source_subscription.tenant_id != tenant_id
        or (
            subscriptions.current_subscription is not None
            and subscriptions.current_subscription.tenant_id != tenant_id
        )
    ):
        raise _billing_invariant("Refund ownership is invalid.")
    grant = db.scalar(
        select(CreditRefundGrant)
        .where(CreditRefundGrant.billing_operation_id == billing_operation.id)
        .order_by(CreditRefundGrant.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if grant is not None and (
        grant.tenant_id != tenant_id
        or grant.user_id != user_id
        or grant.source_subscription_id != subscriptions.source_subscription.id
        or grant.amount_credits != amount_credits
    ):
        raise _billing_invariant("Existing refund grant conflicts with the operation.")
    if (
        subscriptions.current_subscription is not None
        and subscriptions.current_subscription.id
        == subscriptions.source_subscription.id
    ):
        if grant is not None:
            raise _billing_invariant("Current-period refund grant already exists.")
        return CreditRefundDisposition(
            kind="not_required",
            grant_id=None,
            target_subscription_id=None,
            amount_credits=0,
        )
    if grant is not None and grant.status == "applied":
        return CreditRefundDisposition(
            kind="applied",
            grant_id=grant.id,
            target_subscription_id=grant.target_subscription_id,
            amount_credits=grant.amount_credits,
        )
    if (
        grant is not None
        and grant.status == "pending"
        and subscriptions.current_subscription is None
    ):
        return CreditRefundDisposition(
            kind="pending",
            grant_id=grant.id,
            target_subscription_id=None,
            amount_credits=grant.amount_credits,
        )
    if (
        grant is not None
        and grant.status == "pending"
        and subscriptions.current_subscription is not None
    ):
        target = subscriptions.current_subscription
        target.quota_credits_total += grant.amount_credits
        target.updated_at = decided_at
        grant.status = "applied"
        grant.target_subscription_id = target.id
        grant.applied_at = decided_at
        db.flush([target, grant])
        return CreditRefundDisposition(
            kind="applied",
            grant_id=grant.id,
            target_subscription_id=target.id,
            amount_credits=grant.amount_credits,
        )
    if subscriptions.current_subscription is None and grant is None:
        grant = CreditRefundGrant(
            billing_operation_id=billing_operation.id,
            tenant_id=tenant_id,
            user_id=user_id,
            source_subscription_id=subscriptions.source_subscription.id,
            amount_credits=amount_credits,
            status="pending",
        )
        db.add(grant)
        db.flush([grant])
        return CreditRefundDisposition(
            kind="pending",
            grant_id=grant.id,
            target_subscription_id=None,
            amount_credits=amount_credits,
        )
    if subscriptions.current_subscription is not None and grant is None:
        target = subscriptions.current_subscription
        grant = CreditRefundGrant(
            billing_operation_id=billing_operation.id,
            tenant_id=tenant_id,
            user_id=user_id,
            source_subscription_id=subscriptions.source_subscription.id,
            target_subscription_id=target.id,
            amount_credits=amount_credits,
            status="applied",
            applied_at=decided_at,
        )
        target.quota_credits_total += amount_credits
        target.updated_at = decided_at
        db.add(grant)
        db.flush([target, grant])
        return CreditRefundDisposition(
            kind="applied",
            grant_id=grant.id,
            target_subscription_id=target.id,
            amount_credits=amount_credits,
        )
    raise _billing_invariant("Refund disposition is not implemented.")


def apply_pending_refund_grants(
    db: Session,
    *,
    tenant_id: str,
    target_subscription: Subscription,
    applied_at: datetime,
) -> int:
    grants = list(
        db.scalars(
            select(CreditRefundGrant)
            .where(
                CreditRefundGrant.tenant_id == tenant_id,
                CreditRefundGrant.status == "pending",
            )
            .order_by(CreditRefundGrant.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    applied_credits = sum(grant.amount_credits for grant in grants)
    if applied_credits:
        target_subscription.quota_credits_total += applied_credits
        target_subscription.updated_at = applied_at
        for grant in grants:
            grant.status = "applied"
            grant.target_subscription_id = target_subscription.id
            grant.applied_at = applied_at
        db.flush([target_subscription, *grants])
    return applied_credits


def activate_subscription(
    db: Session,
    *,
    tenant_id: str,
    plan: Plan,
    period_start: datetime,
) -> Subscription:
    lock_tenant_for_subscription_lifecycle(db, tenant_id=tenant_id)
    return _activate_subscription_under_tenant_lock(
        db,
        tenant_id=tenant_id,
        plan=plan,
        period_start=period_start,
    )


def _activate_subscription_under_tenant_lock(
    db: Session,
    *,
    tenant_id: str,
    plan: Plan,
    period_start: datetime,
) -> Subscription:
    subscription = current_active_subscription_for_update(
        db,
        tenant_id=tenant_id,
        now=period_start,
    )
    if subscription is None:
        subscription = Subscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            status="active",
            period_start=period_start,
            period_end=_period_end(period_start, plan.period),
            quota_credits_total=plan.quota_credits,
            quota_credits_used=0,
            quota_credits_reserved=0,
        )
        db.add(subscription)
        db.flush([subscription])
    apply_pending_refund_grants(
        db,
        tenant_id=tenant_id,
        target_subscription=subscription,
        applied_at=period_start,
    )
    return subscription


def activate_subscription_with_retry(
    session_factory: sessionmaker[Session],
    *,
    tenant_id: str,
    plan_id: str,
    period_start: datetime,
) -> Subscription:
    """Activate outside onboarding with bounded fresh-session database retry."""

    def transaction(db: Session) -> Subscription:
        lock_tenant_for_subscription_lifecycle(db, tenant_id=tenant_id)
        plan = db.get(Plan, plan_id)
        if plan is None:
            raise _billing_invariant("Activation plan is missing.")
        return _activate_subscription_under_tenant_lock(
            db,
            tenant_id=tenant_id,
            plan=plan,
            period_start=period_start,
        )

    return run_db_transaction_with_retry(session_factory, transaction)


def create_default_subscription(db: Session, tenant_id: str) -> Subscription | None:
    """Create an active subscription for a new tenant from the default plan.

    Runs in the caller's transaction (no commit here). When no active plan is
    configured (seeds not run) this logs a clear warning and returns None instead
    of a 500 — registration still succeeds; the tenant simply has no subscription
    until a plan exists. Production always seeds a plan, so this is the normal path
    that fixes the "new tenant has no subscription → SUBSCRIPTION_NOT_FOUND at 下单"
    bug (P0-B).
    """
    lock_tenant_for_subscription_lifecycle(db, tenant_id=tenant_id)
    plan = default_plan(db)
    if plan is None:
        logger.warning("onboarding.no_active_plan", tenant_id=tenant_id)
        return None
    now = datetime.now(UTC)
    return _activate_subscription_under_tenant_lock(
        db,
        tenant_id=tenant_id,
        plan=plan,
        period_start=now,
    )
