import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ALLOWED_PIPELINES = {"standard", "custom"}
_ALLOWED_MODES = {"generate", "fixed"}
# e.g. "1080x1920/static_default.html" — size dir + html file, no path traversal.
_TEMPLATE_RE = re.compile(r"^[A-Za-z0-9_]+x[A-Za-z0-9_]+/[A-Za-z0-9_.\-]+\.html$")


class VideoGenerateRequest(BaseModel):
    """Request to generate a video from a topic.

    Note: there is deliberately no ``output_path`` field. The output location is
    chosen server-side under a task-isolated, whitelisted path (#002-RV P2); a
    client cannot direct the engine to write anywhere on disk.

    ``extra="forbid"`` rejects unknown fields (e.g. a sneaked-in ``output_path``)
    with 422 instead of silently ignoring them (#005-FIX P2).
    """

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=2000, description="Theme/topic or fixed script")
    pipeline: str = Field(default="standard")
    mode: str = Field(default="generate", description="'generate' (LLM) or 'fixed' (use script)")
    n_scenes: int = Field(default=3, ge=1, le=20)
    frame_template: str | None = Field(
        default=None, description="e.g. '1080x1920/static_default.html'; None uses server default"
    )
    voice: str | None = Field(default=None, description="TTS voice id (local edge-tts)")
    tts_speed: float = Field(default=1.2, ge=0.5, le=2.0)

    @field_validator("pipeline")
    @classmethod
    def _check_pipeline(cls, v: str) -> str:
        if v not in _ALLOWED_PIPELINES:
            raise ValueError(f"pipeline must be one of {sorted(_ALLOWED_PIPELINES)}")
        return v

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in _ALLOWED_MODES:
            raise ValueError(f"mode must be one of {sorted(_ALLOWED_MODES)}")
        return v

    @field_validator("frame_template")
    @classmethod
    def _check_template(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if ".." in v or v.startswith("/") or not _TEMPLATE_RE.match(v):
            raise ValueError("invalid frame_template")
        return v


class VideoAccepted(BaseModel):
    task_id: str
    status: str


class VideoTaskStatus(BaseModel):
    task_id: str
    status: str  # PENDING / STARTED / PROGRESS / SUCCESS / FAILURE
    stage: str | None = None
    progress: float = 0.0  # 0.0 - 1.0
    frame_current: int | None = None
    frame_total: int | None = None
    video_url: str | None = None
    error: str | None = None
