from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


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
    status: str
    created_at: datetime


class BrandVoiceCreateResponse(BrandVoiceRead):
    pass


class BrandVoiceListResponse(BaseModel):
    items: list[BrandVoiceRead]
    total: int


class BrandVoiceDeletedResponse(BaseModel):
    deleted: bool
