from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from app.db.models import EcomReplicateJob, VideoTask
from app.db.session import SessionLocal


@dataclass(frozen=True)
class TaskClaim:
    claimed: bool
    status: str


def claim_video_task_for_worker(
    *,
    tenant_id: str,
    task_id: str,
    session_factory: Any = SessionLocal,
) -> TaskClaim:
    with session_factory() as db:
        now = datetime.now(UTC)
        result = db.execute(
            update(VideoTask)
            .where(
                VideoTask.id == task_id,
                VideoTask.tenant_id == tenant_id,
                VideoTask.status == "queued",
                VideoTask.deleted_at.is_(None),
            )
            .values(status="running", started_at=now, updated_at=now)
        )
        claimed = result.rowcount == 1
        status = "running" if claimed else db.scalar(
            select(VideoTask.status).where(
                VideoTask.id == task_id,
                VideoTask.tenant_id == tenant_id,
            )
        )
        db.commit()
    return TaskClaim(claimed=claimed, status=str(status or "missing"))


def claim_ecom_replicate_job_for_worker(
    *,
    job_id: str,
    session_factory: Any = SessionLocal,
) -> TaskClaim:
    with session_factory() as db:
        now = datetime.now(UTC)
        result = db.execute(
            update(EcomReplicateJob)
            .where(
                EcomReplicateJob.id == job_id,
                EcomReplicateJob.status == "generating",
                EcomReplicateJob.started_at.is_(None),
            )
            .values(started_at=now, updated_at=now)
        )
        claimed = result.rowcount == 1
        status = "generating" if claimed else db.scalar(
            select(EcomReplicateJob.status).where(EcomReplicateJob.id == job_id)
        )
        db.commit()
    return TaskClaim(claimed=claimed, status=str(status or "missing"))
