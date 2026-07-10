import asyncio
import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUserDependency,
    DbSessionDependency,
    get_object_storage,
    get_progress_store,
    require_permission,
    scoped_task_id,
)
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.core.utils import base_mime
from app.db.models import (
    Asset,
    BgmLibraryTrack,
    BrandVoice,
    TaskAsset,
    User,
    VideoTask,
    Voice,
)
from app.providers.base import invoke, resolve
from app.schemas.response import ApiResponse, ok
from app.schemas.videos import (
    ScenePromptRequest,
    ScenePromptResponse,
    VideoAccepted,
    VideoClearResponse,
    VideoDeletedResponse,
    VideoEstimateResponse,
    VideoGenerateRequest,
    VideoListResponse,
    VideoRead,
)
from app.services import provider_costs
from app.services.bgm_library import ensure_default_bgm_tracks
from app.services.history import (
    clear_video_history,
    delete_video_task,
    history_kind,
    prune_video_history,
    video_mode_filter,
)
from app.services.progress import ProgressStore
from app.services.quota import (
    QuotaEstimate,
    estimate_avatar_talk_quota,
    estimate_image_generation_quota,
    estimate_seedance_i2v_quota,
    estimate_video_gen_quota,
    reserve_avatar_talk_quota,
    reserve_image_generation_quota,
    reserve_seedance_i2v_quota,
    reserve_video_gen_quota,
    seedance_i2v_billable_seconds,
    seedance_i2v_target_seconds,
)
from app.services.storage.base import ObjectStorage
from app.workers.avatar_talk import (
    build_seedance_scene_prompt_payload,
    generate_avatar_talk_task,
    generate_seedance_i2v_task,
)
from app.workers.image_gen import generate_image_task
from app.workers.video_gen import generate_video_gen_task
from app.workers.video_tasks import generate_video_task

router = APIRouter()
logger = get_logger(__name__)
ProgressStoreDependency = Depends(get_progress_store)
ObjectStorageDependency = Depends(get_object_storage)
CreateVideoPermissionDependency = Depends(require_permission("video:create"))
_video_task_tenants: dict[str, str] = {}

# Terminal states that end an SSE stream.
_TERMINAL = {"SUCCESS", "FAILURE"}
_SSE_INTERVAL_S = 1.0
_ESTIMATE_NOTE = "Estimated reservation; final settlement uses actual generated duration."
_VIDEO_HISTORY_MODE_PATTERN = "^(avatar_talk|seedance_i2v|photo|video_gen)$"
_VIDEO_GEN_IMAGE_TYPES = {"avatar_image", "product_image", "generated_image", "cover"}
_AVATAR_VIDEO_SOURCE_MAX_BYTES = 200 * 1024 * 1024
_AVATAR_VIDEO_SOURCE_MIN_DURATION_MS = 3_000
_AVATAR_VIDEO_SOURCE_MAX_DURATION_MS = 10_000
_AVATAR_VIDEO_SOURCE_MAX_DIMENSION = 1920
_AVATAR_VIDEO_SOURCE_MIN_DIMENSION = 360
_AVATAR_VIDEO_CODECS = {"h264", "avc1"}
_AVATAR_VIDEO_AUDIO_CODECS = {"aac", "mp4a"}
_CHANGE_LIPS_OPTIONAL_FIELDS = {
    "align_audio_reverse",
    "templ_start_seconds",
    "open_sr",
    "separate_vocal",
    "open_scenedet",
}


@dataclass(frozen=True)
class _AvatarVideoProbe:
    duration_ms: int | None
    width: int | None
    height: int | None
    container: str | None
    video_codec: str | None
    audio_codec: str | None


def _is_avatar_talk_requested(payload: VideoGenerateRequest) -> bool:
    if payload.video_mode == "photo":
        return False
    return (
        payload.video_mode == "avatar_talk"
        or bool(payload.voice_id)
        or bool(payload.avatar_asset_id)
        or bool(payload.avatar_video_asset_id)
    )


def _worker_params(payload: VideoGenerateRequest) -> dict:
    params = payload.model_dump()
    if payload.video_mode == "photo":
        params.pop("image_size", None)
        params.pop("image_quality", None)
        params["requested_aspect_ratio"] = payload.aspect_ratio
    if payload.subtitle_style is None:
        params.pop("subtitle_style", None)
    else:
        params["subtitle_style"] = payload.subtitle_style.model_dump(exclude_none=True)
    if payload.purpose == "cover" or payload.kind == "cover":
        params["purpose"] = "cover"
        params["kind"] = "cover"
    else:
        params.pop("purpose", None)
        params.pop("kind", None)
    return params


def _video_gen_worker_params(payload: VideoGenerateRequest) -> dict:
    params = {
        "video_mode": "video_gen",
        "prompt": payload.topic,
        "topic": payload.topic,
        "reference_image_asset_ids": list(payload.reference_image_asset_ids),
        "duration_sec": int(payload.duration_sec or 5),
        "resolution": payload.resolution,
        "apply_visible_label": payload.apply_visible_label,
    }
    if payload.bgm is not None:
        params["bgm"] = payload.bgm.model_dump(exclude_none=True)
    return params


def _subtitle_style_params(payload: VideoGenerateRequest) -> dict | None:
    if payload.subtitle_style is None:
        return None
    return payload.subtitle_style.model_dump(exclude_none=True)


def _resolve_avatar_talk_voice(
    db: Session,
    *,
    tenant_id: str,
    voice_id: str,
) -> tuple[Voice | None, BrandVoice | None]:
    voice = db.get(Voice, voice_id)
    if voice is not None and voice.is_active:
        return voice, None
    brand_voice = db.get(BrandVoice, voice_id)
    if (
        brand_voice is not None
        and brand_voice.tenant_id == tenant_id
        and brand_voice.deleted_at is None
        and brand_voice.status == "ready"
        and brand_voice.speaker_id
    ):
        return None, brand_voice
    raise AppError("Voice not found.", code="VOICE_NOT_FOUND", status_code=404)


def _avatar_source_count(payload: VideoGenerateRequest) -> int:
    return int(bool(payload.avatar_asset_id)) + int(bool(payload.avatar_video_asset_id))


def _metadata_str(asset: Asset, *keys: str) -> str | None:
    metadata = asset.metadata_ or {}
    for key in keys:
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _metadata_int(asset: Asset, *keys: str) -> int | None:
    for key in keys:
        value = (asset.metadata_ or {}).get(key)
        try:
            if value not in (None, ""):
                return int(float(str(value)))
        except (TypeError, ValueError):
            continue
    return None


def _avatar_video_probe_from_metadata(asset: Asset) -> _AvatarVideoProbe | None:
    container = _metadata_str(asset, "container", "format", "format_name")
    video_codec = _metadata_str(asset, "video_codec", "codec_name")
    audio_codec = _metadata_str(asset, "audio_codec")
    width = asset.width or _metadata_int(asset, "width", "video_width")
    height = asset.height or _metadata_int(asset, "height", "video_height")
    duration_ms = asset.duration_ms or _metadata_int(asset, "duration_ms")
    if not all([container, video_codec, audio_codec, width, height, duration_ms]):
        return None
    return _AvatarVideoProbe(
        duration_ms=duration_ms,
        width=width,
        height=height,
        container=container,
        video_codec=video_codec,
        audio_codec=audio_codec,
    )


def _probe_avatar_video_bytes(content: bytes, *, suffix: str) -> _AvatarVideoProbe:
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix or ".mp4") as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(temp_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise AppError(
                "Avatar source video cannot be decoded. Please upload an MP4/H.264/AAC video.",
                code="AVATAR_VIDEO_DECODE_FAILED",
                status_code=422,
            ) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise AppError(
            "Avatar source video cannot be decoded. Please upload an MP4/H.264/AAC video.",
            code="AVATAR_VIDEO_DECODE_FAILED",
            status_code=422,
        )
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AppError(
            "Avatar source video cannot be decoded. Please upload an MP4/H.264/AAC video.",
            code="AVATAR_VIDEO_DECODE_FAILED",
            status_code=422,
        ) from exc
    streams = data.get("streams") if isinstance(data, dict) else []
    video_stream = next(
        (stream for stream in streams or [] if stream.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (stream for stream in streams or [] if stream.get("codec_type") == "audio"),
        {},
    )
    format_info = data.get("format") if isinstance(data, dict) else {}
    duration = format_info.get("duration") or video_stream.get("duration")
    duration_ms = None
    try:
        duration_ms = int(round(float(duration) * 1000)) if duration is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    return _AvatarVideoProbe(
        duration_ms=duration_ms,
        width=int(video_stream["width"]) if video_stream.get("width") else None,
        height=int(video_stream["height"]) if video_stream.get("height") else None,
        container=str(format_info.get("format_name") or ""),
        video_codec=str(video_stream.get("codec_name") or ""),
        audio_codec=str(audio_stream.get("codec_name") or ""),
    )


def _avatar_video_probe(asset: Asset, *, storage: ObjectStorage) -> _AvatarVideoProbe:
    probe = _avatar_video_probe_from_metadata(asset)
    if probe is not None:
        return probe
    try:
        content = storage.get_bytes(asset.storage_key)
    except Exception as exc:
        raise AppError(
            "Avatar source video metadata is incomplete and the object could not be read.",
            code="AVATAR_VIDEO_NOT_READABLE",
            status_code=422,
        ) from exc
    suffix = Path(asset.storage_key).suffix or ".mp4"
    return _probe_avatar_video_bytes(content, suffix=suffix)


def _avatar_video_size_bytes(asset: Asset, *, storage: ObjectStorage) -> int | None:
    if asset.size_bytes is not None:
        return asset.size_bytes
    try:
        return len(storage.get_bytes(asset.storage_key))
    except Exception as exc:
        raise AppError(
            "Avatar source video metadata is incomplete and the object could not be read.",
            code="AVATAR_VIDEO_NOT_READABLE",
            status_code=422,
        ) from exc


def _validate_avatar_video_probe(asset: Asset, probe: _AvatarVideoProbe) -> None:
    if base_mime(asset.mime_type) != "video/mp4":
        raise AppError(
            "Avatar source video must be MP4.",
            code="AVATAR_VIDEO_UNSUPPORTED_FORMAT",
            status_code=422,
        )
    if asset.size_bytes is not None and asset.size_bytes > _AVATAR_VIDEO_SOURCE_MAX_BYTES:
        raise AppError(
            f"Avatar source video is too large; limit is {_AVATAR_VIDEO_SOURCE_MAX_BYTES} bytes.",
            code="AVATAR_VIDEO_TOO_LARGE",
            status_code=413,
        )
    if (
        probe.duration_ms is None
        or probe.duration_ms < _AVATAR_VIDEO_SOURCE_MIN_DURATION_MS
        or probe.duration_ms > _AVATAR_VIDEO_SOURCE_MAX_DURATION_MS
    ):
        raise AppError(
            "Avatar source video must be between 3 and 10 seconds.",
            code="AVATAR_VIDEO_DURATION_INVALID",
            status_code=422,
        )
    width = int(probe.width or 0)
    height = int(probe.height or 0)
    if (
        min(width, height) < _AVATAR_VIDEO_SOURCE_MIN_DIMENSION
        or max(width, height) > _AVATAR_VIDEO_SOURCE_MAX_DIMENSION
    ):
        raise AppError(
            "Avatar source video resolution must be between 360p and 1080p.",
            code="AVATAR_VIDEO_RESOLUTION_INVALID",
            status_code=422,
        )
    container = (probe.container or "").lower()
    if "mp4" not in container and container not in {"mov,mp4,m4a,3gp,3g2,mj2"}:
        raise AppError(
            "Avatar source video must be MP4.",
            code="AVATAR_VIDEO_UNSUPPORTED_FORMAT",
            status_code=422,
        )
    if (probe.video_codec or "").lower() not in _AVATAR_VIDEO_CODECS:
        raise AppError(
            "Avatar source video must use H.264 video.",
            code="AVATAR_VIDEO_CODEC_INVALID",
            status_code=422,
        )
    if (probe.audio_codec or "").lower() not in _AVATAR_VIDEO_AUDIO_CODECS:
        raise AppError(
            "Avatar source video must include AAC audio.",
            code="AVATAR_VIDEO_AUDIO_CODEC_INVALID",
            status_code=422,
        )


def _avatar_image_asset_or_404(db: Session, *, tenant_id: str, asset_id: str) -> Asset:
    avatar = db.get(Asset, asset_id)
    if (
        avatar is None
        or avatar.type != "avatar_image"
        or avatar.status != "ready"
        or avatar.deleted_at is not None
        or avatar.tenant_id not in {tenant_id, None}
    ):
        raise AppError("Avatar asset not found.", code="AVATAR_ASSET_NOT_FOUND", status_code=404)
    return avatar


def _avatar_video_asset_or_404(
    db: Session,
    *,
    tenant_id: str,
    asset_id: str,
    storage: ObjectStorage,
) -> Asset:
    avatar_video = db.get(Asset, asset_id)
    if (
        avatar_video is None
        or avatar_video.tenant_id != tenant_id
        or avatar_video.type != "video"
        or avatar_video.status != "ready"
        or avatar_video.deleted_at is not None
    ):
        raise AppError(
            "Avatar source video not found.",
            code="AVATAR_VIDEO_ASSET_NOT_FOUND",
            status_code=404,
        )
    if (avatar_video.metadata_ or {}).get("purpose") != "avatar_source":
        raise AppError(
            "Avatar source video not found.",
            code="AVATAR_VIDEO_ASSET_NOT_FOUND",
            status_code=404,
        )
    size_bytes = _avatar_video_size_bytes(avatar_video, storage=storage)
    if size_bytes is not None and size_bytes > _AVATAR_VIDEO_SOURCE_MAX_BYTES:
        raise AppError(
            f"Avatar source video is too large; limit is {_AVATAR_VIDEO_SOURCE_MAX_BYTES} bytes.",
            code="AVATAR_VIDEO_TOO_LARGE",
            status_code=413,
        )
    _validate_avatar_video_probe(
        avatar_video,
        _avatar_video_probe(avatar_video, storage=storage),
    )
    return avatar_video


def _quota_estimate_for_payload(
    payload: VideoGenerateRequest,
    *,
    tenant_id: str,
    db: Session,
) -> QuotaEstimate | None:
    if payload.video_mode == "photo":
        return estimate_image_generation_quota(
            db,
            tenant_id=tenant_id,
            n=1,
        )
    if payload.video_mode == "seedance_i2v":
        target_duration_sec = seedance_i2v_target_seconds(payload.duration_sec)
        return estimate_seedance_i2v_quota(
            db,
            tenant_id=tenant_id,
            script=payload.script or payload.topic,
            speed=payload.speed,
            estimated_seconds=seedance_i2v_billable_seconds(target_duration_sec),
            resolution=payload.resolution,
        )
    if payload.video_mode == "video_gen":
        return estimate_video_gen_quota(
            db,
            tenant_id=tenant_id,
            duration_sec=int(payload.duration_sec or 5),
            resolution=payload.resolution,
        )
    if _is_avatar_talk_requested(payload):
        if not payload.voice_id:
            raise AppError(
                "avatar_talk requires voice_id.",
                code="VALIDATION_ERROR",
                status_code=422,
            )
        if _avatar_source_count(payload) != 1:
            raise AppError(
                "avatar_talk requires exactly one of avatar_asset_id or avatar_video_asset_id.",
                code="VALIDATION_ERROR",
                status_code=422,
            )
        return estimate_avatar_talk_quota(
            db,
            tenant_id=tenant_id,
            script=payload.script or payload.topic,
            speed=payload.speed,
        )
    return None


def _api_status(task: VideoTask, snapshot: dict | None = None) -> str:
    status_value = str(snapshot.get("status") if snapshot else task.status).upper()
    if status_value in {"SUCCESS", "DONE"}:
        return "done"
    if status_value in {"FAILURE", "FAILED"}:
        return "failed"
    if status_value == "CANCELLED":
        return "cancelled"
    if status_value in {"STARTED", "PROGRESS", "RUNNING"}:
        return "running"
    return "queued"


def _normalize_progress_value(progress: object) -> float:
    value = float(progress or 0)
    if value <= 1:
        value *= 100
    return max(0.0, min(100.0, value))


def _normalize_progress(progress: object) -> int:
    return int(round(_normalize_progress_value(progress)))


def _progress(task: VideoTask, snapshot: dict | None = None) -> int:
    if snapshot and snapshot.get("progress") is not None:
        return _normalize_progress(snapshot["progress"])
    return _normalize_progress(task.progress)


def _video_read(
    task: VideoTask,
    *,
    storage: ObjectStorage,
    snapshot: dict | None = None,
) -> VideoRead:
    status_value = _api_status(task, snapshot)
    playback_url = None
    download_url = None
    thumbnail_url = None
    mode = task.mode or task.video_mode
    params = task.params or {}
    if status_value == "done" and task.storage_key:
        playback_url = storage.presign_get_url(
            task.storage_key, expires_in=settings.engine_s3_presign_ttl
        )
        download_url = storage.presign_get_url(
            task.storage_key,
            expires_in=settings.engine_s3_presign_ttl,
            download_filename=f"{task.id}.png" if mode == "photo" else f"{task.id}.mp4",
        )
        if mode == "photo":
            thumbnail_url = playback_url
    if task.thumbnail_key:
        thumbnail_url = storage.presign_get_url(
            task.thumbnail_key, expires_in=settings.engine_s3_presign_ttl
        )
    return VideoRead(
        id=task.id,
        title=(task.topic or ("Untitled image" if mode == "photo" else "Untitled video"))[:80],
        prompt=task.topic or "",
        mode=mode,
        kind=history_kind(task),
        status=status_value,
        progress=_progress(task, snapshot),
        topic=task.topic,
        script=task.script,
        voice_id=task.voice_id,
        aspect_ratio=task.aspect_ratio,
        requested_aspect_ratio=(
            params.get("requested_aspect_ratio") if mode == "photo" else None
        )
        or (task.aspect_ratio if mode == "photo" else None),
        resolved_aspect_ratio=params.get("resolved_aspect_ratio"),
        resolved_size=params.get("resolved_size"),
        actual_aspect_ratio=params.get("actual_aspect_ratio"),
        actual_width=params.get("actual_width"),
        actual_height=params.get("actual_height"),
        actual_size=params.get("actual_size"),
        subtitle_enabled=task.subtitle_enabled,
        created_at=task.created_at,
        duration_sec=task.duration_sec,
        duration_ms=int(task.duration_sec * 1000) if task.duration_sec is not None else None,
        apply_visible_label=bool(params.get("apply_visible_label", False)),
        thumbnail_url=thumbnail_url,
        playback_url=playback_url,
        download_url=download_url,
        error=task.error,
        error_code=task.error_code,
        error_message=task.error_message,
    )


def _snapshot_for(store: ProgressStore, tenant_id: str, task_id: str) -> dict | None:
    return store.read(scoped_task_id(tenant_id, task_id))


@router.get("", response_model=ApiResponse[VideoListResponse])
def list_videos(
    request: Request,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    store: ProgressStore = ProgressStoreDependency,
    storage: ObjectStorage = ObjectStorageDependency,
    mode: str | None = Query(default=None, pattern=_VIDEO_HISTORY_MODE_PATTERN),
    kind: str | None = Query(default=None, pattern="^[A-Za-z0-9_-]{1,40}$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[VideoListResponse]:
    query = select(VideoTask).where(VideoTask.tenant_id == user.tenant_id)
    query = query.where(
        or_(
            VideoTask.params["kind"].as_string().is_(None),
            VideoTask.params["kind"].as_string() != "ecom_poster",
        )
    )
    if mode is not None:
        query = query.where(video_mode_filter(mode))
    if kind is not None:
        query = query.where(
            or_(
                VideoTask.params["kind"].as_string() == kind,
                VideoTask.params["purpose"].as_string() == kind,
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    tasks = list(
        db.scalars(query.order_by(VideoTask.created_at.desc()).offset(offset).limit(limit))
    )
    items = [
        _video_read(task, storage=storage, snapshot=_snapshot_for(store, user.tenant_id, task.id))
        for task in tasks
    ]
    return ok(request, VideoListResponse(items=items, total=total))


def _create_avatar_talk_video(
    payload: VideoGenerateRequest,
    *,
    user: User,
    db: Session,
    storage: ObjectStorage,
) -> str:
    if not payload.voice_id:
        raise AppError("avatar_talk requires voice_id.", code="VALIDATION_ERROR", status_code=422)
    if _avatar_source_count(payload) != 1:
        raise AppError(
            "avatar_talk requires exactly one of avatar_asset_id or avatar_video_asset_id.",
            code="VALIDATION_ERROR",
            status_code=422,
        )
    voice, brand_voice = _resolve_avatar_talk_voice(
        db,
        tenant_id=user.tenant_id,
        voice_id=payload.voice_id,
    )
    if payload.avatar_video_asset_id:
        avatar = _avatar_video_asset_or_404(
            db,
            tenant_id=user.tenant_id,
            asset_id=payload.avatar_video_asset_id,
            storage=storage,
        )
        avatar_source_type = "video"
    else:
        avatar = _avatar_image_asset_or_404(
            db,
            tenant_id=user.tenant_id,
            asset_id=str(payload.avatar_asset_id),
        )
        avatar_source_type = "image"

    task_id = str(uuid4())
    script = payload.script
    params = {
        "avatar_source_type": avatar_source_type,
        "estimated": True,
        "apply_visible_label": payload.apply_visible_label,
    }
    if payload.avatar_video_asset_id:
        params["avatar_video_asset_id"] = payload.avatar_video_asset_id
        for key in _CHANGE_LIPS_OPTIONAL_FIELDS:
            value = getattr(payload, key)
            if value is not None:
                params[key] = value
    else:
        params["avatar_asset_id"] = payload.avatar_asset_id
    if brand_voice is not None:
        params.update(
            {
                "voice_source": "brand_voice",
                "brand_voice_id": brand_voice.id,
                "tts_speaker_id": brand_voice.speaker_id,
                "brand_voice_provider": brand_voice.provider,
            }
        )
    subtitle_style = _subtitle_style_params(payload)
    if subtitle_style is not None:
        params["subtitle_style"] = subtitle_style
    task = VideoTask(
        id=task_id,
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="queued",
        topic=payload.topic,
        script=script,
        mode="avatar_talk",
        video_mode="avatar_talk",
        progress=0,
        voice_id=voice.id if voice is not None else None,
        brand_voice_id=brand_voice.id if brand_voice is not None else None,
        speed=Decimal(str(payload.speed)),
        aspect_ratio=payload.aspect_ratio,
        subtitle_enabled=payload.subtitle_enabled,
        params=params,
    )
    # Persist the parent video_task BEFORE inserting rows that FK-reference it
    # (task_asset, and the usage_record created in reserve_*). Without this flush
    # the child insert can hit the DB before the parent exists → FK violation
    # (P0-A: task_assets_video_task_id_fkey). Same transaction, single commit.
    db.add(task)
    db.flush()
    db.add(TaskAsset(video_task_id=task.id, asset_id=avatar.id, role="input_avatar"))
    reserve_avatar_talk_quota(
        db,
        tenant_id=user.tenant_id,
        video_task_id=task.id,
        script=script or payload.topic,
        speed=payload.speed,
    )
    db.commit()
    _video_task_tenants[task_id] = user.tenant_id
    return task_id


def _create_seedance_i2v_video(
    payload: VideoGenerateRequest,
    *,
    user: User,
    db: Session,
) -> str:
    if not payload.voice_id:
        raise AppError("seedance_i2v requires voice_id.", code="VALIDATION_ERROR", status_code=422)
    voice = db.get(Voice, payload.voice_id)
    if voice is None or not voice.is_active:
        raise AppError("Voice not found.", code="VOICE_NOT_FOUND", status_code=404)

    task_id = str(uuid4())
    script = payload.script
    target_duration_sec = seedance_i2v_target_seconds(payload.duration_sec)
    params = {
        "image_key": payload.image_key,
        "scene_prompt": payload.scene_prompt,
        "duration_sec": target_duration_sec,
        "resolution": payload.resolution,
        "estimated": True,
        "apply_visible_label": payload.apply_visible_label,
    }
    subtitle_style = _subtitle_style_params(payload)
    if subtitle_style is not None:
        params["subtitle_style"] = subtitle_style
    task = VideoTask(
        id=task_id,
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="queued",
        topic=payload.topic,
        script=script,
        mode="seedance_i2v",
        video_mode="seedance_i2v",
        progress=0,
        voice_id=payload.voice_id,
        speed=Decimal(str(payload.speed)),
        aspect_ratio=payload.aspect_ratio,
        subtitle_enabled=payload.subtitle_enabled,
        duration_sec=target_duration_sec,
        params=params,
    )
    db.add(task)
    db.flush()
    reserve_seedance_i2v_quota(
        db,
        tenant_id=user.tenant_id,
        video_task_id=task.id,
        script=script or payload.topic,
        speed=payload.speed,
        estimated_seconds=seedance_i2v_billable_seconds(target_duration_sec),
        resolution=payload.resolution,
    )
    db.commit()
    _video_task_tenants[task_id] = user.tenant_id
    return task_id


def _video_gen_reference_assets_or_404(
    db: Session,
    *,
    tenant_id: str,
    asset_ids: list[str],
) -> list[Asset]:
    assets = list(
        db.scalars(
            select(Asset).where(
                Asset.id.in_(asset_ids),
                Asset.tenant_id == tenant_id,
                Asset.status == "ready",
                Asset.deleted_at.is_(None),
            )
        )
    )
    by_id = {asset.id: asset for asset in assets}
    ordered = [by_id.get(asset_id) for asset_id in asset_ids]
    if any(asset is None for asset in ordered):
        raise AppError(
            "Reference image not found.",
            code="REFERENCE_IMAGE_NOT_FOUND",
            status_code=404,
        )
    resolved = [asset for asset in ordered if asset is not None]
    if any(
        asset.type not in _VIDEO_GEN_IMAGE_TYPES
        or not str(asset.mime_type or "image/").startswith("image/")
        for asset in resolved
    ):
        raise AppError(
            "Reference image not found.",
            code="REFERENCE_IMAGE_NOT_FOUND",
            status_code=404,
        )
    return resolved


def _video_gen_bgm_upload_or_404(
    db: Session,
    *,
    tenant_id: str,
    asset_id: str,
) -> Asset:
    asset = db.get(Asset, asset_id)
    if (
        asset is None
        or asset.tenant_id != tenant_id
        or asset.type not in {"audio", "bgm"}
        or asset.status != "ready"
        or asset.deleted_at is not None
    ):
        raise AppError("BGM asset not found.", code="BGM_ASSET_NOT_FOUND", status_code=404)
    return asset


def _video_gen_library_track_or_404(
    db: Session,
    *,
    track_id: str,
    storage: ObjectStorage,
) -> BgmLibraryTrack:
    ensure_default_bgm_tracks(db, storage=storage)
    track = db.get(BgmLibraryTrack, track_id)
    if track is None or not track.is_active:
        raise AppError("BGM track not found.", code="BGM_TRACK_NOT_FOUND", status_code=404)
    return track


def _create_video_gen_video(
    payload: VideoGenerateRequest,
    *,
    user: User,
    db: Session,
    storage: ObjectStorage,
) -> str:
    reference_assets = _video_gen_reference_assets_or_404(
        db,
        tenant_id=user.tenant_id,
        asset_ids=list(payload.reference_image_asset_ids),
    )
    bgm_asset: Asset | None = None
    if payload.bgm is not None and payload.bgm.source == "upload":
        bgm_asset = _video_gen_bgm_upload_or_404(
            db,
            tenant_id=user.tenant_id,
            asset_id=str(payload.bgm.asset_id),
        )
    if payload.bgm is not None and payload.bgm.source == "library":
        _video_gen_library_track_or_404(
            db,
            track_id=str(payload.bgm.track_id),
            storage=storage,
        )

    task_id = str(uuid4())
    params = _video_gen_worker_params(payload)
    task = VideoTask(
        id=task_id,
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="queued",
        topic=payload.topic,
        script=payload.script,
        mode="video_gen",
        video_mode="video_gen",
        progress=0,
        aspect_ratio=payload.aspect_ratio,
        duration_sec=float(payload.duration_sec or 5),
        params=params,
    )
    db.add(task)
    db.flush()
    for asset in reference_assets:
        db.add(TaskAsset(video_task_id=task.id, asset_id=asset.id, role="input_reference_image"))
    if bgm_asset is not None:
        db.add(TaskAsset(video_task_id=task.id, asset_id=bgm_asset.id, role="input_bgm"))
    reserve_video_gen_quota(
        db,
        tenant_id=user.tenant_id,
        video_task_id=task.id,
        duration_sec=int(payload.duration_sec or 5),
        resolution=payload.resolution,
    )
    db.commit()
    _video_task_tenants[task_id] = user.tenant_id
    return task_id


def _create_photo_video(
    payload: VideoGenerateRequest,
    *,
    user: User,
    db: Session,
) -> str:
    task_id = str(uuid4())
    params = {
        "image_key": payload.image_key,
        "aspect_ratio": payload.aspect_ratio,
        "requested_aspect_ratio": payload.aspect_ratio,
        "estimated": True,
        "apply_visible_label": payload.apply_visible_label,
    }
    if payload.purpose == "cover" or payload.kind == "cover":
        params["purpose"] = "cover"
        params["kind"] = "cover"
    task = VideoTask(
        id=task_id,
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="queued",
        topic=payload.topic,
        script=payload.script,
        mode="photo",
        video_mode="photo",
        progress=0,
        aspect_ratio=payload.aspect_ratio,
        params=params,
    )
    db.add(task)
    db.flush()
    reserve_image_generation_quota(
        db,
        tenant_id=user.tenant_id,
        video_task_id=task.id,
        n=1,
    )
    db.commit()
    _video_task_tenants[task_id] = user.tenant_id
    return task_id


def _prune_after_create(
    db: Session,
    *,
    tenant_id: str,
    mode: str,
    storage: ObjectStorage,
) -> None:
    try:
        prune_video_history(db, tenant_id=tenant_id, mode=mode, storage=storage)
    except Exception as exc:  # pragma: no cover - non-blocking cleanup guard
        logger.warning(
            "video_history_prune_failed",
            tenant_id=tenant_id,
            mode=mode,
            error=str(exc),
        )


@router.post("/scene-prompt", response_model=ApiResponse[ScenePromptResponse])
def generate_scene_prompt(
    request: Request,
    payload: ScenePromptRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ScenePromptResponse]:
    if not (
        settings.engine_llm_api_key
        and settings.engine_llm_base_url
        and settings.engine_llm_model
    ):
        raise AppError(
            "DeepSeek is not configured.",
            code="LLM_NOT_CONFIGURED",
            status_code=503,
        )

    provider = resolve(db, tenant_id=user.tenant_id, capability="llm")
    result = asyncio.run(
        invoke(
            db,
            tenant_id=user.tenant_id,
            capability="llm",
            provider=provider.__class__.__name__,
            operation=lambda: provider.generate_text(
                build_seedance_scene_prompt_payload(
                    payload.topic,
                    duration_sec=payload.duration_sec,
                )
            ),
            timeout_seconds=30.0,
        )
    )
    provider_costs.record_deepseek_usage(
        db,
        tenant_id=user.tenant_id,
        result=result,
    )
    scene_prompt = str(result.get("text") or "").strip()
    if not scene_prompt:
        raise AppError(
            "DeepSeek returned an empty scene prompt.",
            code="LLM_EMPTY_RESULT",
            status_code=502,
        )
    db.commit()
    return ok(request, ScenePromptResponse(scene_prompt=scene_prompt))


@router.post("/estimate", response_model=ApiResponse[VideoEstimateResponse])
def estimate_video(
    request: Request,
    payload: VideoGenerateRequest,
    user: User = CreateVideoPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[VideoEstimateResponse]:
    estimate = _quota_estimate_for_payload(payload, tenant_id=user.tenant_id, db=db)
    return ok(
        request,
        VideoEstimateResponse(
            estimated_credits=estimate.reservation_units if estimate is not None else 0,
            unit="credits",
            note=_ESTIMATE_NOTE,
        ),
    )


@router.post("", response_model=ApiResponse[VideoAccepted], status_code=status.HTTP_202_ACCEPTED)
def create_video(
    request: Request,
    payload: VideoGenerateRequest,
    user: User = CreateVideoPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[VideoAccepted]:
    if payload.video_mode == "photo":
        task_id = _create_photo_video(payload, user=user, db=db)
        _prune_after_create(db, tenant_id=user.tenant_id, mode="photo", storage=storage)
        params = _worker_params(payload)
        params["tenant_id"] = user.tenant_id
        params["video_task_id"] = task_id
        generate_image_task.apply_async(args=[params], task_id=task_id, queue="image")
        return ok(request, VideoAccepted(id=task_id, task_id=task_id, status="queued"))

    if payload.video_mode == "seedance_i2v":
        task_id = _create_seedance_i2v_video(payload, user=user, db=db)
        _prune_after_create(db, tenant_id=user.tenant_id, mode="seedance_i2v", storage=storage)
        params = _worker_params(payload)
        params["tenant_id"] = user.tenant_id
        params["video_task_id"] = task_id
        generate_seedance_i2v_task.apply_async(args=[params], task_id=task_id, queue="video")
        return ok(request, VideoAccepted(id=task_id, task_id=task_id, status="queued"))

    if payload.video_mode == "video_gen":
        task_id = _create_video_gen_video(payload, user=user, db=db, storage=storage)
        _prune_after_create(db, tenant_id=user.tenant_id, mode="video_gen", storage=storage)
        params = _video_gen_worker_params(payload)
        params["tenant_id"] = user.tenant_id
        params["video_task_id"] = task_id
        generate_video_gen_task.apply_async(args=[params], task_id=task_id, queue="video")
        return ok(request, VideoAccepted(id=task_id, task_id=task_id, status="queued"))

    if _is_avatar_talk_requested(payload):
        task_id = _create_avatar_talk_video(payload, user=user, db=db, storage=storage)
        _prune_after_create(db, tenant_id=user.tenant_id, mode="avatar_talk", storage=storage)
        params = _worker_params(payload)
        params["tenant_id"] = user.tenant_id
        params["video_task_id"] = task_id
        generate_avatar_talk_task.apply_async(args=[params], task_id=task_id, queue="avatar")
        return ok(request, VideoAccepted(id=task_id, task_id=task_id, status="queued"))

    task_id = str(uuid4())
    task = VideoTask(
        id=task_id,
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="queued",
        topic=payload.topic,
        video_mode=payload.video_mode,
        progress=0,
        params={"apply_visible_label": payload.apply_visible_label},
    )
    db.add(task)
    db.commit()
    params = _worker_params(payload)
    params["tenant_id"] = user.tenant_id
    params["video_task_id"] = task_id
    result = generate_video_task.apply_async(args=[params], task_id=task_id)
    _video_task_tenants[task_id] = user.tenant_id
    return ok(request, VideoAccepted(id=task_id, task_id=task_id, status=result.status))


@router.delete("", response_model=ApiResponse[VideoClearResponse])
def clear_videos(
    request: Request,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
    mode: str = Query(pattern=_VIDEO_HISTORY_MODE_PATTERN),
) -> ApiResponse[VideoClearResponse]:
    deleted_count = clear_video_history(
        db,
        tenant_id=user.tenant_id,
        mode=mode,
        storage=storage,
    )
    return ok(request, VideoClearResponse(deleted_count=deleted_count))


@router.delete("/{task_id}", response_model=ApiResponse[VideoDeletedResponse])
def delete_video(
    request: Request,
    task_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[VideoDeletedResponse]:
    delete_result = delete_video_task(
        db,
        tenant_id=user.tenant_id,
        task_id=task_id,
        storage=storage,
    )
    if delete_result == "missing":
        raise AppError("Video task not found.", code="VIDEO_TASK_NOT_FOUND", status_code=404)
    return ok(request, VideoDeletedResponse(deleted=delete_result == "deleted"))


@router.get("/{task_id}", response_model=ApiResponse[VideoRead])
def get_video_status(
    request: Request,
    task_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    store: ProgressStore = ProgressStoreDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[VideoRead]:
    task = db.get(VideoTask, task_id)
    if task is None or task.tenant_id != user.tenant_id:
        raise AppError("Video task not found.", code="VIDEO_TASK_NOT_FOUND", status_code=404)

    snapshot = _snapshot_for(store, user.tenant_id, task_id)
    return ok(request, _video_read(task, storage=storage, snapshot=snapshot))


@router.get("/{task_id}/events")
async def stream_video_events(
    task_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    store: ProgressStore = ProgressStoreDependency,
) -> StreamingResponse:
    """Server-Sent Events stream of progress snapshots until the task is terminal."""
    owner_tenant_id = _video_task_tenants.get(task_id)
    task = db.get(VideoTask, task_id)
    if task is not None:
        owner_tenant_id = task.tenant_id
    if owner_tenant_id is not None and owner_tenant_id != user.tenant_id:
        raise AppError("Video task not found.", code="VIDEO_TASK_NOT_FOUND", status_code=404)
    if owner_tenant_id is None and task is None:
        raise AppError("Video task not found.", code="VIDEO_TASK_NOT_FOUND", status_code=404)

    async def event_generator():
        scoped_id = scoped_task_id(user.tenant_id, task_id)
        last: str | None = None
        max_ticks = max(1, int(settings.sse_timeout_seconds / _SSE_INTERVAL_S))
        for _ in range(max_ticks):
            snapshot = store.read(scoped_id)
            if snapshot:
                payload = json.dumps(_sse_payload(task_id, snapshot))
                if payload != last:
                    yield f"data: {payload}\n\n"
                    last = payload
                if str(snapshot.get("status", "")).upper() in _TERMINAL or snapshot.get(
                    "status"
                ) in {"done", "failed"}:
                    return
            await asyncio.sleep(_SSE_INTERVAL_S)

        # Reached the cap without a terminal status: tell the client explicitly
        # instead of silently closing the stream (#005-FIX P2).
        timeout_event = {
            "task_id": task_id,
            "stage": "sse_timeout",
            "timeout_seconds": settings.sse_timeout_seconds,
        }
        yield f"data: {json.dumps(timeout_event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _sse_status(status_value: object) -> str:
    text = str(status_value or "").upper()
    if text in {"SUCCESS", "DONE"}:
        return "done"
    if text in {"FAILURE", "FAILED"}:
        return "failed"
    if text == "CANCELLED":
        return "cancelled"
    if text in {"STARTED", "PROGRESS", "RUNNING"}:
        return "running"
    if text == "QUEUED":
        return "queued"
    return str(status_value or "queued")


def _sse_progress(snapshot: dict) -> int | float:
    value = _normalize_progress_value(snapshot.get("progress", 0))
    return int(value) if value.is_integer() else value


def _sse_payload(task_id: str, snapshot: dict) -> dict:
    payload = {
        "task_id": task_id,
        "status": _sse_status(snapshot.get("status")),
        "progress": _sse_progress(snapshot),
    }
    step = snapshot.get("step") or snapshot.get("stage")
    if step:
        payload["step"] = step
    if payload["status"] == "done":
        for name in ("playback_url", "download_url", "thumbnail_url"):
            if snapshot.get(name):
                payload[name] = snapshot[name]
    if payload["status"] == "failed":
        payload["error_code"] = snapshot.get("error_code") or "VIDEO_TASK_FAILED"
        payload["error_message"] = snapshot.get("error_message") or snapshot.get("error")
    return payload
