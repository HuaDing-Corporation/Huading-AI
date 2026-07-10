from __future__ import annotations

import asyncio
import base64
import inspect
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from sqlalchemy import update

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ReversePromptJob
from app.db.session import SessionLocal
from app.providers.base import resolve
from app.services import quota
from app.services.reverse_prompt import (
    mark_reverse_prompt_job_failed,
    mark_reverse_prompt_job_succeeded,
    nonnegative_int,
    source_asset_or_raise,
)
from app.services.storage.factory import create_object_storage

_MAX_VIDEO_DURATION_SEC = 60.0
_FRAME_MAX_EDGE = 768
_FRAME_TIMEOUT_SEC = 30.0
logger = get_logger(__name__)


class ReversePromptVideoProcessingError(RuntimeError):
    pass


def frame_timestamps(duration_sec: float) -> list[float]:
    duration = float(duration_sec)
    if duration < 1.0 or duration > _MAX_VIDEO_DURATION_SEC:
        raise ReversePromptVideoProcessingError(
            "Reverse prompt video must be between 1 and 60 seconds."
        )
    frame_count = 8 if duration <= 30.0 else 12
    return [round((index + 0.5) * duration / frame_count, 6) for index in range(frame_count)]


def extract_uniform_video_frames(
    video_path: Path,
    *,
    duration_sec: float,
    run: Callable[..., Any] = subprocess.run,
) -> list[bytes]:
    frames: list[bytes] = []
    for timestamp in frame_timestamps(duration_sec):
        command = [
            os.environ.get("FFMPEG_BINARY", "ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            (f"scale={_FRAME_MAX_EDGE}:{_FRAME_MAX_EDGE}:force_original_aspect_ratio=decrease"),
            "-q:v",
            "3",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
        ]
        result = run(
            command,
            capture_output=True,
            check=False,
            timeout=_FRAME_TIMEOUT_SEC,
        )
        frame = bytes(result.stdout or b"")
        if result.returncode != 0 or not frame:
            stderr = bytes(result.stderr or b"").decode("utf-8", errors="replace")
            raise ReversePromptVideoProcessingError(
                f"Failed to extract video frame at {timestamp:.3f}s: {stderr[:500]}"
            )
        frames.append(frame)
    return frames


def claim_reverse_prompt_video_job(db, *, job_id: str) -> bool:
    claimed = db.execute(
        update(ReversePromptJob)
        .where(
            ReversePromptJob.id == job_id,
            ReversePromptJob.source_kind == "video",
            ReversePromptJob.status == "queued",
        )
        .values(
            status="running",
            error_code=None,
            error_message=None,
            updated_at=datetime.now(UTC),
        )
    )
    db.commit()
    return claimed.rowcount == 1


def run_reverse_prompt_video_job(
    job_id: str,
    *,
    session_factory=SessionLocal,
) -> dict[str, str]:
    temp_path: Path | None = None
    with session_factory() as db:
        job = db.get(ReversePromptJob, job_id)
        if job is None or job.source_kind != "video":
            raise ReversePromptVideoProcessingError("Reverse prompt video job not found.")
        if job.status != "queued":
            return {"job_id": job.id, "status": job.status}
        if not claim_reverse_prompt_video_job(db, job_id=job.id):
            db.expire_all()
            current = db.get(ReversePromptJob, job_id)
            status = current.status if current is not None else "missing"
            return {"job_id": job_id, "status": status}
        db.refresh(job)

        try:
            source_kind, source = source_asset_or_raise(
                db,
                tenant_id=job.tenant_id,
                asset_id=str(job.source_asset_id or ""),
            )
            if source_kind != "video":
                raise ReversePromptVideoProcessingError("Reverse prompt source is not a video.")
            storage = create_object_storage(settings)
            video_bytes = storage.get_bytes(source.storage_key)
            if not video_bytes:
                raise ReversePromptVideoProcessingError("Reverse prompt video is empty.")
            with NamedTemporaryFile(delete=False, suffix=".mp4") as temp_file:
                temp_file.write(video_bytes)
                temp_path = Path(temp_file.name)

            duration_sec = float(source.duration_ms or 0) / 1000.0
            timestamps_sec = frame_timestamps(duration_sec)
            frames = extract_uniform_video_frames(
                temp_path,
                duration_sec=duration_sec,
            )
            image_urls = [
                "data:image/jpeg;base64," + base64.b64encode(frame).decode("ascii")
                for frame in frames
            ]
            provider = resolve(db, tenant_id=job.tenant_id, capability="reverse_prompt")
            operation = provider.reverse_video_frames(
                {
                    "image_urls": image_urls,
                    "timestamps_sec": timestamps_sec,
                    "duration_sec": duration_sec,
                    "target_format": job.target_format,
                }
            )
            result = asyncio.run(operation) if inspect.isawaitable(operation) else operation
            if not isinstance(result, dict):
                result = dict(result)
            if not isinstance(result.get("video_analysis"), dict):
                raise ReversePromptVideoProcessingError(
                    "Reverse prompt provider returned no video analysis."
                )
            video_analysis = dict(result["video_analysis"])
            video_analysis["duration_sec"] = duration_sec
            video_analysis["audio_transcript"] = None
            video_analysis["bgm_style"] = None
            result = {**result, "video_analysis": video_analysis}
            mark_reverse_prompt_job_succeeded(db, job=job, result=result)
            total_tokens = nonnegative_int(result.get("total_tokens")) or (
                nonnegative_int(result.get("prompt_tokens"))
                + nonnegative_int(result.get("completion_tokens"))
            )
            quota.settle_reverse_prompt_video_quota(
                db,
                tenant_id=job.tenant_id,
                reverse_prompt_job_id=job.id,
                provider=str(result.get("provider") or "apimart"),
                model=str(result.get("model") or settings.engine_apimart_reverse_prompt_model),
                total_tokens=total_tokens,
                cost_cents=nonnegative_int(result.get("cost_cents")),
            )
            db.commit()
            return {"job_id": job.id, "status": "succeeded"}
        except Exception as exc:
            db.rollback()
            failed_job = db.get(ReversePromptJob, job_id)
            if failed_job is not None:
                quota.release_reverse_prompt_video_quota(
                    db,
                    tenant_id=failed_job.tenant_id,
                    reverse_prompt_job_id=failed_job.id,
                )
                mark_reverse_prompt_job_failed(
                    db,
                    job=failed_job,
                    message="Reverse prompt video processing failed.",
                )
            logger.exception("reverse_prompt_video_failed", job_id=job_id)
            raise ReversePromptVideoProcessingError(
                "Reverse prompt video processing failed."
            ) from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
