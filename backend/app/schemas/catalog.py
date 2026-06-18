from pydantic import BaseModel


class VoiceRead(BaseModel):
    id: str
    provider: str
    voice_code: str
    display_name: str
    gender: str
    language: str
    sample_url: str | None = None


class VoiceListResponse(BaseModel):
    items: list[VoiceRead]
    total: int


class AvatarPresetRead(BaseModel):
    asset_id: str
    display_name: str
    thumbnail_url: str | None = None


class AvatarPresetListResponse(BaseModel):
    items: list[AvatarPresetRead]
    total: int
