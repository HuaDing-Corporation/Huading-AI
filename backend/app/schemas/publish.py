from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PublishPlatformId = Literal[
    "douyin",
    "kuaishou",
    "wxchannels",
    "xiaohongshu",
    "bilibili",
]
PublishSourceKind = Literal["video", "image"]
PublishRecordStatus = Literal["draft", "copied", "published"]


class PublishPlatformRead(BaseModel):
    id: PublishPlatformId
    name: str
    title_max: int
    body_max: int
    hashtag_max: int
    publish_url: str
    cover_ratio: str
    notes: str


class PublishPlatformListResponse(BaseModel):
    items: list[PublishPlatformRead]


class PublishDraftCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: PublishSourceKind
    source_task_id: str = Field(min_length=1, max_length=80)
    platforms: list[PublishPlatformId] = Field(min_length=1, max_length=5)

    @field_validator("source_task_id")
    @classmethod
    def _source_task_id_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("source_task_id must not be blank")
        return text

    @field_validator("platforms")
    @classmethod
    def _platforms_unique(cls, value: list[PublishPlatformId]) -> list[PublishPlatformId]:
        seen: set[str] = set()
        deduped: list[PublishPlatformId] = []
        for platform_id in value:
            if platform_id not in seen:
                seen.add(platform_id)
                deduped.append(platform_id)
        return deduped


class PublishDraftItem(BaseModel):
    platform_id: PublishPlatformId
    title: str
    body: str
    hashtags: list[str]
    cover_url: str
    media_url: str
    publish_url: str


class PublishDraftCreateResponse(BaseModel):
    id: str
    items: list[PublishDraftItem]


class PublishRecordPlatform(BaseModel):
    platform_id: PublishPlatformId
    status: PublishRecordStatus


class PublishRecordRead(BaseModel):
    id: str
    source_kind: PublishSourceKind
    source_task_id: str
    created_at: datetime
    platforms: list[PublishRecordPlatform]


class PublishRecordListResponse(BaseModel):
    items: list[PublishRecordRead]
    total: int


class PublishRecordPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_id: PublishPlatformId
    status: Literal["published"]


class PublishRecordDeletedResponse(BaseModel):
    id: str
    deleted_at: datetime
