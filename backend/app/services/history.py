from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import JSON, String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Asset, TaskAsset, UsageRecord, VideoTask
from app.db.session import Base
from app.services.storage.base import ObjectStorage

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


def _delete_media_best_effort(storage: ObjectStorage, storage_keys: Iterable[str]) -> None:
    delete_object = getattr(storage, "delete_object", None)
    if delete_object is None:
        logger.warning("history.storage_delete_unavailable")
        return

    for storage_key in _unique(storage_keys):
        try:
            delete_object(storage_key)
        except Exception as exc:  # pragma: no cover - log-only best effort
            logger.warning(
                "history.storage_delete_failed",
                storage_key=storage_key,
                error=str(exc),
            )


@dataclass(frozen=True)
class _ReferenceScope:
    task_ids: frozenset[str] = frozenset()
    replicate_job_ids: frozenset[str] = frozenset()
    replicate_output_ids: frozenset[str] = frozenset()
    asset_ids: frozenset[str] = frozenset()

    def outside(self, statement, table):
        if table.name == "task_assets" and self.task_ids:
            return statement.where(~table.c.video_task_id.in_(self.task_ids))
        excluded_ids = {
            "assets": self.asset_ids,
            "ecom_replicate_jobs": self.replicate_job_ids,
            "ecom_replicate_outputs": self.replicate_output_ids,
            "video_tasks": self.task_ids,
        }.get(table.name)
        if excluded_ids:
            return statement.where(~table.c.id.in_(excluded_ids))
        return statement


def _references_in_json(value: object, candidates: set[str]) -> set[str]:
    if isinstance(value, str):
        return {value} & candidates
    if isinstance(value, Mapping):
        references: set[str] = set()
        for key, item in value.items():
            references.update(_references_in_json(key, candidates))
            references.update(_references_in_json(item, candidates))
        return references
    if isinstance(value, (list, tuple, set)):
        references: set[str] = set()
        for item in value:
            references.update(_references_in_json(item, candidates))
        return references
    return set()


def _column_references(db: Session, *, candidates: set[str], scope: _ReferenceScope, match):
    references: set[str] = set()
    for table in Base.metadata.sorted_tables:
        columns = [column for column in table.columns if match(table, column)]
        if not columns:
            continue
        statement = select(*columns).where(
            or_(*(column.in_(candidates) for column in columns))
        )
        for row in db.execute(scope.outside(statement, table)):
            references.update(str(value) for value in row if value in candidates)
    return references


def _json_references(
    db: Session,
    *,
    candidates: set[str],
    scope: _ReferenceScope,
) -> set[str]:
    references: set[str] = set()
    for table in Base.metadata.sorted_tables:
        columns = [column for column in table.columns if isinstance(column.type, JSON)]
        if not columns:
            continue
        possible_matches = [
            cast(column, String).contains(candidate, autoescape=True)
            for column in columns
            for candidate in candidates
        ]
        statement = select(*columns).where(or_(*possible_matches))
        for row in db.execute(scope.outside(statement, table)):
            for value in row:
                references.update(_references_in_json(value, candidates))
    return references


def referenced_asset_ids(
    db: Session,
    *,
    asset_ids: Iterable[str],
    deleting_task_ids: Iterable[str] = (),
    deleting_replicate_job_ids: Iterable[str] = (),
    deleting_replicate_output_ids: Iterable[str] = (),
) -> set[str]:
    candidates = set(asset_ids)
    if not candidates:
        return set()
    scope = _ReferenceScope(
        task_ids=frozenset(deleting_task_ids),
        replicate_job_ids=frozenset(deleting_replicate_job_ids),
        replicate_output_ids=frozenset(deleting_replicate_output_ids),
        asset_ids=frozenset(candidates),
    )
    references = _column_references(
        db,
        candidates=candidates,
        scope=scope,
        match=lambda _table, column: any(
            fk.target_fullname == "assets.id" for fk in column.foreign_keys
        ),
    )
    references.update(_json_references(db, candidates=candidates, scope=scope))
    return references


def referenced_storage_keys(
    db: Session,
    *,
    storage_keys: Iterable[str],
    deleting_task_ids: Iterable[str] = (),
    deleting_replicate_job_ids: Iterable[str] = (),
    deleting_replicate_output_ids: Iterable[str] = (),
    deleting_asset_ids: Iterable[str] = (),
) -> set[str]:
    candidates = set(storage_keys)
    if not candidates:
        return set()
    scope = _ReferenceScope(
        task_ids=frozenset(deleting_task_ids),
        replicate_job_ids=frozenset(deleting_replicate_job_ids),
        replicate_output_ids=frozenset(deleting_replicate_output_ids),
        asset_ids=frozenset(deleting_asset_ids),
    )
    additional_key_columns = {
        ("templates", "path"),
        ("video_tasks", "thumbnail_key"),
        ("voices", "sample_url"),
    }
    references = _column_references(
        db,
        candidates=candidates,
        scope=scope,
        match=lambda table, column: column.name.endswith("storage_key")
        or (table.name, column.name) in additional_key_columns,
    )
    references.update(_json_references(db, candidates=candidates, scope=scope))
    return references


def stage_video_task_deletions(
    db: Session,
    *,
    tasks: list[VideoTask],
) -> tuple[int, list[str]]:
    tasks = [task for task in tasks if is_terminal_history_task(task)]
    if not tasks:
        return 0, []

    task_ids = {task.id for task in tasks}
    candidate_storage_keys: list[str | None] = []
    for task in tasks:
        candidate_storage_keys.extend([task.storage_key, task.thumbnail_key])

    links = list(
        db.scalars(select(TaskAsset).where(TaskAsset.video_task_id.in_(task_ids)))
    )
    candidate_assets: dict[str, Asset] = {}
    for link in links:
        asset = db.get(Asset, link.asset_id)
        if asset is None:
            continue
        if link.role not in _OUTPUT_ASSET_ROLES or asset.source != "generated":
            continue
        candidate_assets[asset.id] = asset

    referenced_assets = referenced_asset_ids(
        db,
        asset_ids=candidate_assets,
        deleting_task_ids=task_ids,
    )
    assets_to_delete = {
        asset_id: asset
        for asset_id, asset in candidate_assets.items()
        if asset_id not in referenced_assets
    }
    candidate_storage_keys.extend(asset.storage_key for asset in assets_to_delete.values())
    referenced_keys = referenced_storage_keys(
        db,
        storage_keys=_unique(candidate_storage_keys),
        deleting_task_ids=task_ids,
        deleting_asset_ids=assets_to_delete,
    )
    storage_keys = [
        storage_key
        for storage_key in _unique(candidate_storage_keys)
        if storage_key not in referenced_keys
    ]

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
    return len(tasks), storage_keys


def _hard_delete_tasks(
    db: Session,
    *,
    tasks: list[VideoTask],
    storage: ObjectStorage,
) -> int:
    try:
        deleted, storage_keys = stage_video_task_deletions(db, tasks=tasks)
        if not deleted:
            return 0
        db.commit()
    except Exception:
        db.rollback()
        raise
    _delete_media_best_effort(storage, storage_keys)
    return deleted


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
    _hard_delete_tasks(db, tasks=[task], storage=storage)
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
    return _hard_delete_tasks(db, tasks=tasks, storage=storage)


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
    return _hard_delete_tasks(db, tasks=tasks[keep:], storage=storage)


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
