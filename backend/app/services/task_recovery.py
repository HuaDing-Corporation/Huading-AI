from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import (
    EcomReplicateJob,
    EcomReplicateOutput,
    ReversePromptJob,
    VideoTask,
)
from app.db.session import SessionLocal
from app.services.aibrain import recover_stale_reasoning_reservations
from app.services.batches import refresh_batch_job
from app.services.quota import (
    release_reserved_quota,
    release_reverse_prompt_video_quota,
)

logger = get_logger(__name__)

_IMAGE_WORKER_LOST_MESSAGE = "Image generation worker stopped before completion."
_VIDEO_GEN_WORKER_LOST_MESSAGE = "Video generation worker stopped before completion."
_REVERSE_WORKER_LOST_MESSAGE = "Video reverse-prompt worker stopped before completion."
_REPLICATE_WORKER_LOST_MESSAGE = (
    "E-commerce replicate worker stopped before completion."
)
_VIDEO_TASK_RECOVERY_ERRORS = {
    "photo": ("IMAGE_GEN_FAILED", _IMAGE_WORKER_LOST_MESSAGE),
    "video_gen": ("VIDEO_GEN_FAILED", _VIDEO_GEN_WORKER_LOST_MESSAGE),
}


@dataclass(frozen=True)
class ImageQueueRecoveryResult:
    photo_tasks: int = 0
    video_gen_tasks: int = 0
    reverse_prompt_jobs: int = 0
    ecom_replicate_jobs: int = 0
    aibrain_reservations: int = 0


def recover_orphaned_image_queue_tasks(
    *,
    session_factory: Any = SessionLocal,
    now: datetime | None = None,
    stale_after_seconds: float | None = None,
    aibrain_stale_after_seconds: float | None = None,
    progress_store: Any | None = None,
) -> ImageQueueRecoveryResult:
    recovered_at = now or datetime.now(UTC)
    stale_seconds = (
        settings.engine_orphan_task_stale_seconds
        if stale_after_seconds is None
        else stale_after_seconds
    )
    cutoff = recovered_at - timedelta(seconds=stale_seconds)
    aibrain_stale_seconds = (
        settings.engine_aibrain_reservation_stale_minutes * 60
        if aibrain_stale_after_seconds is None
        else aibrain_stale_after_seconds
    )
    aibrain_cutoff = recovered_at - timedelta(seconds=aibrain_stale_seconds)
    recovered_progress: list[tuple[str, str, str, str]] = []
    recovered_video_gen_batches: set[tuple[str, str]] = set()
    photo_task_count = 0
    video_gen_task_count = 0

    with session_factory() as db:
        video_tasks = list(
            db.scalars(
                select(VideoTask)
                .where(
                    VideoTask.video_mode.in_(_VIDEO_TASK_RECOVERY_ERRORS),
                    VideoTask.status == "running",
                    VideoTask.deleted_at.is_(None),
                    VideoTask.updated_at <= cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for task in video_tasks:
            error_code, error_message = _VIDEO_TASK_RECOVERY_ERRORS[task.video_mode]
            if task.video_mode == "photo":
                photo_task_count += 1
            else:
                video_gen_task_count += 1
                if task.batch_id:
                    recovered_video_gen_batches.add((task.tenant_id, task.batch_id))
            release_reserved_quota(
                db,
                tenant_id=task.tenant_id,
                video_task_id=task.id,
            )
            task.status = "failed"
            task.error = error_message
            task.error_code = error_code
            task.error_message = error_message
            task.finished_at = recovered_at
            task.updated_at = recovered_at
            recovered_progress.append(
                (task.tenant_id, task.id, error_code, error_message)
            )
        for tenant_id, batch_id in sorted(recovered_video_gen_batches):
            refresh_batch_job(db, batch_id=batch_id, tenant_id=tenant_id)

        reverse_prompt_jobs = list(
            db.scalars(
                select(ReversePromptJob)
                .where(
                    ReversePromptJob.source_kind == "video",
                    ReversePromptJob.status == "running",
                    ReversePromptJob.updated_at <= cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in reverse_prompt_jobs:
            release_reverse_prompt_video_quota(
                db,
                tenant_id=job.tenant_id,
                reverse_prompt_job_id=job.id,
            )
            job.status = "failed"
            job.error_code = "REVERSE_PROMPT_FAILED"
            job.error_message = _REVERSE_WORKER_LOST_MESSAGE
            job.updated_at = recovered_at

        recent_replicate_output = exists().where(
            EcomReplicateOutput.job_id == EcomReplicateJob.id,
            EcomReplicateOutput.updated_at > cutoff,
        )
        replicate_jobs = list(
            db.scalars(
                select(EcomReplicateJob)
                .where(
                    EcomReplicateJob.status == "generating",
                    EcomReplicateJob.started_at.is_not(None),
                    EcomReplicateJob.updated_at <= cutoff,
                    ~recent_replicate_output,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in replicate_jobs:
            outputs = list(
                db.scalars(
                    select(EcomReplicateOutput)
                    .where(EcomReplicateOutput.job_id == job.id)
                    .with_for_update()
                )
            )
            for output in outputs:
                if output.status not in {"planned", "generating"}:
                    continue
                output.status = "failed"
                output.error_code = "ECOM_REPLICATE_RENDER_FAILED"
                output.error_message = _REPLICATE_WORKER_LOST_MESSAGE
                output.updated_at = recovered_at

            succeeded = sum(output.status == "succeeded" for output in outputs)
            failed = sum(output.status == "failed" for output in outputs)
            if outputs and succeeded == len(outputs):
                job.status = "completed"
                job.error_code = None
                job.error_message = None
            elif succeeded:
                job.status = "partial_failed"
                job.error_code = "ECOM_REPLICATE_PARTIAL_FAILED"
                job.error_message = f"{failed} outputs failed after worker stopped."
            else:
                job.status = "failed"
                job.error_code = "ECOM_REPLICATE_FAILED"
                job.error_message = _REPLICATE_WORKER_LOST_MESSAGE
            job.finished_at = recovered_at
            job.updated_at = recovered_at
        aibrain_reservations = recover_stale_reasoning_reservations(
            db,
            cutoff=aibrain_cutoff,
            recovered_at=recovered_at,
        )
        db.commit()

    if progress_store is not None:
        for tenant_id, task_id, error_code, error_message in recovered_progress:
            try:
                progress_store.update(
                    f"{tenant_id}:{task_id}",
                    status="failed",
                    stage="failed",
                    error=error_message,
                    error_code=error_code,
                    error_message=error_message,
                )
            except Exception as exc:  # pragma: no cover - Redis is best effort
                logger.warning(
                    "orphan_task.progress_update_failed",
                    task_id=task_id,
                    error_type=type(exc).__name__,
                )

    return ImageQueueRecoveryResult(
        photo_tasks=photo_task_count,
        video_gen_tasks=video_gen_task_count,
        reverse_prompt_jobs=len(reverse_prompt_jobs),
        ecom_replicate_jobs=len(replicate_jobs),
        aibrain_reservations=aibrain_reservations,
    )
