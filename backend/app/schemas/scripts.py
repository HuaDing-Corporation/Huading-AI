from pydantic import BaseModel, ConfigDict, Field


class ScriptGenerateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    video_mode: str | None = Field(default=None, max_length=40)
    duration_sec: int | None = Field(default=None)

    model_config = ConfigDict(extra="forbid")


class ScriptGenerateResponse(BaseModel):
    script: str
