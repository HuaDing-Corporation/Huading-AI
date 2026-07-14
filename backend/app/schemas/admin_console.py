from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

AdminPlanCode = Literal["free", "basic", "huading"]
AdminTaskFamily = Literal["video", "reverse_prompt", "ecom_replicate"]
AdminTaskStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class AdminSubscriptionSnapshot(BaseModel):
    id: str
    total: int
    used: int
    reserved: int
    remaining: int


class AdminTenantItem(BaseModel):
    tenant_id: str
    slug: str
    name: str
    status: str
    created_at: datetime
    owner_email: str | None
    plan_code: str | None
    subscription: AdminSubscriptionSnapshot | None
    task_count: int


class AdminUsageItem(BaseModel):
    id: str
    created_at: datetime
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    capability: str
    provider: str
    model: str | None
    quantity: float
    unit: str
    credits: float
    cost_cents: int
    status: str
    video_task_id: str | None


class AdminTaskItem(BaseModel):
    id: str
    task_family: AdminTaskFamily
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    mode: str
    label: str | None
    video_mode: str | None
    status: AdminTaskStatus
    progress: int | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    retryable: bool


class AdminVoiceSlotItem(BaseModel):
    speaker_id: str
    scope: Literal["platform", "tenant"]
    sources: list[str]
    tenant_id: str | None
    tenant_slug: str | None
    tenant_name: str | None
    occupied: bool
    brand_voice_id: str | None
    brand_voice_name: str | None
    brand_voice_status: str | None


class AdminVoiceSlotsResponse(BaseModel):
    items: list[AdminVoiceSlotItem]
    total: int
    remaining: int


class AdminAuditLogItem(BaseModel):
    id: str
    actor_user_id: str
    actor_email: str | None
    actor_tenant_id: str
    action: str
    target_tenant_id: str | None
    target_tenant_slug: str | None
    target_id: str | None
    before: dict[str, object] | None
    after: dict[str, object] | None
    reason: str | None
    created_at: datetime


class AdminTenantPage(BaseModel):
    items: list[AdminTenantItem]
    total: int
    page: int
    page_size: int


class AdminUsagePage(BaseModel):
    items: list[AdminUsageItem]
    total: int
    page: int
    page_size: int


class AdminTaskPage(BaseModel):
    items: list[AdminTaskItem]
    total: int
    page: int
    page_size: int


class AdminAuditLogPage(BaseModel):
    items: list[AdminAuditLogItem]
    total: int
    page: int
    page_size: int


class AdminTenantDetail(BaseModel):
    tenant: AdminTenantItem
    recent_tasks: list[AdminTaskItem]
    recent_usage: list[AdminUsageItem]
    voice_slots: list[AdminVoiceSlotItem]


class AdminCreditsAdjustRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta: int
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("delta")
    @classmethod
    def validate_delta(cls, value: int) -> int:
        if value == 0:
            raise PydanticCustomError("friendly_delta", "额度调整值不能为 0")
        return value

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise PydanticCustomError("friendly_reason", "请填写额度调整理由")
        return normalized


class AdminPlanChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: AdminPlanCode
    reason: str | None = Field(default=None, max_length=500)


class AdminTenantStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool
    reason: str | None = Field(default=None, max_length=500)


class AdminVoiceSlotAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_id: str = Field(pattern=r"^S_[A-Za-z0-9_-]{1,157}$")
    reason: str | None = Field(default=None, max_length=500)


class AdminCreditsAdjustResponse(BaseModel):
    tenant_id: str
    delta: int
    subscription: AdminSubscriptionSnapshot


class AdminPlanChangeResponse(BaseModel):
    tenant_id: str
    plan_code: AdminPlanCode
    subscription: AdminSubscriptionSnapshot


class AdminTenantStatusResponse(BaseModel):
    tenant_id: str
    status: str


class AdminTaskRetryResponse(BaseModel):
    id: str
    task_family: AdminTaskFamily
    tenant_id: str
    status: Literal["queued"]
    progress: int
    charged: bool
    credits: int = Field(ge=0)


class AdminVoiceSlotAssignResponse(BaseModel):
    tenant_id: str
    speaker_id: str
    changed: bool
    speaker_ids: list[str]
