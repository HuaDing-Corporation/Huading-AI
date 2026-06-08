import asyncio
import json

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_progress_store
from app.core.config import settings
from app.schemas.response import ApiResponse, ok
from app.schemas.videos import VideoAccepted, VideoGenerateRequest, VideoTaskStatus
from app.services.progress import ProgressStore
from app.workers.celery_app import celery_app
from app.workers.video_tasks import generate_video_task

router = APIRouter()
ProgressStoreDependency = Depends(get_progress_store)

# Terminal states that end an SSE stream.
_TERMINAL = {"SUCCESS", "FAILURE"}
_SSE_INTERVAL_S = 1.0


@router.post("", response_model=ApiResponse[VideoAccepted], status_code=status.HTTP_202_ACCEPTED)
def create_video(request: Request, payload: VideoGenerateRequest) -> ApiResponse[VideoAccepted]:
    result = generate_video_task.delay(payload.model_dump())
    return ok(request, VideoAccepted(task_id=result.id, status=result.status))


@router.get("/{task_id}", response_model=ApiResponse[VideoTaskStatus])
def get_video_status(
    request: Request,
    task_id: str,
    store: ProgressStore = ProgressStoreDependency,
) -> ApiResponse[VideoTaskStatus]:
    snapshot = store.read(task_id)
    if snapshot:
        return ok(request, VideoTaskStatus(**snapshot))

    # No progress recorded yet: fall back to Celery's view (PENDING/STARTED/...).
    result = AsyncResult(task_id, app=celery_app)
    return ok(request, VideoTaskStatus(task_id=task_id, status=result.status))


@router.get("/{task_id}/events")
async def stream_video_events(
    task_id: str,
    store: ProgressStore = ProgressStoreDependency,
) -> StreamingResponse:
    """Server-Sent Events stream of progress snapshots until the task is terminal."""

    async def event_generator():
        last: str | None = None
        max_ticks = max(1, int(settings.sse_timeout_seconds / _SSE_INTERVAL_S))
        for _ in range(max_ticks):
            snapshot = store.read(task_id)
            if snapshot:
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
