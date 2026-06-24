from __future__ import annotations

import asyncio
import base64
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.api.deps import scoped_task_id
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Asset, TaskAsset, VideoTask
from app.db.session import SessionLocal
from app.providers.base import invoke, resolve
from app.services.progress import build_progress_store
from app.services.quota import release_reserved_quota, settle_reserved_quota
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage
from app.workers.celery_app import celery_app
from app.workers.video_tasks import _safe_task_id, _tenant_upload_storage_key

logger = get_logger(__name__)

_ERROR_CODE = "IMAGE_GEN_FAILED"


def _photo_storage_key(tenant_id: str, task_id: str) -> str:
    return f"tenants/{_safe_task_id(tenant_id)}/photos/{_safe_task_id(task_id)}/output.png"


def _write_temp_input_image(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    image_key: str,
    temp_paths: list[Path],
) -> Path:
    storage_key = _tenant_upload_storage_key(tenant_id, image_key)
    suffix = Path(image_key).suffix or ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(storage.get_bytes(storage_key))
        path = Path(handle.name)
    temp_paths.append(path)
    return path


def _image_bytes(result: Mapping[str, Any]) -> bytes:
    raw = result.get("image_bytes")
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return base64.b64decode(raw)
    b64_json = result.get("b64_json")
    if isinstance(b64_json, str):
        return base64.b64decode(b64_json)
    raise ValueError("Image provider returned no image bytes.")


def _update_progress(db, store, *, tenant_id: str, task: VideoTask, stage: str, progress: int):
    task.status = "done" if stage == "done" else "running"
    task.progress = progress
    db.commit()
    store.update(
        scoped_task_id(tenant_id, task.id),
        status=task.status,
        stage=stage,
        progress=progress,
    )


def _failure_message(exc: Exception) -> str:
    cause = exc.__cause__
    if cause is not None and str(cause):
        return str(cause)
    return str(exc) or exc.__class__.__name__


def _mark_failed(*, tenant_id: str, task_id: str, error_message: str, store) -> None:
    with SessionLocal() as db:
        task = db.get(VideoTask, task_id)
        if task is None or task.tenant_id != tenant_id:
            return
        task.status = "failed"
        task.progress = max(int(task.progress or 0), 1)
        task.error = error_message
        task.error_code = _ERROR_CODE
        task.error_message = error_message
        task.finished_at = datetime.now(UTC)
        release_reserved_quota(db, tenant_id=tenant_id, video_task_id=task_id)
        db.commit()
    store.update(
        scoped_task_id(tenant_id, task_id),
        status="failed",
        stage="failed",
        error=error_message,
        error_code=_ERROR_CODE,
        error_message=error_message,
    )


def run_image_generation(params: dict[str, Any]) -> dict[str, Any]:
    tenant_id = str(params["tenant_id"])
    task_id = _safe_task_id(str(params["video_task_id"]))

    started = time.monotonic()
    store = build_progress_store(settings.redis_url)
    storage = create_object_storage(settings)
    temp_paths: list[Path] = []

    try:
        prompt = str(params.get("script") or params.get("topic") or "").strip()
        if not prompt:
            raise ValueError("Image prompt is required.")

        with SessionLocal() as db:
            task = db.get(VideoTask, task_id)
            if task is None or task.tenant_id != tenant_id:
                raise ValueError("Video task not found for image generation.")

            task.started_at = datetime.now(UTC)
            _update_progress(
                db,
                store,
                tenant_id=tenant_id,
                task=task,
                stage="running",
                progress=10,
            )

            input_path = None
            if params.get("image_key"):
                input_path = _write_temp_input_image(
                    storage,
                    tenant_id=tenant_id,
                    image_key=str(params["image_key"]),
                    temp_paths=temp_paths,
                )

            _update_progress(
                db,
                store,
                tenant_id=tenant_id,
                task=task,
                stage="generating",
                progress=30,
            )
            provider = resolve(db, tenant_id=tenant_id, capability="image")
            provider_payload: dict[str, Any] = {
                "prompt": prompt,
                "size": params.get("image_size") or "1024x1024",
                "quality": params.get("image_quality") or "medium",
                "n": 1,
            }
            if input_path is not None:
                provider_payload["input_image_path"] = str(input_path)
            result = asyncio.run(
                invoke(
                    db,
                    tenant_id=tenant_id,
                    capability="image",
                    provider=provider.__class__.__name__,
                    operation=lambda: provider.generate_image(provider_payload),
                    timeout_seconds=settings.openai_image_timeout,
                )
            )

            image_bytes = _image_bytes(result)
            _update_progress(db, store, tenant_id=tenant_id, task=task, stage="got", progress=80)

            output_key = _photo_storage_key(tenant_id, task_id)
            _update_progress(
                db,
                store,
                tenant_id=tenant_id,
                task=task,
                stage="uploading",
                progress=90,
            )
            storage.put_bytes(output_key, image_bytes, content_type="image/png")

            asset = Asset(
                tenant_id=tenant_id,
                type="generated_image",
                source="generated",
                provider="openai",
                storage_key=output_key,
                mime_type="image/png",
                size_bytes=len(image_bytes),
                status="ready",
                metadata_={
                    "model": result.get("model") or settings.openai_image_model,
                    "size": provider_payload["size"],
                    "quality": provider_payload["quality"],
                    "mode": result.get("mode") or ("edit" if input_path else "generate"),
                },
            )
            db.add(asset)
            db.flush()
            db.add(TaskAsset(video_task_id=task_id, asset_id=asset.id, role="output_image"))

            task.status = "done"
            task.progress = 100
            task.storage_bucket = storage.bucket
            task.storage_key = output_key
            task.thumbnail_key = output_key
            task.content_type = "image/png"
            task.size_bytes = len(image_bytes)
            task.duration_sec = round(time.monotonic() - started, 3)
            task.finished_at = datetime.now(UTC)
            task.error = None
            task.error_code = None
            task.error_message = None
            settle_reserved_quota(
                db,
                tenant_id=tenant_id,
                video_task_id=task_id,
                actual_seconds=1,
                cost_cents=0,
            )
            db.commit()

            playback_url = storage.presign_get_url(
                output_key,
                expires_in=settings.engine_s3_presign_ttl,
            )
            download_url = storage.presign_get_url(
                output_key,
                expires_in=settings.engine_s3_presign_ttl,
                download_filename=f"{task_id}.png",
            )
            store.update(
                scoped_task_id(tenant_id, task_id),
                status="done",
                stage="done",
                progress=100,
                playback_url=playback_url,
                download_url=download_url,
                thumbnail_url=playback_url,
            )
            return {
                "status": "SUCCESS",
                "storage_key": output_key,
                "playback_url": playback_url,
                "download_url": download_url,
            }
    except Exception as exc:
        error_message = _failure_message(exc)
        logger.warning(
            "image_generation_failed",
            tenant_id=tenant_id,
            task_id=task_id,
            error=error_message,
        )
        _mark_failed(tenant_id=tenant_id, task_id=task_id, error_message=error_message, store=store)
        return {
            "status": "FAILURE",
            "error_code": _ERROR_CODE,
            "error_message": error_message,
        }
    finally:
        for path in temp_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("image_generation_temp_cleanup_failed", path=str(path))


@celery_app.task(bind=True, name="app.workers.image_gen.generate")
def generate_image_task(self, params: dict[str, Any]) -> dict[str, Any]:
    payload = dict(params)
    payload.setdefault("video_task_id", self.request.id)
    return run_image_generation(payload)
