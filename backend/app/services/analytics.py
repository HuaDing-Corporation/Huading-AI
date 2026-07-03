from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Float, and_, cast, desc, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models import Subscription, Tenant, UsageRecord, VideoTask
from app.schemas.analytics import (
    AnalyticsBalance,
    AnalyticsByProviderResponse,
    AnalyticsByTenantResponse,
    AnalyticsOverviewResponse,
    AnalyticsPeriod,
    AnalyticsProviderItem,
    AnalyticsTenantItem,
    AnalyticsTenantSort,
    AnalyticsTimeseriesBucket,
    AnalyticsTimeseriesResponse,
)

FAILED_TASK_STATUSES = ("failed",)


@dataclass(frozen=True)
class AnalyticsDateRange:
    start: date
    end: date

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.start, datetime.min.time(), tzinfo=UTC)

    @property
    def end_exclusive_dt(self) -> datetime:
        return datetime.combine(self.end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    def period(self) -> AnalyticsPeriod:
        return AnalyticsPeriod(from_=self.start, to=self.end)


def resolve_date_range(from_date: date | None, to_date: date | None) -> AnalyticsDateRange:
    today = datetime.now(UTC).date()
    end = to_date or today
    start = from_date or (end - timedelta(days=29))
    if start > end:
        raise AppError(
            "from must be earlier than or equal to to.",
            code="ANALYTICS_INVALID_PERIOD",
            status_code=422,
        )
    return AnalyticsDateRange(start=start, end=end)


def analytics_overview(db: Session, period: AnalyticsDateRange) -> AnalyticsOverviewResponse:
    credits, cost_cents, tenant_count = db.execute(
        select(
            func.coalesce(func.sum(UsageRecord.credits), 0),
            func.coalesce(func.sum(UsageRecord.cost_cents), 0),
            func.count(func.distinct(UsageRecord.tenant_id)),
        ).where(
            UsageRecord.status == "settled",
            _usage_in_period(period),
        )
    ).one()
    success_count = _success_count(db, period)
    failed_count = _failed_count(db, period)
    return AnalyticsOverviewResponse(
        total_credits_used=_float(credits),
        total_cost_cents=int(cost_cents or 0),
        task_count=success_count + failed_count,
        success_count=success_count,
        failed_count=failed_count,
        tenant_count=int(tenant_count or 0),
        period=period.period(),
    )


def analytics_by_tenant(
    db: Session,
    period: AnalyticsDateRange,
    *,
    sort: AnalyticsTenantSort,
    limit: int,
    offset: int,
) -> AnalyticsByTenantResponse:
    settled = _settled_by_tenant(period)
    successful = _successful_by_tenant(period)
    failed = _failed_by_tenant(period)
    credits_expr = func.coalesce(settled.c.credits_used, 0)
    cost_expr = func.coalesce(settled.c.cost_cents, 0)
    success_expr = func.coalesce(successful.c.success_count, 0)
    failed_expr = func.coalesce(failed.c.failed_count, 0)
    task_count_expr = success_expr + failed_expr
    success_rate_expr = func.coalesce(
        cast(success_expr, Float) / func.nullif(success_expr + failed_expr, 0),
        0.0,
    )

    sort_expr = {
        "credits_desc": desc(credits_expr),
        "credits_asc": credits_expr.asc(),
        "cost_desc": desc(cost_expr),
        "cost_asc": cost_expr.asc(),
        "task_count_desc": desc(task_count_expr),
        "task_count_asc": task_count_expr.asc(),
        "success_rate_desc": desc(success_rate_expr),
        "success_rate_asc": success_rate_expr.asc(),
    }[sort]
    total = db.scalar(select(func.count(Tenant.id)).where(Tenant.deleted_at.is_(None))) or 0
    rows = db.execute(
        select(
            Tenant.id,
            Tenant.name,
            credits_expr.label("credits_used"),
            cost_expr.label("cost_cents"),
            task_count_expr.label("task_count"),
            success_rate_expr.label("success_rate"),
            Subscription.quota_credits_total,
            Subscription.quota_credits_used,
            Subscription.quota_credits_reserved,
        )
        .outerjoin(
            Subscription,
            and_(Subscription.tenant_id == Tenant.id, Subscription.status == "active"),
        )
        .outerjoin(settled, settled.c.tenant_id == Tenant.id)
        .outerjoin(successful, successful.c.tenant_id == Tenant.id)
        .outerjoin(failed, failed.c.tenant_id == Tenant.id)
        .where(Tenant.deleted_at.is_(None))
        .order_by(sort_expr, Tenant.created_at.asc(), Tenant.id.asc())
        .limit(limit)
        .offset(offset)
    ).all()

    return AnalyticsByTenantResponse(
        items=[
            AnalyticsTenantItem(
                tenant_id=tenant_id,
                tenant_name=tenant_name,
                credits_used=_float(credits_used),
                cost_cents=int(cost_cents or 0),
                task_count=int(task_count or 0),
                success_rate=_rounded_rate(success_rate),
                balance=_balance(total_credits, used_credits, reserved_credits),
            )
            for (
                tenant_id,
                tenant_name,
                credits_used,
                cost_cents,
                task_count,
                success_rate,
                total_credits,
                used_credits,
                reserved_credits,
            ) in rows
        ],
        total=int(total),
    )


def analytics_by_provider(db: Session, period: AnalyticsDateRange) -> AnalyticsByProviderResponse:
    rows = db.execute(
        select(
            UsageRecord.provider,
            UsageRecord.model,
            func.coalesce(func.sum(UsageRecord.credits), 0).label("credits_used"),
            func.coalesce(func.sum(UsageRecord.cost_cents), 0).label("cost_cents"),
            func.count(UsageRecord.id).label("task_count"),
        )
        .where(
            UsageRecord.status == "settled",
            _usage_in_period(period),
        )
        .group_by(UsageRecord.provider, UsageRecord.model)
        .order_by(desc("credits_used"), UsageRecord.provider.asc(), UsageRecord.model.asc())
    ).all()
    total_credits = sum((_decimal(row.credits_used) for row in rows), Decimal("0"))
    return AnalyticsByProviderResponse(
        items=[
            AnalyticsProviderItem(
                provider=row.provider,
                model=row.model,
                credits_used=_float(row.credits_used),
                cost_cents=int(row.cost_cents or 0),
                task_count=int(row.task_count or 0),
                share_pct=_share_pct(row.credits_used, total_credits),
            )
            for row in rows
        ]
    )


def analytics_timeseries(db: Session, period: AnalyticsDateRange) -> AnalyticsTimeseriesResponse:
    day_expr = func.date(UsageRecord.created_at)
    rows = db.execute(
        select(
            day_expr.label("usage_day"),
            func.coalesce(func.sum(UsageRecord.credits), 0).label("credits_used"),
            func.coalesce(func.sum(UsageRecord.cost_cents), 0).label("cost_cents"),
            func.count(UsageRecord.id).label("task_count"),
        )
        .where(
            UsageRecord.status == "settled",
            _usage_in_period(period),
        )
        .group_by(day_expr)
    ).all()
    by_day = {
        _coerce_date(row.usage_day): (
            _float(row.credits_used),
            int(row.cost_cents or 0),
            int(row.task_count or 0),
        )
        for row in rows
    }
    return AnalyticsTimeseriesResponse(
        buckets=[
            _timeseries_bucket(day, by_day.get(day, (0.0, 0, 0)))
            for day in _days(period)
        ]
    )


def analytics_timeseries_week(
    db: Session,
    period: AnalyticsDateRange,
) -> AnalyticsTimeseriesResponse:
    daily = analytics_timeseries(db, period)
    by_week: dict[date, tuple[float, int, int]] = {
        week_start: (0.0, 0, 0) for week_start in _weeks(period)
    }
    for bucket in daily.buckets:
        week_start = bucket.date - timedelta(days=bucket.date.weekday())
        credits, cost_cents, task_count = by_week.setdefault(week_start, (0.0, 0, 0))
        by_week[week_start] = (
            credits + bucket.credits_used,
            cost_cents + bucket.cost_cents,
            task_count + bucket.task_count,
        )
    return AnalyticsTimeseriesResponse(
        buckets=[
            AnalyticsTimeseriesBucket(
                date=week_start,
                credits_used=round(credits, 4),
                cost_cents=cost_cents,
                task_count=task_count,
            )
            for week_start, (credits, cost_cents, task_count) in sorted(by_week.items())
        ]
    )


def _usage_in_period(period: AnalyticsDateRange):
    return and_(
        UsageRecord.created_at >= period.start_dt,
        UsageRecord.created_at < period.end_exclusive_dt,
    )


def _settled_by_tenant(period: AnalyticsDateRange):
    return (
        select(
            UsageRecord.tenant_id.label("tenant_id"),
            func.coalesce(func.sum(UsageRecord.credits), 0).label("credits_used"),
            func.coalesce(func.sum(UsageRecord.cost_cents), 0).label("cost_cents"),
        )
        .where(
            UsageRecord.status == "settled",
            _usage_in_period(period),
        )
        .group_by(UsageRecord.tenant_id)
        .subquery()
    )


def _successful_by_tenant(period: AnalyticsDateRange):
    return (
        select(
            UsageRecord.tenant_id.label("tenant_id"),
            func.count(func.distinct(UsageRecord.video_task_id)).label("success_count"),
        )
        .join(VideoTask, VideoTask.id == UsageRecord.video_task_id)
        .where(
            _usage_in_period(period),
            UsageRecord.video_task_id.is_not(None),
            UsageRecord.status == "settled",
            VideoTask.status == "done",
        )
        .group_by(UsageRecord.tenant_id)
        .subquery()
    )


def _failed_by_tenant(period: AnalyticsDateRange):
    return (
        select(
            UsageRecord.tenant_id.label("tenant_id"),
            func.count(func.distinct(UsageRecord.video_task_id)).label("failed_count"),
        )
        .join(VideoTask, VideoTask.id == UsageRecord.video_task_id)
        .where(
            _usage_in_period(period),
            UsageRecord.video_task_id.is_not(None),
            UsageRecord.status != "settled",
            VideoTask.status.in_(FAILED_TASK_STATUSES),
        )
        .group_by(UsageRecord.tenant_id)
        .subquery()
    )


def _success_count(db: Session, period: AnalyticsDateRange) -> int:
    return int(
        db.scalar(
            select(func.count(func.distinct(UsageRecord.video_task_id)))
            .join(VideoTask, VideoTask.id == UsageRecord.video_task_id)
            .where(
                _usage_in_period(period),
                UsageRecord.video_task_id.is_not(None),
                UsageRecord.status == "settled",
                VideoTask.status == "done",
            )
        )
        or 0
    )


def _failed_count(db: Session, period: AnalyticsDateRange) -> int:
    return int(
        db.scalar(
            select(func.count(func.distinct(UsageRecord.video_task_id)))
            .join(VideoTask, VideoTask.id == UsageRecord.video_task_id)
            .where(
                _usage_in_period(period),
                UsageRecord.video_task_id.is_not(None),
                UsageRecord.status != "settled",
                VideoTask.status.in_(FAILED_TASK_STATUSES),
            )
        )
        or 0
    )


def _balance(total: int | None, used: int | None, reserved: int | None) -> AnalyticsBalance:
    total_value = int(total or 0)
    used_value = int(used or 0)
    reserved_value = int(reserved or 0)
    return AnalyticsBalance(
        total=total_value,
        used=used_value,
        reserved=reserved_value,
        remaining=total_value - used_value - reserved_value,
    )


def _timeseries_bucket(
    bucket_date: date,
    values: tuple[float, int, int],
) -> AnalyticsTimeseriesBucket:
    credits_used, cost_cents, task_count = values
    return AnalyticsTimeseriesBucket(
        date=bucket_date,
        credits_used=credits_used,
        cost_cents=cost_cents,
        task_count=task_count,
    )


def _days(period: AnalyticsDateRange):
    current = period.start
    while current <= period.end:
        yield current
        current += timedelta(days=1)


def _weeks(period: AnalyticsDateRange):
    current = period.start - timedelta(days=period.start.weekday())
    last = period.end - timedelta(days=period.end.weekday())
    while current <= last:
        yield current
        current += timedelta(days=7)


def _coerce_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _decimal(value: object) -> Decimal:
    return Decimal(str(value or "0"))


def _float(value: object) -> float:
    return float(_decimal(value))


def _rounded_rate(value: object) -> float:
    return round(float(value or 0), 4)


def _share_pct(credits: object, total_credits: Decimal) -> float:
    if total_credits <= 0:
        return 0.0
    return round(float((_decimal(credits) / total_credits) * Decimal("100")), 2)
