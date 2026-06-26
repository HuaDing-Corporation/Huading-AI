from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import DbSessionDependency, get_object_storage, require_permission
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.models import Asset, User, VideoTask
from app.schemas.ecom_images import (
    EcomCutoutAccepted,
    EcomCutoutBatchAccepted,
    EcomCutoutBatchItem,
    EcomCutoutBatchRequest,
    EcomCutoutRequest,
)
from app.schemas.response import ApiResponse, ok
from app.services.history import prune_video_history
from app.services.quota import reserve_image_generation_quota
from app.services.storage.base import ObjectStorage
from app.workers.image_gen import generate_image_task

router = APIRouter()
logger = get_logger(__name__)
CreateEcomImagePermissionDependency = Depends(require_permission("video:create"))
ObjectStorageDependency = Depends(get_object_storage)

_ECOM_CUTOUT_KIND = "ecom_cutout"
_BATCH_LIMIT = 20
_SOURCE_IMAGE_TYPES = {"avatar_image", "product_image", "generated_image"}
_SOURCE_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_DEFAULT_IMAGE_SIZE = "1024x1024"
_DEFAULT_IMAGE_QUALITY = "medium"


def _is_valid_source_image(asset: Asset) -> bool:
    mime_type = (asset.mime_type or "").lower()
    return (
        asset.status == "ready"
        and asset.deleted_at is None
        and asset.type in _SOURCE_IMAGE_TYPES
        and mime_type in _SOURCE_IMAGE_MIME_TYPES
    )


def _source_asset_or_raise(db: Session, *, tenant_id: str, source_asset_id: str) -> Asset:
    source = db.get(Asset, source_asset_id)
    if source is None or source.tenant_id != tenant_id:
        raise AppError(
            "Source asset not found.",
            code="ECOM_SOURCE_ASSET_NOT_FOUND",
            status_code=404,
        )
    if not _is_valid_source_image(source):
        raise AppError(
            "Source asset must be a ready image asset.",
            code="ECOM_SOURCE_ASSET_INVALID",
            status_code=422,
        )
    expected_prefix = f"tenants/{tenant_id}/"
    if (
        not source.storage_key.startswith(expected_prefix)
        or ".." in source.storage_key
        or "\\" in source.storage_key
    ):
        raise AppError(
            "Source asset storage key is invalid.",
            code="ECOM_SOURCE_ASSET_INVALID",
            status_code=422,
        )
    return source


def _cutout_prompt(background: str) -> str:
    if background == "transparent":
        return (
            "Create an e-commerce product cutout from the input image. Preserve the exact "
            "product shape, colors, logos, materials, and proportions. Remove the entire "
            "background and output a PNG with a real transparent alpha channel around the "
            "product. Do not add shadows, people, text, props, or new product details."
        )
    return (
        "Create an e-commerce product cutout from the input image. Preserve the exact product "
        "shape, colors, logos, materials, and proportions. Replace the background with a clean "
        "pure white studio background. Do not add people, text, props, or new product details."
    )


def _task_params(
    payload: EcomCutoutRequest,
    *,
    source: Asset,
    batch_id: str | None = None,
) -> dict[str, object]:
    params: dict[str, object] = {
        "kind": _ECOM_CUTOUT_KIND,
        "background": payload.background,
        "source_asset_id": source.id,
        "source_storage_key": source.storage_key,
        "image_size": _DEFAULT_IMAGE_SIZE,
        "image_quality": _DEFAULT_IMAGE_QUALITY,
        "estimated": True,
    }
    if batch_id is not None:
        params["batch_id"] = batch_id
    return params


def _create_cutout_task(
    db: Session,
    *,
    user: User,
    payload: EcomCutoutRequest,
    source: Asset,
    batch_id: str | None = None,
) -> VideoTask:
    task = VideoTask(
        id=str(uuid4()),
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="queued",
        topic=_cutout_prompt(payload.background),
        mode="photo",
        video_mode="photo",
        progress=0,
        params=_task_params(payload, source=source, batch_id=batch_id),
    )
    db.add(task)
    db.flush()
    reserve_image_generation_quota(
        db,
        tenant_id=user.tenant_id,
        video_task_id=task.id,
        quality=_DEFAULT_IMAGE_QUALITY,
        n=1,
    )
    return task


def _worker_payload(task: VideoTask) -> dict[str, object]:
    params = dict(task.params or {})
    params["tenant_id"] = task.tenant_id
    params["video_task_id"] = task.id
    params["topic"] = task.topic or _cutout_prompt(str(params.get("background") or "white"))
    return params


def _enqueue_cutout(task: VideoTask) -> None:
    generate_image_task.apply_async(args=[_worker_payload(task)], task_id=task.id, queue="image")


def _prune_photo_history_best_effort(
    db: Session,
    *,
    tenant_id: str,
    storage: ObjectStorage,
) -> None:
    try:
        prune_video_history(db, tenant_id=tenant_id, mode="photo", storage=storage)
    except Exception as exc:  # pragma: no cover - cleanup must not block enqueue
        logger.warning("ecom_image_history_prune_failed", tenant_id=tenant_id, error=str(exc))


@router.post(
    "/cutout",
    response_model=ApiResponse[EcomCutoutAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_cutout(
    request: Request,
    payload: EcomCutoutRequest,
    user: User = CreateEcomImagePermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[EcomCutoutAccepted]:
    source = _source_asset_or_raise(
        db,
        tenant_id=user.tenant_id,
        source_asset_id=payload.source_asset_id,
    )
    task = _create_cutout_task(db, user=user, payload=payload, source=source)
    db.commit()
    _prune_photo_history_best_effort(db, tenant_id=user.tenant_id, storage=storage)
    _enqueue_cutout(task)
    return ok(request, EcomCutoutAccepted(task_id=task.id))


@router.post(
    "/cutout/batch",
    response_model=ApiResponse[EcomCutoutBatchAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_cutout_batch(
    request: Request,
    payload: EcomCutoutBatchRequest,
    user: User = CreateEcomImagePermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[EcomCutoutBatchAccepted]:
    selected_items = payload.items[:_BATCH_LIMIT]
    sources = [
        _source_asset_or_raise(
            db,
            tenant_id=user.tenant_id,
            source_asset_id=item.source_asset_id,
        )
        for item in selected_items
    ]
    batch_id = str(uuid4())
    tasks = [
        _create_cutout_task(
            db,
            user=user,
            payload=item,
            source=source,
            batch_id=batch_id,
        )
        for item, source in zip(selected_items, sources, strict=True)
    ]
    db.commit()
    _prune_photo_history_best_effort(db, tenant_id=user.tenant_id, storage=storage)
    for task in tasks:
        _enqueue_cutout(task)

    response_tasks = [
        EcomCutoutBatchItem(
            task_id=task.id,
            source_asset_id=str(task.params["source_asset_id"]),
        )
        for task in tasks
    ]
    return ok(request, EcomCutoutBatchAccepted(batch_id=batch_id, tasks=response_tasks))
