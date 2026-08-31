from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


def _non_blank(value: str, field: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{field} must not be blank")
    return result


class BrandVoiceOrderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_type: Literal["create", "renew"]
    requested_name: str = Field(min_length=1, max_length=30)
    source_audio_asset_id: str = Field(min_length=1)
    consent_confirmed: StrictBool
    existing_brand_voice_id: str | None = None

    @field_validator("requested_name", "source_audio_asset_id", "existing_brand_voice_id")
    @classmethod
    def normalize_identifiers(cls, value: str | None, info):
        if value is None:
            return None
        return _non_blank(value, info.field_name)

    @model_validator(mode="after")
    def validate_order_type(self):
        if not self.consent_confirmed:
            raise ValueError("consent_confirmed must be true")
        if self.order_type == "create" and self.existing_brand_voice_id is not None:
            raise ValueError("create orders must not include existing_brand_voice_id")
        if self.order_type == "renew" and not self.existing_brand_voice_id:
            raise ValueError("renew orders require existing_brand_voice_id")
        return self


class BrandVoiceOrderFulfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["fulfill"]
    provider_voice_id: str = Field(min_length=1)

    @field_validator("provider_voice_id")
    @classmethod
    def normalize_provider_voice_id(cls, value: str) -> str:
        return _non_blank(value, "provider_voice_id")


class BrandVoiceOrderRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["reject"]
    rejection_reason: str = Field(min_length=1)

    @field_validator("rejection_reason")
    @classmethod
    def normalize_rejection_reason(cls, value: str) -> str:
        return _non_blank(value, "rejection_reason")


BrandVoiceOrderResolveRequest = Annotated[
    BrandVoiceOrderFulfillRequest | BrandVoiceOrderRejectRequest,
    Field(discriminator="action"),
]


RefundDisposition = Literal[
    "not_applicable",
    "source_subscription_released",
    "current_subscription_credited",
    "pending_next_subscription",
]


class BrandVoiceOrderBillingSummary(BaseModel):
    operation_id: str
    idempotency_key: UUID
    status: Literal["reserved", "settled", "partially_settled", "released"]
    requested_credits: int
    held_credits: int
    settled_credits: int
    released_credits: int


class BrandVoiceOrderRead(BaseModel):
    id: str
    tenant_id: str
    ordered_by_user_id: str
    order_type: Literal["create", "renew"]
    requested_name: str
    source_audio_asset_id: str
    existing_brand_voice_id: str | None
    status: Literal["awaiting_fulfillment", "fulfilled", "rejected"]
    fulfilled_brand_voice_id: str | None
    fulfilled_provider_voice_id: str | None
    rejection_reason: str | None
    fulfilled_at: datetime | None
    expires_at: datetime | None
    rejected_at: datetime | None
    created_at: datetime
    updated_at: datetime
    billing: BrandVoiceOrderBillingSummary
    refund_disposition: RefundDisposition = "not_applicable"
    refund_grant_status: Literal["pending", "applied"] | None = None
    refund_applied_at: datetime | None = None


class AdminBrandVoiceOrderRead(BrandVoiceOrderRead):
    source_audio_url: str | None = None


class BrandVoiceOrderPage(BaseModel):
    items: list[BrandVoiceOrderRead]
    total: int
    page: int | None = None
    page_size: int | None = None
