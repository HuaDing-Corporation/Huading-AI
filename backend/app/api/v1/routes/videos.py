import asyncio
import json
from decimal import Decimal
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
from app.db.models import Asset, BrandVoice, TaskAsset, User, VideoTask, Voice
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
    reserve_avatar_talk_quota,
    reserve_image_generation_quota,
    reserve_seedance_i2v_quota,
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


def _is_avatar_talk_requested(payload: VideoGenerateRequest) -> bool:
    if payload.video_mode == "photo":
        return False
    return (
        payload.video_mode == "avatar_talk"
        or bool(payload.voice_id)
        or bool(payload.avatar_asset_id)
    )


def _worker_params(payload: VideoGenerateRequest) -> dict:
    params = payload.model_dump()
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
            quality=payload.image_quality,
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
        )
    if _is_avatar_talk_requested(payload):
        if not payload.voice_id:
            raise AppError(
                "avatar_talk requires voice_id.",
                code="VALIDATION_ERROR",
                status_code=422,
            )
        if not payload.avatar_asset_id:
            raise AppError(
                "avatar_talk requires avatar_asset_id.",
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
        subtitle_enabled=task.subtitle_enabled,
        created_at=task.created_at,
        duration_sec=task.duration_sec,
        duration_ms=int(task.duration_sec * 1000) if task.duration_sec is not None else None,
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
    mode: str | None = Query(default=None, pattern="^(avatar_talk|seedance_i2v|photo)$"),
    kind: str | None = Query(default=None, pattern="^[A-Za-z0-9_-]{1,40}$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[VideoListResponse]:
    query = select(VideoTask).where(VideoTask.tenant_id == user.tenant_id)
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
) -> str:
    if not payload.voice_id:
        raise AppError("avatar_talk requires voice_id.", code="VALIDATION_ERROR", status_code=422)
    if not payload.avatar_asset_id:
        raise AppError(
            "avatar_talk requires avatar_asset_id.",
            code="VALIDATION_ERROR",
            status_code=422,
        )
    voice, brand_voice = _resolve_avatar_talk_voice(
        db,
        tenant_id=user.tenant_id,
        voice_id=payload.voice_id,
    )
    avatar = db.get(Asset, payload.avatar_asset_id)
    if (
        avatar is None
        or avatar.type != "avatar_image"
        or avatar.status != "ready"
        or avatar.deleted_at is not None
        or avatar.tenant_id not in {user.tenant_id, None}
    ):
        raise AppError("Avatar asset not found.", code="AVATAR_ASSET_NOT_FOUND", status_code=404)

    task_id = str(uuid4())
    script = payload.script
    params = {
        "avatar_asset_id": payload.avatar_asset_id,
        "estimated": True,
    }
    if brand_voice is not None:
        params.update(
            {
                "voice_source": "brand_voice",
                "brand_voice_id": brand_voice.id,
                "tts_speaker_id": brand_voice.speaker_id,
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
        "estimated": True,
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
        "image_size": payload.image_size,
        "image_quality": payload.image_quality,
        "estimated": True,
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
        quality=payload.image_quality,
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
    scene_prompt = str(result.get("text") or "").strip()
    if not scene_prompt:
        raise AppError(
            "DeepSeek returned an empty scene prompt.",
            code="LLM_EMPTY_RESULT",
            status_code=502,
        )
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
        generate_seedance_i2v_task.apply_async(args=[params], task_id=task_id, queue="avatar")
        return ok(request, VideoAccepted(id=task_id, task_id=task_id, status="queued"))

    if _is_avatar_talk_requested(payload):
        task_id = _create_avatar_talk_video(payload, user=user, db=db)
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
    mode: str = Query(pattern="^(avatar_talk|seedance_i2v|photo)$"),
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
