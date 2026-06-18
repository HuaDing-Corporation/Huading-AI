import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ALLOWED_PIPELINES = {"standard", "custom"}
_ALLOWED_MODES = {"generate", "fixed"}
_ALLOWED_VIDEO_MODES = {"static_template", "seedance_t2v", "seedance_i2v", "avatar_talk"}
# e.g. "1080x1920/static_default.html" — size dir + html file, no path traversal.
_TEMPLATE_RE = re.compile(r"^[A-Za-z0-9_]+x[A-Za-z0-9_]+/[A-Za-z0-9_.\-]+\.html$")
# Tenant-relative upload key as returned by POST /api/v1/uploads.
_IMAGE_KEY_RE = re.compile(r"^uploads/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)$")


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
    script: str | None = Field(default=None, max_length=5000)
    voice_id: str | None = None
    avatar_asset_id: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    aspect_ratio: str = Field(default="9:16")
    subtitle_enabled: bool = True
    pipeline: str = Field(default="standard")
    mode: str = Field(default="generate", description="'generate' (LLM) or 'fixed' (use script)")
    # Which generation flow to run. 'mode' above is kept for the static-template
    # pipeline's script handling (generate|fixed); this selects the flow itself.
    video_mode: str = Field(
        default="static_template",
            description="static_template | seedance_t2v | seedance_i2v | avatar_talk",
    )
    image_key: str | None = Field(
        default=None,
        description="Tenant-relative upload key from POST /uploads (seedance_i2v input)",
    )
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

    @field_validator("video_mode")
    @classmethod
    def _check_video_mode(cls, v: str) -> str:
        if v not in _ALLOWED_VIDEO_MODES:
            raise ValueError(f"video_mode must be one of {sorted(_ALLOWED_VIDEO_MODES)}")
        return v

    @field_validator("image_key")
    @classmethod
    def _check_image_key(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if ".." in v or not _IMAGE_KEY_RE.match(v):
            raise ValueError("invalid image_key (use the key returned by POST /uploads)")
        return v

    @field_validator("frame_template")
    @classmethod
    def _check_template(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if ".." in v or v.startswith("/") or not _TEMPLATE_RE.match(v):
            raise ValueError("invalid frame_template")
        return v

    @model_validator(mode="after")
    def _check_i2v_has_image(self) -> "VideoGenerateRequest":
        if self.video_mode == "seedance_i2v" and not self.image_key:
            raise ValueError("seedance_i2v requires image_key (upload an image first)")
        if self.video_mode == "avatar_talk":
            if not self.voice_id:
                raise ValueError("avatar_talk requires voice_id")
            if not self.avatar_asset_id:
                raise ValueError("avatar_talk requires avatar_asset_id")
        return self


class VideoAccepted(BaseModel):
    id: str
    # Kept for the existing M2 frontend while the new contract standardizes on id.
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


class VideoRead(BaseModel):
    id: str
    title: str
    prompt: str
    mode: str
    status: str
    progress: int
    topic: str | None = None
    script: str | None = None
    voice_id: str | None = None
    aspect_ratio: str | None = None
    subtitle_enabled: bool | None = None
    created_at: datetime
    duration_sec: float | None = None
    duration_ms: int | None = None
    thumbnail_url: str | None = None
    playback_url: str | None = None
    download_url: str | None = None
    error: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class VideoListResponse(BaseModel):
    items: list[VideoRead]
    total: int | None = None
    next_cursor: str | None = None
