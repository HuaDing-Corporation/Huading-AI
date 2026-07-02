from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AnalyticsTenantSort = Literal[
    "credits_desc",
    "credits_asc",
    "cost_desc",
    "cost_asc",
    "task_count_desc",
    "task_count_asc",
    "success_rate_desc",
    "success_rate_asc",
]
AnalyticsGranularity = Literal["day", "week"]


class AnalyticsPeriod(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from")
    to: date


class AnalyticsOverviewResponse(BaseModel):
    total_credits_used: float
    total_cost_cents: int
    task_count: int
    success_count: int
    failed_count: int
    tenant_count: int
    period: AnalyticsPeriod


class AnalyticsBalance(BaseModel):
    total: int
    used: int
    reserved: int
    remaining: int


class AnalyticsTenantItem(BaseModel):
    tenant_id: str
    tenant_name: str
    credits_used: float
    cost_cents: int
    task_count: int
    success_rate: float
    balance: AnalyticsBalance


class AnalyticsByTenantResponse(BaseModel):
    items: list[AnalyticsTenantItem]
    total: int


class AnalyticsProviderItem(BaseModel):
    provider: str
    model: str | None
    credits_used: float
    cost_cents: int
    task_count: int
    share_pct: float


class AnalyticsByProviderResponse(BaseModel):
    items: list[AnalyticsProviderItem]


class AnalyticsTimeseriesBucket(BaseModel):
    date: date
    credits_used: float
    cost_cents: int
    task_count: int


class AnalyticsTimeseriesResponse(BaseModel):
    buckets: list[AnalyticsTimeseriesBucket]
