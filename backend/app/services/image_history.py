from collections.abc import Mapping
from typing import Any, Literal, cast

from sqlalchemy import func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import Asset, EcomReplicateJob, EcomReplicateOutput, TaskAsset, VideoTask
from app.schemas.history import (
    ImageHistoryCategory,
    ImageHistoryDetailItem,
    ImageHistoryDetailResponse,
    ImageHistoryItem,
    ImageHistoryListResponse,
)
from app.services import ecom_replicate
from app.services.history import video_mode_filter
from app.services.storage.base import ObjectStorage

PhotoHistoryCategory = Literal["image_gen", "ecom_white", "ecom_model"]

_PHOTO_KIND_BY_CATEGORY: dict[PhotoHistoryCategory, str | None] = {
    "image_gen": None,
    "ecom_white": "ecom_cutout",
    "ecom_model": "ecom_model",
}
_REPLICATE_HISTORY_STATUSES = {"completed", "partial_failed", "failed"}


def _tenant_storage_pattern(tenant_id: str) -> str:
    return f"tenants/{tenant_id}/%"


def _is_tenant_storage_key(tenant_id: str, storage_key: str) -> bool:
    return storage_key.startswith(f"tenants/{tenant_id}/")


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
    )


def _source_index(tenant_id: str, category: ImageHistoryCategory | None):
    queries = []
    if category is None:
        categories: tuple[PhotoHistoryCategory, ...] = (
            "image_gen",
            "ecom_white",
            "ecom_model",
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
        cover_url=storage.presign_get_url(
            first.thumbnail_key or str(first.storage_key),
            expires_in=settings.engine_s3_presign_ttl,
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
            return storage.presign_get_url(
                asset.storage_key,
                expires_in=settings.engine_s3_presign_ttl,
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


def _photo_dimensions(task: VideoTask, asset: Asset | None) -> tuple[int, int]:
    if asset is not None and asset.width and asset.height:
        return int(asset.width), int(asset.height)
    if asset is not None:
        dimensions = _parse_dimensions((asset.metadata_ or {}).get("size"))
        if dimensions is not None:
            return dimensions
    dimensions = _parse_dimensions((task.params or {}).get("image_size"))
    return dimensions or (0, 0)


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
    if category == "image_gen":
        meta["prompt"] = tasks[0].topic or ""
        for key in ("image_size", "image_quality"):
            if params.get(key) is not None:
                meta[key] = params[key]
    elif category == "ecom_white":
        for key in ("background", "source_asset_id"):
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
        storage_key = asset.storage_key if asset is not None else str(task.storage_key)
        items.append(
            ImageHistoryDetailItem(
                index=index,
                download_url=storage.presign_get_url(
                    storage_key,
                    expires_in=settings.engine_s3_presign_ttl,
                    download_filename=f"{task.id}.png",
                ),
                width=width,
                height=height,
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
        output = ecom_replicate.output_response(output_row, storage=storage)
        if not output.download_url:  # pragma: no cover - safe rows always have storage keys
            continue
        fallback_dimensions = _parse_dimensions(output.requested_size) or (0, 0)
        items.append(
            ImageHistoryDetailItem(
                index=output.index,
                download_url=output.download_url,
                width=output.actual_width or fallback_dimensions[0],
                height=output.actual_height or fallback_dimensions[1],
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
