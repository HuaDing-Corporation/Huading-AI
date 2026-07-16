from collections.abc import Iterable
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.models import Asset, TaskAsset, UsageRecord, VideoTask
from app.services.storage.base import ObjectStorage, StorageKeyError
from app.services.storage.keys import (
    delete_tenant_storage_key,
    validate_tenant_storage_keys,
)

logger = get_logger(__name__)

HISTORY_KEEP_LIMIT = 20
TERMINAL_HISTORY_STATUSES = {"done", "succeeded", "failed"}
_OUTPUT_ASSET_ROLES = {
    "output_audio",
    "output_subtitle",
    "output_video",
    "output_image",
}
DeleteVideoResult = Literal["deleted", "missing", "skipped"]


def history_kind(task: VideoTask) -> str | None:
    params = task.params or {}
    if not isinstance(params, dict):
        return None
    value = params.get("kind") or params.get("purpose")
    if not isinstance(value, str) or not value:
        return None
    return value


def video_mode_filter(mode: str):
    return or_(VideoTask.mode == mode, VideoTask.video_mode == mode)


def terminal_history_filter():
    return func.lower(VideoTask.status).in_(TERMINAL_HISTORY_STATUSES)


def is_terminal_history_task(task: VideoTask) -> bool:
    return str(task.status or "").lower() in TERMINAL_HISTORY_STATUSES


def _unique(items: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        unique_items.append(item)
    return unique_items


def _delete_media_best_effort(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    storage_keys: Iterable[str],
) -> None:
    for storage_key in _unique(storage_keys):
        try:
            delete_tenant_storage_key(
                storage,
                tenant_id=tenant_id,
                storage_key=storage_key,
            )
        except Exception as exc:  # pragma: no cover - log-only best effort
            logger.warning(
                "history.storage_delete_failed",
                storage_key=storage_key,
                error=str(exc),
            )


def _asset_has_other_task_links(db: Session, *, asset_id: str, task_ids: set[str]) -> bool:
    other_links = (
        db.scalar(
            select(func.count())
            .select_from(TaskAsset)
            .where(
                TaskAsset.asset_id == asset_id,
                ~TaskAsset.video_task_id.in_(task_ids),
            )
        )
        or 0
    )
    return int(other_links) > 0


def _hard_delete_tasks(
    db: Session,
    *,
    tenant_id: str,
    tasks: list[VideoTask],
    storage: ObjectStorage,
) -> int:
    tasks = [task for task in tasks if is_terminal_history_task(task)]
    if not tasks:
        return 0

    task_ids = {task.id for task in tasks}
    storage_keys: list[str | None] = []
    for task in tasks:
        storage_keys.extend([task.storage_key, task.thumbnail_key])

    links = list(
        db.scalars(select(TaskAsset).where(TaskAsset.video_task_id.in_(task_ids)))
    )
    assets_to_delete: dict[str, Asset] = {}
    for link in links:
        asset = db.get(Asset, link.asset_id)
        if asset is None:
            continue
        if link.role not in _OUTPUT_ASSET_ROLES or asset.source != "generated":
            continue
        if _asset_has_other_task_links(db, asset_id=asset.id, task_ids=task_ids):
            continue
        assets_to_delete[asset.id] = asset
        storage_keys.append(asset.storage_key)

    try:
        safe_storage_keys = validate_tenant_storage_keys(
            tenant_id,
            _unique(storage_keys),
        )
    except StorageKeyError as exc:
        raise AppError(
            "Video task not found.",
            code="VIDEO_TASK_NOT_FOUND",
            status_code=404,
        ) from exc

    for usage in db.scalars(
        select(UsageRecord).where(UsageRecord.video_task_id.in_(task_ids))
    ):
        usage.video_task_id = None
    for link in links:
        db.delete(link)
    db.flush()
    for asset in assets_to_delete.values():
        db.delete(asset)
    for task in tasks:
        db.delete(task)
    db.commit()

    _delete_media_best_effort(
        storage,
        tenant_id=tenant_id,
        storage_keys=safe_storage_keys,
    )
    return len(tasks)


def delete_video_task(
    db: Session,
    *,
    tenant_id: str,
    task_id: str,
    storage: ObjectStorage,
) -> DeleteVideoResult:
    task = db.scalar(
        select(VideoTask).where(
            VideoTask.id == task_id,
            VideoTask.tenant_id == tenant_id,
        )
    )
    if task is None:
        return "missing"
    if not is_terminal_history_task(task):
        return "skipped"
    _hard_delete_tasks(db, tenant_id=tenant_id, tasks=[task], storage=storage)
    return "deleted"


def clear_video_history(
    db: Session,
    *,
    tenant_id: str,
    mode: str,
    storage: ObjectStorage,
) -> int:
    tasks = list(
        db.scalars(
            select(VideoTask).where(
                VideoTask.tenant_id == tenant_id,
                video_mode_filter(mode),
                terminal_history_filter(),
            )
        )
    )
    return _hard_delete_tasks(db, tenant_id=tenant_id, tasks=tasks, storage=storage)


def prune_video_history(
    db: Session,
    *,
    tenant_id: str,
    mode: str,
    storage: ObjectStorage,
    keep: int = HISTORY_KEEP_LIMIT,
) -> int:
    tasks = list(
        db.scalars(
            select(VideoTask)
            .where(
                VideoTask.tenant_id == tenant_id,
                video_mode_filter(mode),
                terminal_history_filter(),
            )
            .order_by(VideoTask.created_at.desc(), VideoTask.id.desc())
        )
    )
    return _hard_delete_tasks(
        db,
        tenant_id=tenant_id,
        tasks=tasks[keep:],
        storage=storage,
    )


def prune_video_history_best_effort(
    db: Session,
    *,
    tenant_id: str,
    mode: str,
    storage: ObjectStorage,
    keep: int = HISTORY_KEEP_LIMIT,
) -> int:
    try:
        return prune_video_history(
            db,
            tenant_id=tenant_id,
            mode=mode,
            storage=storage,
            keep=keep,
        )
    except Exception as exc:  # pragma: no cover - non-blocking cleanup guard
        logger.warning(
            "video_history_prune_failed",
            tenant_id=tenant_id,
            mode=mode,
            error=str(exc),
        )
        return 0
