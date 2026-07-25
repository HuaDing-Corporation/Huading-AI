from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReversePromptTargetFormat = Literal["seedance_2_0"]
ReversePromptSourceKind = Literal["image", "video"]
ReversePromptImageAspectRatio = Literal[
    "1:1",
    "4:3",
    "3:2",
    "16:9",
    "21:9",
    "3:4",
    "2:3",
    "9:16",
    "auto",
]
ReversePromptVideoGenAspectRatio = Literal[
    "16:9",
    "9:16",
    "1:1",
    "4:3",
    "3:4",
    "21:9",
    "auto",
]
ReversePromptSeedanceAspectRatio = Literal["9:16", "16:9", "1:1"]


class ReversePromptCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=36)
    target_format: ReversePromptTargetFormat = "seedance_2_0"


class ReversePromptEstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=36)


class ReversePromptEstimateResponse(BaseModel):
    credits: int = Field(ge=0)
    duration_sec: float | None = Field(default=None, ge=0)
    tier: Literal["image", "video_short", "video_long"]


class ReversePromptAvatarTalkTarget(BaseModel):
    topic: str
    script: str


class ReversePromptSeedanceI2VTarget(BaseModel):
    topic: str
    script: str | None = None
    scene_prompt: str
    negative_prompt: str = ""
    aspect_ratio: ReversePromptSeedanceAspectRatio | None = None
    duration_sec: int | None = None
    duration_clamped: bool = False
    shot_section: str | None = None


class ReversePromptVideoGenTarget(BaseModel):
    topic: str
    prompt: str
    negative_prompt: str = ""
    aspect_ratio: ReversePromptVideoGenAspectRatio | None = None
    duration_sec: int | None = None
    duration_clamped: bool = False
    generate_audio: bool = False
    shot_section: str | None = None


class ReversePromptPhotoTarget(BaseModel):
    topic: str
    master_prompt: str | None = None
    negative_prompt: str = ""
    aspect_ratio: ReversePromptImageAspectRatio | None = None


class ReversePromptEcomModelTarget(BaseModel):
    extra_prompt: str
    aspect_ratio: ReversePromptImageAspectRatio | None = None


class ReversePromptFillTargets(BaseModel):
    avatar_talk: ReversePromptAvatarTalkTarget
    seedance_i2v: ReversePromptSeedanceI2VTarget
    video_gen: ReversePromptVideoGenTarget
    photo: ReversePromptPhotoTarget
    ecom_model: ReversePromptEcomModelTarget


class ReversePromptSourceMedia(BaseModel):
    kind: ReversePromptSourceKind
    width: int | None = None
    height: int | None = None
    duration_sec: float | None = None
    aspect_ratio_raw: str | None = None


class ReversePromptStructuredPrompt(BaseModel):
    en: str
    zh: str


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
    shot_summary: str = ""


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
    source_media: ReversePromptSourceMedia | None = None
    structured_prompt: ReversePromptStructuredPrompt | None = None
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
    segments_total: int | None = None
    segments_done: int | None = None
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
