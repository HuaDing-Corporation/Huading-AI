"""Celery task: generate a video from a topic via the distilled engine.

Flow: build EngineConfig from platform-injected settings (no hardcoded keys,
#002-FIX-1) -> run the standard pipeline -> publish progress to Redis -> upload
the finished mp4 to object storage under a task-isolated key -> return the URL.

Concurrency: the engine config is a process-wide singleton, so this worker must
run single-config / single-process (e.g. `celery ... worker -c 1`). See the
backend README. Multi-tenancy / per-task config is M2.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import VideoTask
from app.db.session import SessionLocal
from app.services.progress import build_progress_store
from app.services.storage.factory import create_object_storage
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# Celery task ids are UUIDs; validate before using one in a storage key/path.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Output prefix: one or more safe segments separated by '/', no traversal.
_PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
_UPLOAD_IMAGE_KEY_RE = re.compile(r"^uploads/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)$")
_TENANT_UPLOAD_OBJECT_KEY_RE = re.compile(
    r"^tenants/[A-Za-z0-9_-]+/uploads/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)$"
)


def _safe_task_id(task_id: str) -> str:
    if not task_id or not _TASK_ID_RE.match(task_id):
        raise ValueError(f"unsafe task id: {task_id!r}")
    return task_id


def _safe_prefix(prefix: str) -> str:
    # Guard the videos/{task_id}/final.mp4 contract even if the prefix is
    # misconfigured (e.g. '../', leading '/', backslashes) (#005-FIX P2).
    if not prefix or ".." in prefix or not _PREFIX_RE.match(prefix):
        raise ValueError(f"unsafe engine_output_prefix: {prefix!r}")
    return prefix


def _storage_key(task_id: str, tenant_id: str) -> str:
    """Task-isolated object-storage key. No user input flows in here (#002-RV P2)."""
    return (
        f"tenants/{_safe_task_id(tenant_id)}/"
        f"{_safe_prefix(settings.engine_output_prefix)}/{_safe_task_id(task_id)}/output.mp4"
    )


def _thumbnail_key(task_id: str, tenant_id: str) -> str:
    return (
        f"tenants/{_safe_task_id(tenant_id)}/"
        f"{_safe_prefix(settings.engine_output_prefix)}/{_safe_task_id(task_id)}/thumbnail.jpg"
    )


def _tenant_upload_storage_key(tenant_id: str, image_key: str) -> str:
    if not image_key or ".." in image_key or not _UPLOAD_IMAGE_KEY_RE.match(image_key):
        raise ValueError(f"unsafe image_key: {image_key!r}")
    storage_key = f"tenants/{_safe_task_id(tenant_id)}/{image_key}"
    if not _TENANT_UPLOAD_OBJECT_KEY_RE.match(storage_key):
        raise ValueError(f"unsafe image_key: {image_key!r}")
    return storage_key


def _update_video_task(
    task_id: str,
    tenant_id: str,
    *,
    status: str,
    progress: int | None = None,
    storage_bucket: str | None = None,
    storage_key: str | None = None,
    thumbnail_key: str | None = None,
    content_type: str | None = None,
    size_bytes: int | None = None,
    duration_sec: float | None = None,
    local_path: str | None = None,
    error: str | None = None,
) -> None:
    try:
        with SessionLocal() as db:
            task = db.get(VideoTask, task_id)
            if task is None or task.tenant_id != tenant_id:
                return
            task.status = status
            if progress is not None:
                task.progress = progress
            if storage_bucket is not None:
                task.storage_bucket = storage_bucket
            if storage_key is not None:
                task.storage_key = storage_key
            if thumbnail_key is not None:
                task.thumbnail_key = thumbnail_key
            if content_type is not None:
                task.content_type = content_type
            if size_bytes is not None:
                task.size_bytes = size_bytes
            if duration_sec is not None:
                task.duration_sec = duration_sec
            if local_path is not None:
                task.local_path = local_path
            if error is not None:
                task.error = error
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_task.db_update_failed", task_id=task_id, error=str(exc))


def _build_engine_config(params: dict[str, Any]):
    # Validate credentials before importing the (heavy) engine package so callers
    # / tests hitting the missing-config path stay cheap.
    if not (
        settings.engine_llm_api_key
        and settings.engine_llm_base_url
        and settings.engine_llm_model
    ):
        raise RuntimeError(
            "Engine LLM credentials are not configured "
            "(set ENGINE_LLM_API_KEY / ENGINE_LLM_BASE_URL / ENGINE_LLM_MODEL)."
        )

    from app.engine import EngineConfig

    return EngineConfig(
        llm_api_key=settings.engine_llm_api_key,
        llm_base_url=settings.engine_llm_base_url,
        llm_model=settings.engine_llm_model,
        dashscope_api_key=settings.engine_dashscope_api_key,
        seedance_api_key=settings.engine_seedance_api_key,
        seedance_base_url=settings.engine_seedance_base_url,
        seedance_model=settings.engine_seedance_model,
        seedance_request_timeout_seconds=settings.engine_seedance_request_timeout_seconds,
        seedance_poll_interval_seconds=settings.engine_seedance_poll_interval_seconds,
        seedance_timeout_seconds=settings.engine_seedance_timeout_seconds,
        default_template=params.get("frame_template") or settings.engine_default_template,
        browser_channel=settings.engine_browser_channel,
        runtime_root=settings.engine_runtime_root,
    )


async def _generate_with_engine(params: dict[str, Any], progress_cb) -> dict[str, Any]:
    from app.engine import create_engine

    cfg = _build_engine_config(params)
    engine = await create_engine(cfg)
    try:
        result = await engine.generate_video(
            text=params["topic"],
            pipeline=params.get("pipeline", "standard"),
            mode=params.get("mode", "generate"),
            n_scenes=params.get("n_scenes", 3),
            frame_template=params.get("frame_template") or cfg.default_template,
            tts_voice=params.get("voice"),
            tts_speed=params.get("tts_speed", 1.2),
            progress_callback=progress_cb,
        )
        return {
            "video_path": result.video_path,
            "duration": result.duration,
            "file_size": result.file_size,
        }
    finally:
        await engine.cleanup()


def _resolve_i2v_image(params: dict[str, Any]) -> str:
    """Fetch the uploaded product image from tenant-scoped storage to a temp file."""
    import tempfile

    image_key = params.get("image_key") or ""
    tenant_id = _safe_task_id(str(params["tenant_id"]))
    storage_key = _tenant_upload_storage_key(tenant_id, image_key)

    storage = create_object_storage(settings)
    content = storage.get_bytes(storage_key)
    suffix = Path(image_key).suffix or ".jpg"
    handle = tempfile.NamedTemporaryFile(prefix="i2v-", suffix=suffix, delete=False)
    with handle as fh:
        fh.write(content)
    return handle.name


async def _generate_with_seedance(params: dict[str, Any], progress_cb) -> dict[str, Any]:
    """seedance_t2v / seedance_i2v flows (LLM scenes + Seedance clips + TTS + compose)."""
    from app.engine import run_seedance_pipeline

    cfg = _build_engine_config(params)
    if not cfg.seedance_api_key:
        raise RuntimeError("Seedance is not configured (set ENGINE_SEEDANCE_API_KEY).")

    image_path: str | None = None
    if params.get("video_mode") == "seedance_i2v":
        image_path = _resolve_i2v_image(params)

    try:
        return await run_seedance_pipeline(
            cfg,
            params["topic"],
            image_path=image_path,
            n_scenes=params.get("n_scenes", 2),
            voice=params.get("voice"),
            tts_speed=params.get("tts_speed"),
            progress_callback=progress_cb,
        )
    finally:
        if image_path:
            Path(image_path).unlink(missing_ok=True)


@celery_app.task(bind=True, name="app.workers.tasks.generate_video")
def generate_video_task(self, params: dict[str, Any]) -> dict[str, Any]:
    task_id = self.request.id or params.get("video_task_id") or "eager"
    tenant_id = _safe_task_id(str(params["tenant_id"]))
    progress_task_id = f"{tenant_id}:{task_id}"
    store = build_progress_store(settings.redis_url)
    store.update(progress_task_id, status="STARTED", progress=0.0, stage="queued")
    _update_video_task(task_id, tenant_id, status="running", progress=0)

    def progress_cb(event: Any) -> None:
        # Progress reporting must never break generation.
        try:
            store.update(
                progress_task_id,
                status="PROGRESS",
                stage=getattr(event, "event_type", None),
                progress=float(getattr(event, "progress", 0.0) or 0.0),
                frame_current=getattr(event, "frame_current", None),
                frame_total=getattr(event, "frame_total", None),
            )
        except Exception:  # noqa: BLE001
            logger.warning("progress.update_failed", task_id=task_id)

    try:
        # static_template (default) runs the engine's HTML-frame pipeline;
        # seedance_t2v / seedance_i2v run the Seedance clip pipelines.
        video_mode = params.get("video_mode", "static_template")
        if video_mode in ("seedance_t2v", "seedance_i2v"):
            result = asyncio.run(_generate_with_seedance(params, progress_cb))
        else:
            result = asyncio.run(_generate_with_engine(params, progress_cb))
        video_bytes = Path(result["video_path"]).read_bytes()
        storage = create_object_storage(settings)
        storage_bucket = getattr(storage, "bucket", settings.engine_s3_bucket)
        storage_key = _storage_key(task_id, tenant_id)
        storage.put_bytes(storage_key, video_bytes, content_type="video/mp4")
        thumbnail_key = None
        thumbnail_path = result.get("thumbnail_path")
        if thumbnail_path:
            thumbnail_bytes = Path(thumbnail_path).read_bytes()
            thumbnail_key = _thumbnail_key(task_id, tenant_id)
            storage.put_bytes(thumbnail_key, thumbnail_bytes, content_type="image/jpeg")
        duration = result.get("duration")
        file_size = int(result.get("file_size") or len(video_bytes))
        _update_video_task(
            task_id,
            tenant_id,
            status="done",
            progress=100,
            storage_bucket=storage_bucket,
            storage_key=storage_key,
            thumbnail_key=thumbnail_key,
            content_type="video/mp4",
            size_bytes=file_size,
            duration_sec=float(duration) if duration is not None else None,
            local_path=str(result["video_path"]),
        )
        store.update(
            progress_task_id,
            status="SUCCESS",
            progress=1.0,
            stage="completed",
        )
        logger.info("video.generated", task_id=task_id, bucket=storage_bucket, size=file_size)
        return {
            "task_id": task_id,
            "status": "SUCCESS",
            "storage_bucket": storage_bucket,
            "storage_key": storage_key,
            "duration": duration,
            "file_size": file_size,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("video.generation_failed", task_id=task_id, error=str(exc))
        _update_video_task(task_id, tenant_id, status="failed", error=str(exc))
        store.update(progress_task_id, status="FAILURE", stage="failed", error=str(exc))
        raise
