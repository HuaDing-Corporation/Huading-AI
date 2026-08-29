from typing import Literal

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


class BrandVoiceOrderResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["fulfilled", "rejected"]
    fulfilled_brand_voice_id: str | None = None
    fulfilled_provider_id: str | None = None
    rejection_reason: str | None = None

    @field_validator("fulfilled_brand_voice_id", "fulfilled_provider_id")
    @classmethod
    def normalize_identifiers(cls, value: str | None, info):
        if value is None:
            return None
        return _non_blank(value, info.field_name)

    @model_validator(mode="after")
    def validate_resolution(self):
        fulfilled = (self.fulfilled_brand_voice_id, self.fulfilled_provider_id)
        if self.status == "fulfilled" and all(fulfilled) and self.rejection_reason is None:
            return self
        if self.status == "rejected" and not any(fulfilled) and self.rejection_reason:
            self.rejection_reason = _non_blank(self.rejection_reason, "rejection_reason")
            return self
        raise ValueError("resolution fields do not match status")
