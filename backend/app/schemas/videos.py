import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.core.image_aspect_ratio import (
    VIDEO_ASPECT_RATIOS,
    VIDEO_GEN_ASPECT_RATIOS,
    RequestedImageAspectRatio,
    image_aspect_ratio_from_legacy_size,
)
from app.services.subtitle_styles import SUBTITLE_TEMPLATE_IDS, clamp_subtitle_font_size

_ALLOWED_PIPELINES = {"standard", "custom"}
_ALLOWED_MODES = {"generate", "fixed"}
_ALLOWED_VIDEO_MODES = {
    "static_template",
    "seedance_t2v",
    "seedance_i2v",
    "avatar_talk",
    "photo",
    "video_gen",
}
_MIN_DURATION_SEC = 5
_MAX_DURATION_SEC = 120
_VIDEO_GEN_MIN_DURATION_SEC = 4
_VIDEO_GEN_MAX_DURATION_SEC = 15
_PHOTO_PROMPT_MAX_LENGTH = 20_000
# e.g. "1080x1920/static_default.html" — size dir + html file, no path traversal.
_TEMPLATE_RE = re.compile(r"^[A-Za-z0-9_]+x[A-Za-z0-9_]+/[A-Za-z0-9_.\-]+\.html$")
# Tenant-relative upload key as returned by POST /api/v1/uploads.
_IMAGE_KEY_RE = re.compile(r"^uploads/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)$")
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class SubtitleStyleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    font_family: str | None = Field(default=None, max_length=100)
    font_size: int | None = None
    color: str | None = None
    position: Literal["top", "center", "bottom"] | None = None

    @field_validator("template_id")
    @classmethod
    def _check_template_id(cls, value: str) -> str:
        if value not in SUBTITLE_TEMPLATE_IDS:
            raise ValueError("unknown subtitle template_id")
        return value

    @field_validator("font_size")
    @classmethod
    def _clamp_font_size(cls, value: int | None) -> int | None:
        if value is None:
            return value
        return clamp_subtitle_font_size(value)

    @field_validator("color")
    @classmethod
    def _check_color(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not _HEX_COLOR_RE.match(value):
            raise ValueError("color must be #RRGGBB")
        return value.upper()


class BgmSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["upload", "library"]
    asset_id: str | None = None
    track_id: str | None = None

    @model_validator(mode="after")
    def _check_source_payload(self) -> "BgmSelectionRequest":
        if self.source == "upload":
            if not self.asset_id or self.track_id is not None:
                raise ValueError("upload BGM requires asset_id only")
        if self.source == "library":
            if not self.track_id or self.asset_id is not None:
                raise ValueError("library BGM requires track_id only")
        return self


class VideoGenerateRequest(BaseModel):
    """Request to generate a video from a topic.

    Note: there is deliberately no ``output_path`` field. The output location is
    chosen server-side under a task-isolated, whitelisted path (#002-RV P2); a
    client cannot direct the engine to write anywhere on disk.

    ``extra="forbid"`` rejects unknown fields (e.g. a sneaked-in ``output_path``)
    with 422 instead of silently ignoring them (#005-FIX P2).
    """

    model_config = ConfigDict(extra="forbid")

    topic: str | None = Field(
        default=None,
        max_length=_PHOTO_PROMPT_MAX_LENGTH,
        description="Theme/topic or fixed script; photo accepts up to 20,000 characters.",
    )
    master_prompt: str | None = Field(
        default=None,
        max_length=_PHOTO_PROMPT_MAX_LENGTH,
        description="Optional photo-wide style prefix encoded into the provider prompt.",
    )
    master_negative_prompt: str | None = Field(
        default=None,
        max_length=_PHOTO_PROMPT_MAX_LENGTH,
        description="Optional soft photo-wide negative guidance encoded into the prompt.",
    )
    prompt: str | None = Field(
        default=None,
        description="Prompt for video_gen (maximum 2,000 characters); stored as topic for history.",
    )
    script: str | None = Field(default=None, max_length=5000)
    voice_id: str | None = None
    avatar_asset_id: str | None = None
    avatar_video_asset_id: str | None = None
    align_audio_reverse: bool | None = None
    templ_start_seconds: float | None = Field(default=None, ge=0)
    open_sr: bool | None = None
    separate_vocal: bool | None = None
    open_scenedet: bool | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    aspect_ratio: RequestedImageAspectRatio = Field(default="9:16")
    subtitle_enabled: bool = True
    subtitle_style: SubtitleStyleRequest | None = None
    apply_visible_label: bool = False
    pipeline: str = Field(default="standard")
    mode: str = Field(default="generate", description="'generate' (LLM) or 'fixed' (use script)")
    # Which generation flow to run. 'mode' above is kept for the static-template
    # pipeline's script handling (generate|fixed); this selects the flow itself.
    video_mode: str = Field(
        default="static_template",
        description=(
            "static_template | seedance_t2v | seedance_i2v | avatar_talk | photo | video_gen"
        ),
    )
    image_key: str | None = Field(
        default=None,
        description="Tenant-relative upload key from POST /uploads (photo input)",
    )
    image_keys: list[str] = Field(
        default_factory=list,
        min_length=1,
        max_length=6,
        description="Tenant-relative upload keys from POST /uploads (photo references).",
    )
    similarity_strength: int | None = Field(
        default=None,
        ge=10,
        le=100,
        multiple_of=10,
        description="Optional photo reference-similarity guidance encoded into the prompt.",
    )
    creativity_strength: int | None = Field(
        default=None,
        ge=10,
        le=100,
        multiple_of=10,
        description="Optional photo creativity guidance encoded into the prompt.",
    )
    subject_strength: int | None = Field(
        default=None,
        ge=10,
        le=100,
        multiple_of=10,
        description="Optional photo subject-preservation guidance encoded into the prompt.",
    )
    product_image_keys: list[str] = Field(
        default_factory=list,
        min_length=1,
        max_length=9,
        description="Tenant-relative product upload keys for seedance_i2v (1-9 items).",
    )
    image_size: str = Field(
        default="1024x1024",
        description="Deprecated photo size; translated only when aspect_ratio is omitted.",
    )
    image_quality: str = Field(
        default="medium",
        description="Deprecated photo quality; accepted for compatibility and ignored.",
    )
    image_resolution: Literal["1k", "2k", "4k"] | None = Field(
        default="1k",
        description=(
            "Photo output resolution tier. Defaults to 1k; this field, not prompt text, "
            "controls the provider resolution parameter."
        ),
    )
    purpose: Literal["cover"] | None = Field(
        default=None,
        description="Optional photo generation purpose marker; cover reuses the photo pipeline.",
    )
    kind: Literal["cover"] | None = Field(
        default=None,
        description="Optional photo generation kind marker; cover reuses the photo pipeline.",
    )
    scene_prompt: str | None = Field(
        default=None,
        description="Overall visual prompt for seedance_i2v scene planning.",
    )
    negative_prompt: str | None = Field(
        default=None,
        description=(
            "Optional negative guidance; photo encodes it as soft prompt guidance and caps "
            "it at 20,000 characters, while existing video semantics remain unrestricted."
        ),
    )
    duration_sec: int | None = Field(
        default=None,
        description=(
            "Target duration in seconds; seedance_i2v clamps 5..120, "
            "video_gen accepts integer seconds from 4 through 15."
        ),
    )
    reference_image_asset_ids: list[str] = Field(default_factory=list)
    reference_video_asset_ids: list[str] = Field(default_factory=list)
    resolution: Literal["480p", "720p", "1080p"] = Field(default="720p")
    generate_audio: bool = Field(
        default=False,
        description="Whether video_gen asks the provider to generate synchronized audio.",
    )
    bgm: BgmSelectionRequest | None = None
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

    @field_validator("topic", "prompt")
    @classmethod
    def _strip_optional_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        text = v.strip()
        if not text:
            return text
        return text

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

    @field_validator("image_keys", mode="before")
    @classmethod
    def _check_image_key_count(cls, values):
        if isinstance(values, list | tuple):
            if len(values) > 6:
                raise ValueError("参考图最多支持 6 张")
            if not values:
                raise ValueError("参考图至少需要 1 张")
        return values

    @field_validator("image_keys")
    @classmethod
    def _check_image_keys(cls, values: list[str]) -> list[str]:
        if any(".." in value or not _IMAGE_KEY_RE.match(value) for value in values):
            raise ValueError("invalid image_keys (use keys returned by POST /uploads)")
        return values

    @field_validator("product_image_keys")
    @classmethod
    def _check_video_product_image_keys(cls, values: list[str]) -> list[str]:
        if any(".." in value or not _IMAGE_KEY_RE.match(value) for value in values):
            raise ValueError(
                "invalid product_image_keys (use keys returned by POST /uploads)"
            )
        return values

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
        if self.video_mode == "video_gen":
            video_gen_prompt = (self.prompt or self.topic or "").strip()
            if len(video_gen_prompt) > 2000:
                raise PydanticCustomError(
                    "friendly_video_gen_prompt_too_long",
                    "提示词输入最大上限为 2000 字",
                )
        if (
            self.video_mode != "photo"
            and self.topic is not None
            and len(self.topic) > 2000
        ):
            raise ValueError("non-photo topic must contain at most 2000 characters")
        if self.video_mode == "photo":
            if (self.image_key or self.image_keys) and (self.image_resolution or "1k") != "1k":
                raise ValueError("photo image editing supports only image_resolution=1k")
            photo_prompt_values = (
                self.topic,
                self.master_prompt,
                self.master_negative_prompt,
                self.negative_prompt,
            )
            if any(
                value is not None and len(value) > _PHOTO_PROMPT_MAX_LENGTH
                for value in photo_prompt_values
            ):
                raise ValueError("photo prompt fields must contain at most 20000 characters")
            if "aspect_ratio" not in self.model_fields_set:
                self.aspect_ratio = image_aspect_ratio_from_legacy_size(self.image_size)
        elif self.video_mode == "video_gen":
            if self.aspect_ratio not in VIDEO_GEN_ASPECT_RATIOS:
                raise ValueError(
                    "video_gen supports only 16:9, 9:16, 1:1, 4:3, 3:4, 21:9, or auto"
                )
        elif self.aspect_ratio not in VIDEO_ASPECT_RATIOS:
            raise ValueError("non-photo video modes support only 9:16, 16:9, or 1:1")

        if self.video_mode == "video_gen":
            prompt = (self.prompt or self.topic or "").strip()
            if not prompt:
                raise ValueError("video_gen prompt must not be blank")
            self.prompt = prompt
            self.topic = prompt
            if self.reference_image_asset_ids and self.reference_video_asset_ids:
                raise PydanticCustomError(
                    "friendly_video_gen_reference_media_conflict",
                    "参考图与参考视频不能同时使用，请选择其中一种。",
                )
            if not (0 <= len(self.reference_image_asset_ids) <= 9):
                raise ValueError("video_gen reference_image_asset_ids must contain at most 9 items")
            if len(set(self.reference_image_asset_ids)) != len(self.reference_image_asset_ids):
                raise ValueError("video_gen reference_image_asset_ids must be unique")
            if len(self.reference_video_asset_ids) > 3:
                raise PydanticCustomError(
                    "friendly_video_gen_reference_video_count_invalid",
                    "参考视频最多支持 3 条。",
                )
            if len(set(self.reference_video_asset_ids)) != len(
                self.reference_video_asset_ids
            ):
                raise PydanticCustomError(
                    "friendly_video_gen_reference_video_duplicate",
                    "参考视频不能重复选择。",
                )
            if (
                "aspect_ratio" not in self.model_fields_set
                and (self.reference_image_asset_ids or self.reference_video_asset_ids)
            ):
                self.aspect_ratio = "auto"
            if self.duration_sec is None or not (
                _VIDEO_GEN_MIN_DURATION_SEC
                <= self.duration_sec
                <= _VIDEO_GEN_MAX_DURATION_SEC
            ):
                raise ValueError("video_gen duration_sec must be between 4 and 15")
            return self

        if self.video_mode != "seedance_i2v" and not (self.topic or "").strip():
            raise ValueError("topic must not be blank")
        if (self.purpose == "cover" or self.kind == "cover") and self.video_mode != "photo":
            raise ValueError("cover purpose/kind requires video_mode=photo")
        if self.video_mode == "seedance_i2v":
            if self.duration_sec is not None:
                self.duration_sec = max(
                    _MIN_DURATION_SEC,
                    min(_MAX_DURATION_SEC, int(self.duration_sec)),
                )
            if not self.product_image_keys:
                raise ValueError(
                    "seedance_i2v requires product_image_keys (upload an image first)"
                )
            if not self.voice_id:
                raise ValueError("seedance_i2v requires voice_id")
        if self.video_mode == "avatar_talk":
            if not self.voice_id:
                raise ValueError("avatar_talk requires voice_id")
            source_count = int(bool(self.avatar_asset_id)) + int(bool(self.avatar_video_asset_id))
            if source_count != 1:
                raise ValueError(
                    "avatar_talk requires exactly one of avatar_asset_id or "
                    "avatar_video_asset_id"
                )
        return self


class VideoAccepted(BaseModel):
    id: str
    # Kept for the existing M2 frontend while the new contract standardizes on id.
    task_id: str
    status: str


class VideoEstimateResponse(BaseModel):
    estimated_credits: int
    unit: Literal["credits"] = "credits"
    note: str | None = None


class ScenePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str | None = Field(default=None, max_length=2000)
    script: str | None = Field(default=None, max_length=5000)
    product_image_keys: list[str] = Field(min_length=1, max_length=9)
    duration_sec: int | None = Field(default=None)

    @field_validator("product_image_keys")
    @classmethod
    def _check_product_image_keys(cls, values: list[str]) -> list[str]:
        if any(".." in value or not _IMAGE_KEY_RE.match(value) for value in values):
            raise ValueError(
                "invalid product_image_keys (use keys returned by POST /uploads)"
            )
        return values

    @field_validator("duration_sec")
    @classmethod
    def _clamp_duration(cls, v: int | None) -> int | None:
        if v is None:
            return v
        return max(_MIN_DURATION_SEC, min(_MAX_DURATION_SEC, int(v)))


class ScenePromptResponse(BaseModel):
    scene_prompt: str
    negative_prompt: str


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
    prompt: str = Field(
        description=(
            "Deprecated topic alias retained for compatibility; use topic or the "
            "mode-specific scene_prompt field."
        ),
        deprecated=True,
    )
    scene_prompt: str | None = None
    mode: str
    kind: str | None = None
    status: str
    progress: int
    topic: str | None = None
    script: str | None = None
    voice_id: str | None = None
    aspect_ratio: str | None = None
    image_resolution: Literal["1k", "2k", "4k"] | None = None
    requested_aspect_ratio: str | None = None
    resolved_aspect_ratio: str | None = None
    resolved_size: str | None = None
    actual_aspect_ratio: str | None = None
    actual_width: int | None = None
    actual_height: int | None = None
    actual_size: str | None = None
    subtitle_enabled: bool | None = None
    apply_visible_label: bool = False
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


class VideoDeletedResponse(BaseModel):
    deleted: bool


class VideoClearResponse(BaseModel):
    deleted_count: int
