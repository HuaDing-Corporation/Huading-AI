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
from app.services.progress import build_progress_store
from app.services.storage.factory import create_object_storage
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# Celery task ids are UUIDs; validate before using one in a storage key/path.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Output prefix: one or more safe segments separated by '/', no traversal.
_PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")


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
        f"{_safe_prefix(settings.engine_output_prefix)}/"
        f"{_safe_task_id(tenant_id)}/{_safe_task_id(task_id)}/final.mp4"
    )


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


@celery_app.task(bind=True, name="app.workers.tasks.generate_video")
def generate_video_task(self, params: dict[str, Any]) -> dict[str, Any]:
    task_id = self.request.id or "eager"
    tenant_id = _safe_task_id(str(params["tenant_id"]))
    progress_task_id = f"{tenant_id}:{task_id}"
    store = build_progress_store(settings.redis_url)
    store.update(progress_task_id, status="STARTED", progress=0.0, stage="queued")

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
        result = asyncio.run(_generate_with_engine(params, progress_cb))
        video_bytes = Path(result["video_path"]).read_bytes()
        storage = create_object_storage(settings)
        url = storage.put_bytes(
            _storage_key(task_id, tenant_id), video_bytes, content_type="video/mp4"
        )
        store.update(
            progress_task_id,
            status="SUCCESS",
            progress=1.0,
            stage="completed",
            video_url=url,
        )
        logger.info("video.generated", task_id=task_id, url=url, size=result["file_size"])
        return {
            "task_id": task_id,
            "status": "SUCCESS",
            "video_url": url,
            "duration": result["duration"],
            "file_size": result["file_size"],
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("video.generation_failed", task_id=task_id, error=str(exc))
        store.update(progress_task_id, status="FAILURE", stage="failed", error=str(exc))
        raise
