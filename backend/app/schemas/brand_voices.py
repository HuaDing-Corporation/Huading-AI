from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.schemas.brand_voice_orders import BrandVoiceOrderBillingSummary


def _non_blank_name(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("name must not be blank")
    return text


class BrandVoiceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=30)
    source_audio_asset_id: str
    consent_confirmed: StrictBool
    provider: Literal[
        "doubao",
        "doubao-voice-clone",
        "cosyvoice",
        "cosyvoice-voice-clone",
    ] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return _non_blank_name(value)


class BrandVoiceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=30)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return _non_blank_name(value)


class BrandVoiceRead(BaseModel):
    id: str
    name: str
    provider: str
    status: str
    order_status: Literal["awaiting_fulfillment", "fulfilled", "rejected"] | None
    delivery_status: Literal["awaiting_fulfillment", "active", "expired", "rejected"]
    created_at: datetime


class BrandVoiceCreateResponse(BrandVoiceRead):
    billing: BrandVoiceOrderBillingSummary


class BrandVoiceListResponse(BaseModel):
    items: list[BrandVoiceRead]
    total: int
