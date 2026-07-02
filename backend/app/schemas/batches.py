from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.videos import BgmSelectionRequest

BatchKind = Literal["ecom_table", "prompt_set"]


class BatchCommonParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_mode: Literal["seedance_i2v", "video_gen"]
    duration_sec: int | None = None
    resolution: Literal["480p", "720p", "1080p"] = "720p"
    reference_image_asset_ids: list[str] = Field(default_factory=list)
    bgm: BgmSelectionRequest | None = None
    voice_id: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    subtitle_enabled: bool = True
    apply_visible_label: bool = False
    size: str | None = None


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: BatchKind
    rows: list[dict[str, object]] = Field(min_length=1, max_length=30)
    common: BatchCommonParams

    @model_validator(mode="after")
    def _check_mode_matches_kind(self) -> BatchRequest:
        if self.kind == "ecom_table" and self.common.video_mode != "seedance_i2v":
            raise ValueError("ecom_table batches require common.video_mode=seedance_i2v")
        if self.kind == "prompt_set" and self.common.video_mode != "video_gen":
            raise ValueError("prompt_set batches require common.video_mode=video_gen")
        return self


class BatchEstimateResponse(BaseModel):
    total_rows: int
    per_row_credits: int
    total_credits: int
    insufficient: bool
    balance_credits: int


class BatchCreateResponse(BaseModel):
    batch_id: str
    task_ids: list[str]


class BatchSummary(BaseModel):
    id: str
    kind: str
    status: str
    total: int
    succeeded: int
    failed: int
    common_params: dict[str, object]
    created_at: datetime
    updated_at: datetime | None = None


class BatchListResponse(BaseModel):
    items: list[BatchSummary]
    total: int


class BatchTaskRead(BaseModel):
    task_id: str
    row_index: int
    status: str
    video_url: str | None = None
    error: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class BatchDetailResponse(BaseModel):
    batch: BatchSummary
    tasks: list[BatchTaskRead]


class BatchCancelResponse(BaseModel):
    batch_id: str
    cancelled: int
    running: int
