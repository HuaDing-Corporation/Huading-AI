from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReversePromptTargetFormat = Literal["seedance_2_0"]
ReversePromptSourceKind = Literal["image", "video"]


class ReversePromptCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=36)
    target_format: ReversePromptTargetFormat = "seedance_2_0"


class ReversePromptFillTargets(BaseModel):
    avatar_talk: dict[str, str]
    seedance_i2v: dict[str, str]
    video_gen: dict[str, str]
    photo: dict[str, str]
    ecom_model: dict[str, str]
    ecom_poster: dict[str, str]


class ReversePromptShot(BaseModel):
    index: int = Field(ge=0)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    visual: str
    camera: str = ""
    motion: str = ""
    transition: str = ""


class ReversePromptVideoAnalysis(BaseModel):
    duration_sec: float = Field(gt=0)
    pacing: Literal["slow", "medium", "fast", "variable"]
    shot_list: list[ReversePromptShot] = Field(default_factory=list)
    audio_transcript: str | None = None
    bgm_style: str | None = None


class ReversePromptResult(BaseModel):
    target_format: ReversePromptTargetFormat
    prompt_zh: str
    prompt_en: str
    negative_prompt: str = ""
    style_tags: list[str] = Field(default_factory=list)
    camera: str = ""
    lighting: str = ""
    composition: str = ""
    subject: str = ""
    scene: str = ""
    motion_hint: str = ""
    selling_points: list[str] = Field(default_factory=list)
    text_in_media: list[str] = Field(default_factory=list)
    disclaimer: str = ""
    confidence: float = 0.0
    fill_targets: ReversePromptFillTargets
    video_analysis: ReversePromptVideoAnalysis | None = None


class ReversePromptJobRead(BaseModel):
    id: str
    status: str
    source_kind: str
    source_asset_id: str | None = None
    target_format: str
    result: ReversePromptResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    credits: float = 0.0
    cost_cents: int = 0
    created_at: datetime
    updated_at: datetime
    saved_at: datetime | None = None


class ReversePromptSavedResponse(BaseModel):
    id: str
    status: Literal["saved"] = "saved"
    saved_at: datetime


class ReversePromptDeletedResponse(BaseModel):
    id: str
    deleted_at: datetime


class ReversePromptHistoryItem(BaseModel):
    id: str
    source_kind: ReversePromptSourceKind
    status: str
    created_at: datetime
    source_thumbnail_url: str | None = None
    summary: str | None = None


class ReversePromptHistoryListResponse(BaseModel):
    items: list[ReversePromptHistoryItem]
    total: int
    page: int
    page_size: int
