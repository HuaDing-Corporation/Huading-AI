from __future__ import annotations

import calendar
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Plan, Subscription

logger = structlog.get_logger(__name__)

_DEFAULT_PLAN_CODE = "basic"


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
    """The plan a new tenant subscribes to: 'basic' if present, else cheapest active."""
    plan = db.scalar(select(Plan).where(Plan.code == _DEFAULT_PLAN_CODE, Plan.is_active.is_(True)))
    if plan is not None:
        return plan
    return db.scalar(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_cents.asc()))


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
    subscription = Subscription(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status="active",
        period_start=now,
        period_end=_period_end(now, plan.period),
        quota_credits_total=plan.quota_credits,
        quota_credits_used=0,
        quota_credits_reserved=0,
    )
    db.add(subscription)
    return subscription
