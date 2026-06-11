import asyncio
import json
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
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
from app.db.models import User, VideoTask
from app.schemas.response import ApiResponse, ok
from app.schemas.videos import (
    VideoAccepted,
    VideoGenerateRequest,
    VideoListResponse,
    VideoRead,
)
from app.services.progress import ProgressStore
from app.services.storage.base import ObjectStorage
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
        mode=task.video_mode,
        status=status_value,
        progress=_progress(task, snapshot),
        created_at=task.created_at,
        duration_sec=task.duration_sec,
        thumbnail_url=thumbnail_url,
        playback_url=playback_url,
        download_url=download_url,
        error=task.error,
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
    cursor: str | None = Query(default=None),
) -> ApiResponse[VideoListResponse]:
    query = select(VideoTask).where(VideoTask.tenant_id == user.tenant_id)
    if cursor:
        query = query.where(VideoTask.created_at < datetime.fromisoformat(cursor))
    tasks = list(db.scalars(query.order_by(VideoTask.created_at.desc()).limit(limit + 1)))
    items = [
        _video_read(task, storage=storage, snapshot=_snapshot_for(store, user.tenant_id, task.id))
        for task in tasks[:limit]
    ]
    next_cursor = tasks[limit].created_at.isoformat() if len(tasks) > limit else None
    return ok(request, VideoListResponse(items=items, next_cursor=next_cursor))


@router.post("", response_model=ApiResponse[VideoAccepted], status_code=status.HTTP_202_ACCEPTED)
def create_video(
    request: Request,
    payload: VideoGenerateRequest,
    user: User = CreateVideoPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[VideoAccepted]:
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
    return ok(request, VideoAccepted(task_id=task_id, status=result.status))


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
    store: ProgressStore = ProgressStoreDependency,
) -> StreamingResponse:
    """Server-Sent Events stream of progress snapshots until the task is terminal."""
    owner_tenant_id = _video_task_tenants.get(task_id)
    if owner_tenant_id is not None and owner_tenant_id != user.tenant_id:
        raise AppError("Video task not found.", code="VIDEO_TASK_NOT_FOUND", status_code=404)
    if owner_tenant_id is None:
        raise AppError("Video task not found.", code="VIDEO_TASK_NOT_FOUND", status_code=404)

    async def event_generator():
        scoped_id = scoped_task_id(user.tenant_id, task_id)
        last: str | None = None
        max_ticks = max(1, int(settings.sse_timeout_seconds / _SSE_INTERVAL_S))
        for _ in range(max_ticks):
            snapshot = store.read(scoped_id)
            if snapshot:
                snapshot["task_id"] = task_id
                payload = json.dumps(snapshot)
                if payload != last:
                    yield f"data: {payload}\n\n"
                    last = payload
                if snapshot.get("status") in _TERMINAL:
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
