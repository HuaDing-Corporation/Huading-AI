from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import func, literal, or_, select, union_all, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import Asset, EcomReplicateJob, EcomReplicateOutput, TaskAsset, VideoTask
from app.schemas.history import (
    ImageHistoryCategory,
    ImageHistoryClearResponse,
    ImageHistoryDeletedResponse,
    ImageHistoryDetailItem,
    ImageHistoryDetailResponse,
    ImageHistoryItem,
    ImageHistoryListResponse,
)
from app.services import ecom_replicate
from app.services.history import video_mode_filter
from app.services.storage.base import ObjectStorage, StorageKeyError
from app.services.storage.keys import (
    is_tenant_storage_key,
    presign_tenant_storage_key,
    validate_tenant_storage_key,
)

PhotoHistoryCategory = Literal["image_gen", "ecom_white", "ecom_model", "cover"]

_PHOTO_KIND_BY_CATEGORY: dict[PhotoHistoryCategory, str | None] = {
    "image_gen": None,
    "ecom_white": "ecom_cutout",
    "ecom_model": "ecom_model",
    "cover": "cover",
}
_REPLICATE_HISTORY_STATUSES = {"completed", "partial_failed", "failed"}


def _tenant_storage_pattern(tenant_id: str) -> str:
    return f"tenants/{tenant_id}/%"


def _is_tenant_storage_key(tenant_id: str, storage_key: str) -> bool:
    return is_tenant_storage_key(tenant_id, storage_key)


def _validated_tenant_storage_key(tenant_id: str, storage_key: str | None) -> str:
    try:
        return validate_tenant_storage_key(tenant_id, storage_key)
    except StorageKeyError as exc:
        raise AppError(
            "Image history item not found.",
            code="IMAGE_HISTORY_NOT_FOUND",
            status_code=404,
        ) from exc


def _photo_cover_storage_key(tenant_id: str, task: VideoTask) -> str:
    thumbnail_key = str(task.thumbnail_key or "")
    if _is_tenant_storage_key(tenant_id, thumbnail_key):
        return thumbnail_key
    return _validated_tenant_storage_key(tenant_id, task.storage_key)


def _presign_tenant_storage_key(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    storage_key: str | None,
    download_filename: str | None = None,
) -> str:
    safe_key = _validated_tenant_storage_key(tenant_id, storage_key)
    return presign_tenant_storage_key(
        storage,
        tenant_id=tenant_id,
        storage_key=safe_key,
        expires_in=settings.engine_s3_presign_ttl,
        download_filename=download_filename,
    )


def _successful_photo_filters(tenant_id: str) -> tuple[object, ...]:
    return (
        VideoTask.tenant_id == tenant_id,
        video_mode_filter("photo"),
        VideoTask.status == "done",
        VideoTask.storage_key.is_not(None),
        VideoTask.storage_key.like(_tenant_storage_pattern(tenant_id)),
        VideoTask.deleted_at.is_(None),
    )


def _photo_category_filters(category: PhotoHistoryCategory) -> tuple[object, ...]:
    kind = VideoTask.params["kind"].as_string()
    if category == "image_gen":
        return (kind.is_(None), VideoTask.params["purpose"].as_string().is_(None))
    return (kind == _PHOTO_KIND_BY_CATEGORY[category],)


def _photo_source_query(tenant_id: str, category: PhotoHistoryCategory):
    batch_id = VideoTask.params["batch_id"].as_string()
    history_id = VideoTask.id if category == "image_gen" else func.coalesce(batch_id, VideoTask.id)
    return (
        select(
            history_id.label("id"),
            literal(category).label("category"),
            func.max(VideoTask.created_at).label("created_at"),
            func.count(VideoTask.id).label("item_count"),
        )
        .where(
            *_successful_photo_filters(tenant_id),
            *_photo_category_filters(category),
        )
        .group_by(history_id)
    )


def _replicate_source_query(tenant_id: str):
    available_outputs = (
        select(func.count(EcomReplicateOutput.id))
        .where(
            EcomReplicateOutput.job_id == EcomReplicateJob.id,
            EcomReplicateOutput.tenant_id == tenant_id,
            EcomReplicateOutput.status == "succeeded",
            EcomReplicateOutput.storage_key.is_not(None),
            EcomReplicateOutput.storage_key.like(_tenant_storage_pattern(tenant_id)),
        )
        .correlate(EcomReplicateJob)
        .scalar_subquery()
    )
    return select(
        EcomReplicateJob.id.label("id"),
        literal("ecom_detail").label("category"),
        EcomReplicateJob.created_at.label("created_at"),
        available_outputs.label("item_count"),
    ).where(
        EcomReplicateJob.tenant_id == tenant_id,
        EcomReplicateJob.status.in_(_REPLICATE_HISTORY_STATUSES),
        EcomReplicateJob.deleted_at.is_(None),
    )


def _source_index(tenant_id: str, category: ImageHistoryCategory | None):
    queries = []
    if category is None:
        categories: tuple[PhotoHistoryCategory, ...] = (
            "image_gen",
            "ecom_white",
            "ecom_model",
            "cover",
        )
        queries.extend(_photo_source_query(tenant_id, item) for item in categories)
        queries.append(_replicate_source_query(tenant_id))
    elif category == "ecom_detail":
        queries.append(_replicate_source_query(tenant_id))
    else:
        queries.append(_photo_source_query(tenant_id, cast(PhotoHistoryCategory, category)))

    if len(queries) == 1:
        return queries[0].subquery()
    return union_all(*queries).subquery()


def _photo_tasks_for_history(
    db: Session,
    *,
    tenant_id: str,
    category: PhotoHistoryCategory,
    history_id: str,
) -> list[VideoTask]:
    filters = [
        *_successful_photo_filters(tenant_id),
        *_photo_category_filters(category),
    ]
    if category == "image_gen":
        filters.append(VideoTask.id == history_id)
    else:
        filters.append(
            or_(
                VideoTask.id == history_id,
                VideoTask.params["batch_id"].as_string() == history_id,
            )
        )
    return list(
        db.scalars(
            select(VideoTask)
            .where(*filters)
            .order_by(VideoTask.created_at.asc(), VideoTask.id.asc())
        )
    )


def _photo_title(category: PhotoHistoryCategory, task: VideoTask) -> str:
    params = task.params or {}
    if category == "image_gen":
        return (task.topic or "图片生成").strip() or "图片生成"
    if category == "ecom_white":
        return "透明底图" if params.get("background") == "transparent" else "白底图"
    if category == "cover":
        return (task.topic or "封面").strip() or "封面"
    extra_prompt = str(params.get("extra_prompt") or "").strip()
    if extra_prompt:
        return extra_prompt
    style_id = str(params.get("style_id") or "").strip()
    return style_id or "电商模特图"


def _photo_history_item(
    db: Session,
    *,
    tenant_id: str,
    storage: ObjectStorage,
    row: Mapping[str, Any],
) -> ImageHistoryItem:
    category = cast(PhotoHistoryCategory, str(row["category"]))
    tasks = _photo_tasks_for_history(
        db,
        tenant_id=tenant_id,
        category=category,
        history_id=str(row["id"]),
    )
    first = tasks[0]
    return ImageHistoryItem(
        id=str(row["id"]),
        category=category,
        title=_photo_title(category, first),
        cover_url=_presign_tenant_storage_key(
            storage,
            tenant_id=tenant_id,
            storage_key=_photo_cover_storage_key(tenant_id, first),
        ),
        created_at=row["created_at"],
        status="ready",
        item_count=int(row["item_count"]),
    )


def _replicate_title(job: EcomReplicateJob) -> str:
    product_name = str((job.product_info or {}).get("name") or "").strip()
    if product_name:
        return product_name
    selling_points = list(job.selling_points or [])
    if selling_points:
        first = str(selling_points[0]).strip()
        if first:
            return first
    return "电商详情图"


def _safe_replicate_outputs(
    db: Session,
    *,
    tenant_id: str,
    job_id: str,
) -> list[EcomReplicateOutput]:
    return list(
        db.scalars(
            select(EcomReplicateOutput)
            .where(
                EcomReplicateOutput.job_id == job_id,
                EcomReplicateOutput.tenant_id == tenant_id,
                EcomReplicateOutput.status == "succeeded",
                EcomReplicateOutput.storage_key.is_not(None),
                EcomReplicateOutput.storage_key.like(_tenant_storage_pattern(tenant_id)),
            )
            .order_by(EcomReplicateOutput.index.asc())
        )
    )


def _replicate_cover_url(
    db: Session,
    *,
    tenant_id: str,
    job: EcomReplicateJob,
    storage: ObjectStorage,
) -> str:
    safe_outputs = _safe_replicate_outputs(
        db,
        tenant_id=tenant_id,
        job_id=job.id,
    )
    if safe_outputs:
        _validated_tenant_storage_key(tenant_id, safe_outputs[0].storage_key)
        output = ecom_replicate.output_response(safe_outputs[0], storage=storage)
        if output.download_url:
            return output.download_url

    asset_ids = [
        *list(job.product_image_asset_ids or []),
        *list(job.reference_image_asset_ids or []),
    ]
    for asset_id in asset_ids:
        asset = db.get(Asset, str(asset_id))
        if (
            asset is not None
            and asset.tenant_id == tenant_id
            and asset.status == "ready"
            and asset.deleted_at is None
            and _is_tenant_storage_key(tenant_id, asset.storage_key)
        ):
            return _presign_tenant_storage_key(
                storage,
                tenant_id=tenant_id,
                storage_key=asset.storage_key,
            )
    return ""


def _replicate_history_item(
    db: Session,
    *,
    tenant_id: str,
    storage: ObjectStorage,
    row: Mapping[str, Any],
) -> ImageHistoryItem:
    job = db.scalar(
        select(EcomReplicateJob).where(
            EcomReplicateJob.id == str(row["id"]),
            EcomReplicateJob.tenant_id == tenant_id,
            EcomReplicateJob.deleted_at.is_(None),
        )
    )
    if job is None:  # pragma: no cover - source row and hydration share one transaction
        raise RuntimeError("Image history replicate source disappeared during hydration.")
    return ImageHistoryItem(
        id=job.id,
        category="ecom_detail",
        title=_replicate_title(job),
        cover_url=_replicate_cover_url(
            db,
            tenant_id=tenant_id,
            job=job,
            storage=storage,
        ),
        created_at=job.created_at,
        status=job.status,
        item_count=int(row["item_count"]),
    )


def _history_item(
    db: Session,
    *,
    tenant_id: str,
    storage: ObjectStorage,
    row: Mapping[str, Any],
) -> ImageHistoryItem:
    if str(row["category"]) == "ecom_detail":
        return _replicate_history_item(
            db,
            tenant_id=tenant_id,
            storage=storage,
            row=row,
        )
    return _photo_history_item(
        db,
        tenant_id=tenant_id,
        storage=storage,
        row=row,
    )


def _photo_output_asset(db: Session, *, tenant_id: str, task_id: str) -> Asset | None:
    return db.scalar(
        select(Asset)
        .join(TaskAsset, TaskAsset.asset_id == Asset.id)
        .where(
            TaskAsset.video_task_id == task_id,
            TaskAsset.role == "output_image",
            Asset.tenant_id == tenant_id,
            Asset.status == "ready",
            Asset.storage_key.like(_tenant_storage_pattern(tenant_id)),
            Asset.deleted_at.is_(None),
        )
        .order_by(Asset.created_at.asc(), Asset.id.asc())
    )


def _parse_dimensions(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    parts = value.lower().split("x", maxsplit=1)
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    width, height = (int(part) for part in parts)
    if width <= 0 or height <= 0:
        return None
    return width, height


def _complete_dimensions(width: object, height: object) -> tuple[int, int] | None:
    if width is None or height is None:
        return None
    return _parse_dimensions(f"{width}x{height}")


def _first_dimensions(*values: object) -> tuple[int, int] | None:
    for value in values:
        dimensions = _parse_dimensions(value)
        if dimensions is not None:
            return dimensions
    return None


def _photo_dimensions(task: VideoTask, asset: Asset | None) -> tuple[int, int]:
    if asset is not None and asset.width and asset.height:
        return int(asset.width), int(asset.height)
    if asset is not None:
        dimensions = _parse_dimensions((asset.metadata_ or {}).get("size"))
        if dimensions is not None:
            return dimensions
    dimensions = _parse_dimensions((task.params or {}).get("image_size"))
    return dimensions or (0, 0)


def _photo_requested_dimensions(
    task: VideoTask,
    asset: Asset | None,
) -> tuple[int, int] | None:
    params = task.params or {}
    metadata = (asset.metadata_ or {}) if asset is not None else {}
    return _first_dimensions(
        params.get("resolved_size"),
        metadata.get("resolved_size"),
        params.get("image_size"),
        metadata.get("image_size"),
        metadata.get("size"),
    )


def _photo_actual_dimensions(
    task: VideoTask,
    asset: Asset | None,
) -> tuple[int, int] | None:
    if asset is not None:
        dimensions = _complete_dimensions(asset.width, asset.height)
        if dimensions is not None:
            return dimensions

    params = task.params or {}
    metadata = (asset.metadata_ or {}) if asset is not None else {}
    for dimensions in (
        _complete_dimensions(metadata.get("actual_width"), metadata.get("actual_height")),
        _complete_dimensions(params.get("actual_width"), params.get("actual_height")),
        _parse_dimensions(metadata.get("actual_size")),
        _parse_dimensions(params.get("actual_size")),
    ):
        if dimensions is not None:
            return dimensions
    return None


def _photo_size_evidence(task: VideoTask, asset: Asset | None) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for source in (task.params or {}, (asset.metadata_ or {}) if asset is not None else {}):
        for key in (
            "requested_aspect_ratio",
            "resolved_aspect_ratio",
            "resolved_size",
            "actual_aspect_ratio",
            "actual_size",
        ):
            value = source.get(key)
            if value is not None:
                evidence[key] = str(value)
    return evidence


def _photo_detail_meta(
    category: PhotoHistoryCategory,
    history_id: str,
    tasks: list[VideoTask],
) -> dict[str, object]:
    params = tasks[0].params or {}
    meta: dict[str, object] = {"task_ids": [task.id for task in tasks]}
    batch_id = str(params.get("batch_id") or "").strip()
    if batch_id:
        meta["batch_id"] = history_id
    if params.get("image_resolution") in {"1k", "2k", "4k"}:
        meta["image_resolution"] = params["image_resolution"]
    if category == "image_gen":
        meta["prompt"] = tasks[0].topic or ""
        for key in ("image_size", "image_quality"):
            if params.get(key) is not None:
                meta[key] = params[key]
    elif category == "ecom_white":
        for key in ("background", "source_asset_id"):
            if params.get(key) is not None:
                meta[key] = params[key]
    elif category == "cover":
        for key in (
            "source",
            "source_video_task_id",
            "timestamp_sec",
            "layout_template_id",
        ):
            if params.get(key) is not None:
                meta[key] = params[key]
    else:
        for key in ("style_id", "gender", "extra_prompt", "source_asset_id"):
            if params.get(key) is not None:
                meta[key] = params[key]
    return meta


def _photo_history_detail(
    db: Session,
    *,
    tenant_id: str,
    storage: ObjectStorage,
    category: PhotoHistoryCategory,
    history_id: str,
) -> ImageHistoryDetailResponse:
    tasks = _photo_tasks_for_history(
        db,
        tenant_id=tenant_id,
        category=category,
        history_id=history_id,
    )
    if not tasks:
        raise AppError(
            "Image history item not found.",
            code="IMAGE_HISTORY_NOT_FOUND",
            status_code=404,
        )

    items: list[ImageHistoryDetailItem] = []
    for index, task in enumerate(tasks):
        asset = _photo_output_asset(db, tenant_id=tenant_id, task_id=task.id)
        width, height = _photo_dimensions(task, asset)
        requested_dimensions = _photo_requested_dimensions(task, asset)
        actual_dimensions = _photo_actual_dimensions(task, asset)
        size_evidence = _photo_size_evidence(task, asset)
        storage_key = asset.storage_key if asset is not None else str(task.storage_key)
        items.append(
            ImageHistoryDetailItem(
                index=index,
                download_url=_presign_tenant_storage_key(
                    storage,
                    tenant_id=tenant_id,
                    storage_key=storage_key,
                    download_filename=f"{task.id}.png",
                ),
                width=width,
                height=height,
                requested_width=(
                    requested_dimensions[0] if requested_dimensions is not None else None
                ),
                requested_height=(
                    requested_dimensions[1] if requested_dimensions is not None else None
                ),
                actual_width=actual_dimensions[0] if actual_dimensions is not None else None,
                actual_height=actual_dimensions[1] if actual_dimensions is not None else None,
                **size_evidence,
            )
        )
    return ImageHistoryDetailResponse(
        id=history_id,
        category=category,
        created_at=max(task.created_at for task in tasks),
        status="ready",
        items=items,
        meta=_photo_detail_meta(category, history_id, tasks),
    )


def _replicate_history_detail(
    db: Session,
    *,
    tenant_id: str,
    storage: ObjectStorage,
    history_id: str,
) -> ImageHistoryDetailResponse:
    job = db.scalar(
        select(EcomReplicateJob).where(
            EcomReplicateJob.id == history_id,
            EcomReplicateJob.tenant_id == tenant_id,
            EcomReplicateJob.status.in_(_REPLICATE_HISTORY_STATUSES),
            EcomReplicateJob.deleted_at.is_(None),
        )
    )
    if job is None:
        raise AppError(
            "Image history item not found.",
            code="IMAGE_HISTORY_NOT_FOUND",
            status_code=404,
        )

    response = ecom_replicate.response_for_job(db, job)
    safe_outputs = _safe_replicate_outputs(
        db,
        tenant_id=tenant_id,
        job_id=job.id,
    )
    items: list[ImageHistoryDetailItem] = []
    for output_row in safe_outputs:
        _validated_tenant_storage_key(tenant_id, output_row.storage_key)
        output = ecom_replicate.output_response(output_row, storage=storage)
        if not output.download_url:  # pragma: no cover - safe rows always have storage keys
            continue
        requested_dimensions = _parse_dimensions(output.requested_size)
        fallback_dimensions = requested_dimensions or (0, 0)
        actual_dimensions = _complete_dimensions(
            output.actual_width,
            output.actual_height,
        )
        items.append(
            ImageHistoryDetailItem(
                index=output.index,
                download_url=output.download_url,
                width=output.actual_width or fallback_dimensions[0],
                height=output.actual_height or fallback_dimensions[1],
                requested_width=(
                    requested_dimensions[0] if requested_dimensions is not None else None
                ),
                requested_height=(
                    requested_dimensions[1] if requested_dimensions is not None else None
                ),
                actual_width=actual_dimensions[0] if actual_dimensions is not None else None,
                actual_height=actual_dimensions[1] if actual_dimensions is not None else None,
                theme=output.theme,
            )
        )
    return ImageHistoryDetailResponse(
        id=job.id,
        category="ecom_detail",
        created_at=job.created_at,
        status=job.status,
        items=items,
        meta={
            "output_mode": job.output_mode,
            "requested_size": job.requested_size,
            "requested_aspect": job.requested_aspect,
            "output_count": job.output_count,
            "product_info": dict(job.product_info or {}),
            "selling_points": list(job.selling_points or []),
            "reference_analysis_json": list(response.plan.reference_analysis_json),
            "template_mapping_json": dict(response.plan.template_mapping_json),
            "generation_plan_json": dict(response.plan.generation_plan_json),
        },
    )


def get_image_history(
    db: Session,
    *,
    tenant_id: str,
    storage: ObjectStorage,
    category: ImageHistoryCategory,
    history_id: str,
) -> ImageHistoryDetailResponse:
    if category == "ecom_detail":
        return _replicate_history_detail(
            db,
            tenant_id=tenant_id,
            storage=storage,
            history_id=history_id,
        )
    return _photo_history_detail(
        db,
        tenant_id=tenant_id,
        storage=storage,
        category=cast(PhotoHistoryCategory, category),
        history_id=history_id,
    )


def delete_image_history(
    db: Session,
    *,
    tenant_id: str,
    category: ImageHistoryCategory,
    history_id: str,
) -> ImageHistoryDeletedResponse:
    if category == "ecom_detail":
        job = db.scalar(
            select(EcomReplicateJob).where(
                EcomReplicateJob.id == history_id,
                EcomReplicateJob.tenant_id == tenant_id,
                EcomReplicateJob.status.in_(_REPLICATE_HISTORY_STATUSES),
                EcomReplicateJob.deleted_at.is_(None),
            )
        )
        if job is not None:
            job.deleted_at = datetime.now(UTC)
            db.commit()
            return ImageHistoryDeletedResponse(deleted=True)
    else:
        tasks = _photo_tasks_for_history(
            db,
            tenant_id=tenant_id,
            category=cast(PhotoHistoryCategory, category),
            history_id=history_id,
        )
        if tasks:
            deleted_at = datetime.now(UTC)
            for task in tasks:
                task.deleted_at = deleted_at
            db.commit()
            return ImageHistoryDeletedResponse(deleted=True)

    raise AppError(
        "Image history item not found.",
        code="IMAGE_HISTORY_NOT_FOUND",
        status_code=404,
    )


def clear_image_history(
    db: Session,
    *,
    tenant_id: str,
    category: ImageHistoryCategory,
) -> ImageHistoryClearResponse:
    deleted_at = datetime.now(UTC)
    if category == "ecom_detail":
        statement = (
            update(EcomReplicateJob)
            .where(
                EcomReplicateJob.tenant_id == tenant_id,
                EcomReplicateJob.status.in_(_REPLICATE_HISTORY_STATUSES),
                EcomReplicateJob.deleted_at.is_(None),
            )
            .values(deleted_at=deleted_at)
        )
    else:
        statement = (
            update(VideoTask)
            .where(
                *_successful_photo_filters(tenant_id),
                *_photo_category_filters(cast(PhotoHistoryCategory, category)),
            )
            .values(deleted_at=deleted_at)
        )

    result = db.execute(statement)
    deleted_count = int(result.rowcount or 0)
    db.commit()
    return ImageHistoryClearResponse(deleted_count=deleted_count)


def list_image_history(
    db: Session,
    *,
    tenant_id: str,
    storage: ObjectStorage,
    category: ImageHistoryCategory | None,
    page: int,
    page_size: int,
) -> ImageHistoryListResponse:
    source = _source_index(tenant_id, category)
    total = int(db.scalar(select(func.count()).select_from(source)) or 0)
    rows = db.execute(
        select(source)
        .order_by(source.c.created_at.desc(), source.c.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).mappings().all()
    items = [
        _history_item(
            db,
            tenant_id=tenant_id,
            storage=storage,
            row=row,
        )
        for row in rows
    ]
    return ImageHistoryListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
