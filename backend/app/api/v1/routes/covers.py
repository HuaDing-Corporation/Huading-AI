from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency, get_object_storage
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.models import Asset, TaskAsset, User, VideoTask
from app.schemas.covers import (
    CoverFromFrameRequest,
    CoverFromFrameResponse,
    CoverRead,
    FrameCandidate,
    FrameCandidatesResponse,
)
from app.schemas.response import ApiResponse, ok
from app.services.covers import (
    CoverFrameError,
    CoverTimestampOutOfRange,
    candidate_timestamps,
    clamp_frame_count,
    extract_frame_candidates,
    extract_frame_cover,
)
from app.services.history import prune_video_history
from app.services.storage.base import ObjectStorage
from app.services.synthetic_label import (
    label_artifact_bytes,
    synthetic_label_context,
)

router = APIRouter()
logger = get_logger(__name__)
ObjectStorageDependency = Depends(get_object_storage)


def _apply_synthetic_cover_label(
    db: Session,
    *,
    tenant_id: str,
    content_id: str,
    image_bytes: bytes,
) -> tuple[bytes, dict[str, object]]:
    label_settings, meta, payload = synthetic_label_context(
        db,
        tenant_id=tenant_id,
        content_id=content_id,
    )
    return (
        label_artifact_bytes(
            image_bytes,
            kind="image",
            settings=label_settings,
            meta=meta,
            suffix=".png",
        ),
        payload,
    )


def _oral_task_or_error(db: Session, *, tenant_id: str, task_id: str) -> VideoTask:
    task = db.get(VideoTask, task_id)
    if (
        task is None
        or task.tenant_id != tenant_id
        or task.deleted_at is not None
        or (task.mode != "avatar_talk" and task.video_mode != "avatar_talk")
    ):
        raise AppError("Video task not found.", code="VIDEO_TASK_NOT_FOUND", status_code=404)
    if task.status != "done" or not task.storage_key:
        raise AppError(
            "Video task is not ready for cover generation.",
            code="VIDEO_TASK_NOT_READY",
            status_code=status.HTTP_409_CONFLICT,
        )
    return task


@router.get("/frame-candidates", response_model=ApiResponse[FrameCandidatesResponse])
def frame_candidates(
    request: Request,
    video_task_id: str = Query(min_length=1),
    count: int = Query(default=5),
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[FrameCandidatesResponse]:
    task = _oral_task_or_error(db, tenant_id=user.tenant_id, task_id=video_task_id)
    safe_count = clamp_frame_count(count)
    timestamps = candidate_timestamps(task.duration_sec, safe_count)
    try:
        frames = extract_frame_candidates(
            storage,
            video_key=task.storage_key,
            timestamps=timestamps,
        )
    except CoverFrameError as exc:
        raise AppError(
            "Could not extract frame candidates.",
            code="COVER_FRAME_EXTRACTION_FAILED",
            status_code=422,
        ) from exc

    response_frames: list[FrameCandidate] = []
    for index, frame in enumerate(frames):
        millis = int(round(frame.timestamp_sec * 1000))
        key = (
            f"tenants/{user.tenant_id}/covers/{task.id}/candidates/"
            f"{index:02d}-{millis}.jpg"
        )
        storage.put_bytes(key, frame.image_bytes, content_type="image/jpeg")
        response_frames.append(
            FrameCandidate(
                timestamp_sec=frame.timestamp_sec,
                preview_url=storage.presign_get_url(
                    key,
                    expires_in=settings.engine_s3_presign_ttl,
                ),
            )
        )
    return ok(request, FrameCandidatesResponse(frames=response_frames))


@router.post("/from-frame", response_model=ApiResponse[CoverFromFrameResponse])
def cover_from_frame(
    request: Request,
    payload: CoverFromFrameRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[CoverFromFrameResponse]:
    task = _oral_task_or_error(db, tenant_id=user.tenant_id, task_id=payload.video_task_id)
    title = _title_payload(payload)
    try:
        cover = extract_frame_cover(
            storage,
            video_key=task.storage_key,
            timestamp_sec=payload.timestamp_sec,
            title=title,
        )
    except CoverTimestampOutOfRange as exc:
        raise AppError(
            "timestamp_sec is outside the video duration.",
            code="COVER_TIMESTAMP_OUT_OF_RANGE",
            status_code=422,
        ) from exc
    except CoverFrameError as exc:
        raise AppError(
            "Could not extract cover frame.",
            code="COVER_FRAME_EXTRACTION_FAILED",
            status_code=422,
        ) from exc

    cover_task_id = str(uuid4())
    cover_id = str(uuid4())
    storage_key = f"tenants/{user.tenant_id}/photos/{cover_task_id}/output.png"
    cover_bytes, label_metadata = _apply_synthetic_cover_label(
        db,
        tenant_id=user.tenant_id,
        content_id=cover_task_id,
        image_bytes=cover.image_bytes,
    )
    storage.put_bytes(storage_key, cover_bytes, content_type="image/png")
    cover_task = VideoTask(
        id=cover_task_id,
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="done",
        topic=title["text"] if title else task.topic,
        script=None,
        mode="photo",
        video_mode="photo",
        progress=100,
        storage_bucket=storage.bucket,
        storage_key=storage_key,
        thumbnail_key=storage_key,
        content_type="image/png",
        size_bytes=len(cover_bytes),
        finished_at=datetime.now(UTC),
        params={
            "kind": "cover",
            "purpose": "cover",
            "source": "frame",
            "source_video_task_id": task.id,
            "timestamp_sec": payload.timestamp_sec,
            "layout_template_id": payload.layout_template_id,
        },
    )
    asset = Asset(
        id=cover_id,
        tenant_id=user.tenant_id,
        type="generated_image",
        source="generated",
        provider="frame",
        storage_key=storage_key,
        mime_type="image/png",
        size_bytes=len(cover_bytes),
        width=cover.width,
        height=cover.height,
        status="ready",
        metadata_={
            "kind": "cover",
            "purpose": "cover",
            "source": "frame",
            "video_task_id": task.id,
            "source_video_task_id": task.id,
            "timestamp_sec": payload.timestamp_sec,
            "layout_template_id": payload.layout_template_id,
            "title": title,
            "synthetic_label": label_metadata,
        },
    )
    db.add_all([cover_task, asset])
    db.flush()
    db.add(TaskAsset(video_task_id=cover_task.id, asset_id=asset.id, role="output_image"))
    db.commit()
    try:
        prune_video_history(db, tenant_id=user.tenant_id, mode="photo", storage=storage)
    except Exception as exc:  # pragma: no cover - non-blocking cleanup guard
        logger.warning(
            "cover_history_prune_failed",
            tenant_id=user.tenant_id,
            source_video_task_id=task.id,
            error=str(exc),
        )

    return ok(
        request,
        CoverFromFrameResponse(
            cover=CoverRead(
                id=asset.id,
                image_url=storage.presign_get_url(
                    storage_key,
                    expires_in=settings.engine_s3_presign_ttl,
                ),
                width=cover.width,
                height=cover.height,
            )
        ),
    )


def _title_payload(payload: CoverFromFrameRequest) -> dict | None:
    if payload.title is None:
        return None
    title = payload.title.model_dump(exclude_none=True)
    if not str(title.get("text") or "").strip():
        return None
    return title
