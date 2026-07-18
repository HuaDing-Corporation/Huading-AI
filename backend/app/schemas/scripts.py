from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScriptGenerateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    video_mode: str | None = Field(default=None, max_length=40)
    duration_sec: int | None = Field(default=None)
    length_tier: Literal["short", "medium", "long"] = Field(
        default="medium",
        description="Script length tier for seedance_i2v generation only.",
    )

    model_config = ConfigDict(extra="forbid")


class ScriptGenerateResponse(BaseModel):
    script: str
