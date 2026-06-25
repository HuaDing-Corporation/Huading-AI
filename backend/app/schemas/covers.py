from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_HEX_COLOR_RE = r"^#[0-9A-Fa-f]{6}$"


class CoverTitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=200)
    font_size: int | None = None
    color: str = Field(default="#FFFFFF", pattern=_HEX_COLOR_RE)
    position: Literal["top", "center", "bottom"] = "bottom"

    @field_validator("font_size")
    @classmethod
    def _clamp_font_size(cls, value: int | None) -> int | None:
        if value is None:
            return value
        return max(24, min(120, int(value)))


class CoverFromFrameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_task_id: str = Field(min_length=1)
    timestamp_sec: float = Field(ge=0)
    title: CoverTitleRequest | None = None
    layout_template_id: str | None = Field(default=None, max_length=100)


class FrameCandidate(BaseModel):
    timestamp_sec: float
    preview_url: str


class FrameCandidatesResponse(BaseModel):
    frames: list[FrameCandidate]


class CoverRead(BaseModel):
    id: str
    image_url: str
    width: int
    height: int


class CoverFromFrameResponse(BaseModel):
    cover: CoverRead
