from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CopyRewriteMode = Literal["smart", "custom", "auto"]
CopyVideoMode = Literal["avatar_talk", "seedance_i2v"]
CopyTargetPlatform = Literal["douyin", "xiaohongshu"]
CopyTitleStyle = Literal["短句", "长句"]


class CopyRewriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(min_length=1, max_length=4000)
    mode: CopyRewriteMode
    instruction: str | None = Field(default=None, max_length=1000)
    n: int = Field(default=3)
    target_platform: CopyTargetPlatform | None = None
    video_mode: CopyVideoMode | None = None
    duration_sec: int | None = None

    @field_validator("source_text")
    @classmethod
    def _source_text_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("source_text must not be blank")
        return text

    @field_validator("n")
    @classmethod
    def _clamp_n(cls, value: int) -> int:
        return max(1, min(5, int(value)))

    @model_validator(mode="after")
    def _custom_requires_instruction(self) -> "CopyRewriteRequest":
        if self.mode == "custom" and not (self.instruction or "").strip():
            raise ValueError("instruction is required when mode is custom")
        return self


class CopyRewriteResult(BaseModel):
    text: str


class CopyRewriteResponse(BaseModel):
    results: list[CopyRewriteResult]


class CopyTitlesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(min_length=1, max_length=4000)
    n: int = Field(default=5)
    style: CopyTitleStyle | None = None

    @field_validator("source_text")
    @classmethod
    def _source_text_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("source_text must not be blank")
        return text

    @field_validator("n")
    @classmethod
    def _clamp_n(cls, value: int) -> int:
        return max(1, min(10, int(value)))


class CopyTitlesResponse(BaseModel):
    titles: list[str]


class CopyTopicsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(min_length=1, max_length=4000)
    n: int = Field(default=5)

    @field_validator("source_text")
    @classmethod
    def _source_text_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("source_text must not be blank")
        return text

    @field_validator("n")
    @classmethod
    def _clamp_n(cls, value: int) -> int:
        return max(1, min(10, int(value)))


class CopyTopicsResponse(BaseModel):
    topics: list[str]


class CopyDraftCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(min_length=1, max_length=4000)
    result_text: str = Field(min_length=1, max_length=5000)
    titles: list[str] | None = None
    topics: list[str] | None = None
    mode: CopyRewriteMode
    target_platform: CopyTargetPlatform | None = None

    @field_validator("source_text", "result_text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("text fields must not be blank")
        return text


class CopyDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_text: str
    result_text: str
    titles: list[str] | None = None
    topics: list[str] | None = None
    mode: CopyRewriteMode
    target_platform: CopyTargetPlatform | None = None
    created_at: datetime
    deleted_at: datetime | None = None


class CopyDraftListResponse(BaseModel):
    items: list[CopyDraftRead]
    total: int


class CopyDraftDeletedResponse(BaseModel):
    id: str
    deleted_at: datetime
