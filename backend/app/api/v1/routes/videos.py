import asyncio
import json
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
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
from app.db.models import Asset, TaskAsset, User, VideoTask, Voice
from app.schemas.response import ApiResponse, ok
from app.schemas.videos import (
    VideoAccepted,
    VideoGenerateRequest,
    VideoListResponse,
    VideoRead,
)
from app.services.progress import ProgressStore
from app.services.quota import reserve_avatar_talk_quota
from app.services.storage.base import ObjectStorage
from app.workers.avatar_talk import generate_avatar_talk_task
from app.workers.video_tasks import generate_video_task

router = APIRouter()
ProgressStoreDependency = Depends(get_progress_store)
ObjectStorageDependency = Depends(get_object_storage)
CreateVideoPermissionDependency = Depends(require_permission("video:create"))
_video_task_tenants: dict[str, str] = {}

# Terminal states that end an SSE stream.
_TERMINAL = {"SUCCESS", "FAILURE"}
_SSE_INTERVAL_S = 1.0


def _api_status(task: VideoTask, snapshot: dict | None = None) -> str:
    status_value = str(snapshot.get("status") if snapshot else task.status).upper()
    if status_value in {"SUCCESS", "DONE"}:
        return "done"
    if status_value in {"FAILURE", "FAILED"}:
        return "failed"
    if status_value in {"STARTED", "PROGRESS", "RUNNING"}:
        return "running"
    return "queued"


def _progress(task: VideoTask, snapshot: dict | None = None) -> int:
    if snapshot and snapshot.get("progress") is not None:
        return max(0, min(100, int(float(snapshot["progress"]) * 100)))
    return max(0, min(100, int(task.progress or 0)))


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
    if status_value == "done" and task.storage_key:
        playback_url = storage.presign_get_url(
            task.storage_key, expires_in=settings.engine_s3_presign_ttl
        )
        download_url = storage.presign_get_url(
            task.storage_key,
            expires_in=settings.engine_s3_presign_ttl,
            download_filename=f"{task.id}.mp4",
        )
    if task.thumbnail_key:
        thumbnail_url = storage.presign_get_url(
            task.thumbnail_key, expires_in=settings.engine_s3_presign_ttl
        )
    return VideoRead(
        id=task.id,
        title=(task.topic or "Untitled video")[:80],
        prompt=task.topic or "",
        mode=task.mode or task.video_mode,
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
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[VideoListResponse]:
    query = select(VideoTask).where(VideoTask.tenant_id == user.tenant_id)
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
    voice = db.get(Voice, payload.voice_id)
    if voice is None or not voice.is_active:
        raise AppError("Voice not found.", code="VOICE_NOT_FOUND", status_code=404)
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
        voice_id=payload.voice_id,
        speed=Decimal(str(payload.speed)),
        aspect_ratio=payload.aspect_ratio,
        subtitle_enabled=payload.subtitle_enabled,
        params={
            "avatar_asset_id": payload.avatar_asset_id,
            "estimated": True,
        },
    )
    db.add(task)
    db.add(TaskAsset(video_task_id=task_id, asset_id=avatar.id, role="input_avatar"))
    reserve_avatar_talk_quota(
        db,
        tenant_id=user.tenant_id,
        video_task_id=task_id,
        script=script or payload.topic,
        speed=payload.speed,
    )
    db.commit()
    _video_task_tenants[task_id] = user.tenant_id
    return task_id


@router.post("", response_model=ApiResponse[VideoAccepted], status_code=status.HTTP_202_ACCEPTED)
def create_video(
    request: Request,
    payload: VideoGenerateRequest,
    user: User = CreateVideoPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[VideoAccepted]:
    avatar_talk_requested = (
        payload.video_mode == "avatar_talk"
        or bool(payload.voice_id)
        or bool(payload.avatar_asset_id)
    )
    if avatar_talk_requested:
        task_id = _create_avatar_talk_video(payload, user=user, db=db)
        params = payload.model_dump()
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
    params = payload.model_dump()
    params["tenant_id"] = user.tenant_id
    params["video_task_id"] = task_id
    result = generate_video_task.apply_async(args=[params], task_id=task_id)
    _video_task_tenants[task_id] = user.tenant_id
    return ok(request, VideoAccepted(id=task_id, task_id=task_id, status=result.status))


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


def _sse_progress(snapshot: dict) -> int:
    progress = snapshot.get("progress", 0)
    value = float(progress or 0)
    if value <= 1:
        value *= 100
    return max(0, min(100, int(round(value))))


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
