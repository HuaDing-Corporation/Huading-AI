from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import DbSessionDependency, require_admin
from app.schemas.analytics import (
    AnalyticsByProviderResponse,
    AnalyticsByTenantResponse,
    AnalyticsGranularity,
    AnalyticsOverviewResponse,
    AnalyticsTenantSort,
    AnalyticsTimeseriesResponse,
)
from app.schemas.response import ApiResponse, ok
from app.services.analytics import (
    analytics_by_provider,
    analytics_by_tenant,
    analytics_overview,
    analytics_timeseries,
    analytics_timeseries_week,
    resolve_date_range,
)

router = APIRouter(dependencies=[Depends(require_admin)])
FromDateQuery = Annotated[date | None, Query(alias="from")]
ToDateQuery = Annotated[date | None, Query()]
TenantSortQuery = Annotated[AnalyticsTenantSort, Query()]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]
GranularityQuery = Annotated[AnalyticsGranularity, Query()]


@router.get("/overview", response_model=ApiResponse[AnalyticsOverviewResponse])
def overview(
    request: Request,
    from_: FromDateQuery = None,
    to: ToDateQuery = None,
    db: Session = DbSessionDependency,
) -> ApiResponse[AnalyticsOverviewResponse]:
    period = resolve_date_range(from_, to)
    return ok(request, analytics_overview(db, period))


@router.get("/by-tenant", response_model=ApiResponse[AnalyticsByTenantResponse])
def by_tenant(
    request: Request,
    from_: FromDateQuery = None,
    to: ToDateQuery = None,
    sort: TenantSortQuery = "credits_desc",
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
    db: Session = DbSessionDependency,
) -> ApiResponse[AnalyticsByTenantResponse]:
    period = resolve_date_range(from_, to)
    return ok(
        request,
        analytics_by_tenant(
            db,
            period,
            sort=sort,
            limit=limit,
            offset=offset,
        ),
    )


@router.get("/by-provider", response_model=ApiResponse[AnalyticsByProviderResponse])
def by_provider(
    request: Request,
    from_: FromDateQuery = None,
    to: ToDateQuery = None,
    db: Session = DbSessionDependency,
) -> ApiResponse[AnalyticsByProviderResponse]:
    period = resolve_date_range(from_, to)
    return ok(request, analytics_by_provider(db, period))


@router.get("/timeseries", response_model=ApiResponse[AnalyticsTimeseriesResponse])
def timeseries(
    request: Request,
    from_: FromDateQuery = None,
    to: ToDateQuery = None,
    granularity: GranularityQuery = "day",
    db: Session = DbSessionDependency,
) -> ApiResponse[AnalyticsTimeseriesResponse]:
    period = resolve_date_range(from_, to)
    if granularity == "week":
        return ok(request, analytics_timeseries_week(db, period))
    return ok(request, analytics_timeseries(db, period))
