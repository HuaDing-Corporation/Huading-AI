from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import scoped_task_id
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Asset, BgmLibraryTrack, TaskAsset, VideoTask
from app.db.session import SessionLocal
from app.providers.base import resolve
from app.services.batches import refresh_batch_job
from app.services.history import prune_video_history_best_effort
from app.services.progress import ProgressStore, build_progress_store
from app.services.quota import release_reserved_quota, settle_reserved_quota
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage
from app.services.synthetic_label import (
    label_artifact_bytes,
    synthetic_label_context,
)
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

_BGM_VOLUME = 0.22
_APIMART_VIDEO_MIN_PRESIGN_TTL_SECONDS = 7200


@dataclass
class VideoGenContext:
    task_id: str
    tenant_id: str
    db: Session
    store: ProgressStore
    storage: ObjectStorage
    task: VideoTask
    reference_assets: list[Asset]
    duration_sec: int
    resolution: str
    bgm: dict[str, Any] | None = None


def get_object_storage() -> ObjectStorage:
    return create_object_storage(settings)


def _task_or_raise(db: Session, *, tenant_id: str, task_id: str) -> VideoTask:
    task = db.get(VideoTask, task_id)
    if task is None or task.tenant_id != tenant_id or task.video_mode != "video_gen":
        raise RuntimeError("Video generation task not found.")
    return task


def _update_progress(ctx: VideoGenContext, *, progress: int, step: str) -> None:
    try:
        ctx.store.update(
            scoped_task_id(ctx.tenant_id, ctx.task_id),
            status="running",
            progress=progress,
            step=step,
        )
    except Exception as exc:  # pragma: no cover - progress is best effort
        logger.warning("video_gen.progress_update_failed", error=str(exc))


def _store_progress(store: ProgressStore, task_id: str, **fields: Any) -> None:
    try:
        store.update(task_id, **fields)
    except Exception as exc:  # pragma: no cover - progress is best effort
        logger.warning("video_gen.progress_update_failed", error=str(exc))


def _task_reference_assets(db: Session, task: VideoTask) -> list[Asset]:
    ids = list((task.params or {}).get("reference_image_asset_ids") or [])
    links = list(
        db.scalars(
            select(TaskAsset).where(
                TaskAsset.video_task_id == task.id,
                TaskAsset.role == "input_reference_image",
            )
        )
    )
    linked_ids = [item.asset_id for item in links]
    ordered_ids = ids or linked_ids
    assets = list(db.scalars(select(Asset).where(Asset.id.in_(ordered_ids))))
    by_id = {asset.id: asset for asset in assets}
    return [by_id[asset_id] for asset_id in ordered_ids if asset_id in by_id]


def _load_context(
    *,
    db: Session,
    tenant_id: str,
    task_id: str,
    storage: ObjectStorage,
    store: ProgressStore,
) -> VideoGenContext:
    task = _task_or_raise(db, tenant_id=tenant_id, task_id=task_id)
    params = task.params or {}
    return VideoGenContext(
        task_id=task_id,
        tenant_id=tenant_id,
        db=db,
        store=store,
        storage=storage,
        task=task,
        reference_assets=_task_reference_assets(db, task),
        duration_sec=int(params.get("duration_sec") or task.duration_sec or 5),
        resolution=str(params.get("resolution") or "720p"),
        bgm=params.get("bgm") if isinstance(params.get("bgm"), dict) else None,
    )


def _provider_payload(ctx: VideoGenContext) -> dict[str, Any]:
    image_urls = []
    presign_ttl = max(
        int(settings.engine_s3_presign_ttl),
        math.ceil(float(settings.engine_apimart_video_timeout_seconds)),
        _APIMART_VIDEO_MIN_PRESIGN_TTL_SECONDS,
    )
    for asset in ctx.reference_assets:
        image_urls.append(
            ctx.storage.presign_get_url(asset.storage_key, expires_in=presign_ttl)
        )
    payload: dict[str, Any] = {
        "model": settings.engine_apimart_video_model,
        "prompt": ctx.task.topic or "",
        "duration": ctx.duration_sec,
        "resolution": ctx.resolution,
        "size": "adaptive" if image_urls else "9:16",
        "generate_audio": False,
    }
    if image_urls:
        payload["image_urls"] = image_urls
    return payload


def _generate_seedance_mini_video(ctx: VideoGenContext) -> bytes:
    provider = resolve(ctx.db, tenant_id=ctx.tenant_id, capability="video")
    result = asyncio.run(provider.generate_video(_provider_payload(ctx)))
    video_bytes = result.get("video_bytes") if isinstance(result, dict) else None
    if not isinstance(video_bytes, bytes) or not video_bytes:
        raise RuntimeError("Video provider returned no video bytes.")
    return video_bytes


def _suffix_for_key(key: str, fallback: str) -> str:
    suffix = Path(key).suffix.lower()
    return suffix if suffix else fallback


def _bgm_bytes(ctx: VideoGenContext) -> tuple[bytes, str] | None:
    if not ctx.bgm:
        return None
    if ctx.bgm.get("source") == "upload":
        asset = ctx.db.get(Asset, str(ctx.bgm.get("asset_id") or ""))
        if asset is None or asset.tenant_id != ctx.tenant_id:
            raise RuntimeError("BGM upload asset not found.")
        return ctx.storage.get_bytes(asset.storage_key), _suffix_for_key(asset.storage_key, ".mp3")
    if ctx.bgm.get("source") == "library":
        track = ctx.db.get(BgmLibraryTrack, str(ctx.bgm.get("track_id") or ""))
        if track is None or not track.is_active:
            raise RuntimeError("BGM library track not found.")
        return ctx.storage.get_bytes(track.storage_key), _suffix_for_key(track.storage_key, ".mp3")
    return None


def _ffmpeg_binary() -> str:
    return os.environ.get("FFMPEG_BINARY", "ffmpeg")


def _ffprobe_binary() -> str:
    return os.environ.get("FFPROBE_BINARY", "ffprobe")


def _run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _probe_duration_sec(path: Path) -> float:
    result = _run_subprocess(
        [
            _ffprobe_binary(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    duration = float(json.loads(result.stdout)["format"]["duration"])
    return max(0.1, duration)


def _mix_bgm_bytes(video_bytes: bytes, bgm_bytes: bytes, audio_suffix: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="huading-video-gen-") as temp_dir:
        temp_path = Path(temp_dir)
        video_path = temp_path / "video.mp4"
        audio_path = temp_path / f"bgm{audio_suffix}"
        output_path = temp_path / "mixed.mp4"
        video_path.write_bytes(video_bytes)
        audio_path.write_bytes(bgm_bytes)
        duration_sec = _probe_duration_sec(video_path)
        audio_filter = (
            f"[1:a]volume={_BGM_VOLUME},"
            f"atrim=duration={duration_sec:.3f},asetpts=PTS-STARTPTS[a]"
        )
        _run_subprocess(
            [
                _ffmpeg_binary(),
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-filter_complex",
                audio_filter,
                "-map",
                "0:v:0",
                "-map",
                "[a]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        return output_path.read_bytes()


def _apply_synthetic_video_label(ctx: VideoGenContext, video_bytes: bytes) -> bytes:
    visible_label = bool((ctx.task.params or {}).get("apply_visible_label", False))
    label_settings, meta, _payload = synthetic_label_context(
        ctx.db,
        tenant_id=ctx.tenant_id,
        content_id=ctx.task_id,
        visible=visible_label,
    )
    return label_artifact_bytes(
        video_bytes,
        kind="video",
        settings=label_settings,
        meta=meta,
        suffix=".mp4",
        visible=visible_label,
    )


def _store_output(ctx: VideoGenContext, video_bytes: bytes) -> str:
    key = f"tenants/{ctx.tenant_id}/videos/{ctx.task_id}/final.mp4"
    ctx.storage.put_bytes(key, video_bytes, content_type="video/mp4")
    output_asset = Asset(
        tenant_id=ctx.tenant_id,
        type="video",
        source="generated",
        provider="apimart",
        storage_key=key,
        mime_type="video/mp4",
        size_bytes=len(video_bytes),
        duration_ms=int(ctx.duration_sec * 1000),
        status="ready",
        metadata_={
            "video_mode": "video_gen",
            "model": settings.engine_apimart_video_model,
        },
    )
    ctx.db.add(output_asset)
    ctx.db.flush()
    ctx.db.add(
        TaskAsset(video_task_id=ctx.task_id, asset_id=output_asset.id, role="output_video")
    )
    return key


def _mark_video_gen_failed(
    db: Session,
    *,
    tenant_id: str,
    task_id: str,
    error_message: str,
) -> int:
    task = db.get(VideoTask, task_id)
    if task is None or task.tenant_id != tenant_id or task.video_mode != "video_gen":
        return 100
    task.status = "failed"
    task.error_code = "VIDEO_GEN_FAILED"
    task.error_message = error_message
    task.error = error_message
    task.finished_at = datetime.now(UTC)
    return int(task.progress or 100)


def run_video_gen_pipeline(*, tenant_id: str, task_id: str) -> dict[str, Any]:
    storage: ObjectStorage | None = None
    store: ProgressStore | None = None
    with SessionLocal() as db:
        try:
            storage = get_object_storage()
            store = build_progress_store(settings.redis_url)
            ctx = _load_context(
                db=db,
                tenant_id=tenant_id,
                task_id=task_id,
                storage=storage,
                store=store,
            )
            task = ctx.task
            if task.status == "cancelled":
                _store_progress(
                    store,
                    scoped_task_id(tenant_id, task_id),
                    status="cancelled",
                    progress=task.progress or 0,
                    step="cancelled",
                )
                return {"task_id": task_id, "status": "cancelled"}
            task.status = "running"
            task.started_at = datetime.now(UTC)
            task.progress = 5
            db.commit()

            _update_progress(ctx, progress=15, step="seedance")
            video_bytes = _generate_seedance_mini_video(ctx)
            bgm = _bgm_bytes(ctx)
            if bgm is not None:
                _update_progress(ctx, progress=70, step="mix_bgm")
                video_bytes = _mix_bgm_bytes(video_bytes, bgm[0], bgm[1])
            _update_progress(ctx, progress=86, step="label")
            labeled_bytes = _apply_synthetic_video_label(ctx, video_bytes)
            storage_key = _store_output(ctx, labeled_bytes)
            task.status = "done"
            task.progress = 100
            task.storage_bucket = storage.bucket
            task.storage_key = storage_key
            task.content_type = "video/mp4"
            task.size_bytes = len(labeled_bytes)
            task.duration_sec = float(ctx.duration_sec)
            task.finished_at = datetime.now(UTC)
            settle_reserved_quota(
                db,
                tenant_id=tenant_id,
                video_task_id=task_id,
                actual_seconds=ctx.duration_sec,
                cost_cents=0,
            )
            refresh_batch_job(db, batch_id=task.batch_id)
            db.commit()
            _store_progress(
                store,
                scoped_task_id(tenant_id, task_id),
                status="done",
                progress=100,
                step="upload",
                playback_url=storage.presign_get_url(
                    storage_key,
                    expires_in=settings.engine_s3_presign_ttl,
                ),
                download_url=storage.presign_get_url(
                    storage_key,
                    expires_in=settings.engine_s3_presign_ttl,
                    download_filename=f"{task_id}.mp4",
                ),
            )
            prune_video_history_best_effort(
                db,
                tenant_id=tenant_id,
                mode="video_gen",
                storage=storage,
            )
            return {
                "task_id": task_id,
                "status": "done",
                "storage_key": storage_key,
                "file_size": len(labeled_bytes),
            }
        except Exception as exc:
            logger.exception("video_gen.failed", task_id=task_id, tenant_id=tenant_id)
            db.rollback()
            error_message = str(exc)
            failed_task = db.get(VideoTask, task_id)
            batch_id = (
                failed_task.batch_id
                if failed_task is not None and failed_task.tenant_id == tenant_id
                else None
            )
            failed_progress = _mark_video_gen_failed(
                db,
                tenant_id=tenant_id,
                task_id=task_id,
                error_message=error_message,
            )
            release_reserved_quota(db, tenant_id=tenant_id, video_task_id=task_id)
            refresh_batch_job(db, batch_id=batch_id)
            db.commit()
            if storage is not None:
                prune_video_history_best_effort(
                    db,
                    tenant_id=tenant_id,
                    mode="video_gen",
                    storage=storage,
                )
            if store is not None:
                _store_progress(
                    store,
                    scoped_task_id(tenant_id, task_id),
                    status="failed",
                    progress=failed_progress,
                    step="failed",
                    error_code="VIDEO_GEN_FAILED",
                    error_message=error_message,
                )
            raise


@celery_app.task(bind=True, name="app.workers.video_gen.generate")
def generate_video_gen_task(self, params: dict[str, Any]) -> dict[str, Any]:
    tenant_id = str(params["tenant_id"])
    task_id = str(params["video_task_id"])
    logger.info(
        "video_gen.queued",
        task_id=task_id,
        tenant_id=tenant_id,
        visible_label=bool(params.get("apply_visible_label", False)),
    )
    return run_video_gen_pipeline(tenant_id=tenant_id, task_id=task_id)
