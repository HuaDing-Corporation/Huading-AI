from __future__ import annotations

import calendar
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CreditRefundGrant, Plan, Subscription, Tenant

logger = structlog.get_logger(__name__)

_DEFAULT_PLAN_CODE = "free"


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


def create_default_subscription(db: Session, tenant_id: str) -> Subscription | None:
    """Create an active subscription for a new tenant from the default plan.

    Runs in the caller's transaction (no commit here). When no active plan is
    configured (seeds not run) this logs a clear warning and returns None instead
    of a 500 — registration still succeeds; the tenant simply has no subscription
    until a plan exists. Production always seeds a plan, so this is the normal path
    that fixes the "new tenant has no subscription → SUBSCRIPTION_NOT_FOUND at 下单"
    bug (P0-B).
    """
    plan = default_plan(db)
    if plan is None:
        logger.warning("onboarding.no_active_plan", tenant_id=tenant_id)
        return None
    now = datetime.now(UTC)
    return activate_subscription(
        db,
        tenant_id=tenant_id,
        plan=plan,
        period_start=now,
    )
