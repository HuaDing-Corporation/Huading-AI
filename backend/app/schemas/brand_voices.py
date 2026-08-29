from datetime import datetime
from decimal import Decimal
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


class CosyVoiceCloneResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    speaker_id: str = Field(min_length=1)
    status: Literal["ready"]
    provider: Literal["cosyvoice-voice-clone"]
    model: str | None = None
    cost_cents: int = Field(default=0, ge=0)
    provider_cost_usd: Decimal | None = Field(default=None, ge=0)

    @field_validator("speaker_id")
    @classmethod
    def _strip_speaker_id(cls, value: str) -> str:
        speaker_id = value.strip()
        if not speaker_id:
            raise ValueError("speaker_id must not be blank")
        return speaker_id


class BrandVoiceListResponse(BaseModel):
    items: list[BrandVoiceRead]
    total: int
