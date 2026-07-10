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
    EcomModelAccepted,
    EcomModelBatchAccepted,
    EcomModelBatchItem,
    EcomModelBatchRequest,
    EcomModelRequest,
    EcomModelStyle,
    EcomModelStylesResponse,
    EcomPosterAccepted,
    EcomPosterBatchAccepted,
    EcomPosterBatchRequest,
    EcomPosterRequest,
    EcomPosterTemplate,
    EcomPosterTemplatesResponse,
    EcomReplicateAccepted,
    EcomReplicateConfirmAccepted,
    EcomReplicatePlanOutput,
    EcomReplicateRequest,
)
from app.schemas.response import ApiResponse, ok
from app.services import ecom_replicate
from app.services.history import prune_video_history
from app.services.quota import reserve_image_generation_quota
from app.services.storage.base import ObjectStorage
from app.workers.image_gen import generate_ecom_replicate_task, generate_image_task

router = APIRouter()
logger = get_logger(__name__)
CreateEcomImagePermissionDependency = Depends(require_permission("video:create"))
ObjectStorageDependency = Depends(get_object_storage)

_ECOM_CUTOUT_KIND = "ecom_cutout"
_ECOM_MODEL_KIND = "ecom_model"
_ECOM_POSTER_KIND = "ecom_poster"
_BATCH_LIMIT = 20
_SOURCE_IMAGE_TYPES = {"avatar_image", "product_image", "generated_image"}
_SOURCE_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MODEL_STYLES: tuple[EcomModelStyle, ...] = (
    EcomModelStyle(id="studio_white", name="Studio white"),
    EcomModelStyle(id="lifestyle", name="Lifestyle"),
    EcomModelStyle(id="street", name="Street style"),
)
_MODEL_STYLE_PROMPTS = {
    "studio_white": "a clean studio white product catalog scene with controlled lighting",
    "lifestyle": "a natural lifestyle scene with realistic everyday styling",
    "street": "a modern street fashion scene with editorial styling",
}
_EXTRA_PROMPT_LIMIT = 200
_POSTER_TITLE_LIMIT = 30
_POSTER_SUBTITLE_LIMIT = 40
_POSTER_TEMPLATES: tuple[EcomPosterTemplate, ...] = (
    EcomPosterTemplate(id="promo_bold", name="Promo bold"),
    EcomPosterTemplate(id="minimal", name="Minimal"),
    EcomPosterTemplate(id="festival", name="Festival"),
)
_POSTER_TEMPLATE_IDS = {template.id for template in _POSTER_TEMPLATES}


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


def _model_style_prompt_or_raise(style_id: str) -> str:
    prompt = _MODEL_STYLE_PROMPTS.get(style_id)
    if prompt is None:
        raise AppError(
            "Unknown AI model style.",
            code="ECOM_MODEL_STYLE_INVALID",
            status_code=422,
        )
    return prompt


def _clamp_extra_prompt(extra_prompt: str | None) -> str | None:
    if extra_prompt is None:
        return None
    return extra_prompt[:_EXTRA_PROMPT_LIMIT]


def _clamp_text(text: str, *, limit: int) -> str:
    return text[:limit]


def _model_prompt(*, gender: str, style_id: str, extra_prompt: str | None) -> str:
    style_prompt = _model_style_prompt_or_raise(style_id)
    gender_phrase = "" if gender == "any" else f" Use a {gender} fashion model."
    extra = f" Additional direction: {extra_prompt}" if extra_prompt else ""
    return (
        "Compose the input product image onto an AI fashion model for an e-commerce product "
        f"image in {style_prompt}.{gender_phrase} Preserve the exact product shape, logo, "
        "colors, materials, proportions, and visible details. Do not alter the product design, "
        f"brand marks, text, or colorway.{extra}"
    )


def _poster_template_or_raise(template_id: str) -> str:
    if template_id not in _POSTER_TEMPLATE_IDS:
        raise AppError(
            "Unknown poster template.",
            code="ECOM_POSTER_TEMPLATE_INVALID",
            status_code=422,
        )
    return template_id


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
        "aspect_ratio": payload.aspect_ratio,
        "requested_aspect_ratio": payload.aspect_ratio,
        "estimated": True,
        "apply_visible_label": payload.apply_visible_label,
    }
    if batch_id is not None:
        params["batch_id"] = batch_id
    return params


def _poster_task_params(
    payload: EcomPosterRequest,
    *,
    source: Asset,
    title: str,
    subtitle: str,
    batch_id: str | None = None,
) -> dict[str, object]:
    params: dict[str, object] = {
        "kind": _ECOM_POSTER_KIND,
        "template_id": payload.template_id,
        "title": title,
        "subtitle": subtitle,
        "source_asset_id": source.id,
        "source_storage_key": source.storage_key,
        "apply_visible_label": payload.apply_visible_label,
    }
    if batch_id is not None:
        params["batch_id"] = batch_id
    return params


def _model_task_params(
    payload: EcomModelRequest,
    *,
    source: Asset,
    extra_prompt: str | None,
    batch_id: str | None = None,
) -> dict[str, object]:
    params: dict[str, object] = {
        "kind": _ECOM_MODEL_KIND,
        "gender": payload.gender,
        "style_id": payload.style_id,
        "source_asset_id": source.id,
        "source_storage_key": source.storage_key,
        "aspect_ratio": payload.aspect_ratio,
        "requested_aspect_ratio": payload.aspect_ratio,
        "estimated": True,
        "apply_visible_label": payload.apply_visible_label,
    }
    if extra_prompt:
        params["extra_prompt"] = extra_prompt
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
        aspect_ratio=payload.aspect_ratio,
        params=_task_params(payload, source=source, batch_id=batch_id),
    )
    db.add(task)
    db.flush()
    reserve_image_generation_quota(
        db,
        tenant_id=user.tenant_id,
        video_task_id=task.id,
        n=1,
    )
    return task


def _create_poster_task(
    db: Session,
    *,
    user: User,
    payload: EcomPosterRequest,
    source: Asset,
    batch_id: str | None = None,
) -> VideoTask:
    _poster_template_or_raise(payload.template_id)
    title = _clamp_text(payload.title, limit=_POSTER_TITLE_LIMIT)
    subtitle = _clamp_text(payload.subtitle, limit=_POSTER_SUBTITLE_LIMIT)
    task = VideoTask(
        id=str(uuid4()),
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="queued",
        topic=title,
        mode="photo",
        video_mode="photo",
        progress=0,
        params=_poster_task_params(
            payload,
            source=source,
            title=title,
            subtitle=subtitle,
            batch_id=batch_id,
        ),
    )
    db.add(task)
    db.flush()
    return task


def _create_model_task(
    db: Session,
    *,
    user: User,
    payload: EcomModelRequest,
    source: Asset,
    batch_id: str | None = None,
) -> VideoTask:
    extra_prompt = _clamp_extra_prompt(payload.extra_prompt)
    task = VideoTask(
        id=str(uuid4()),
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="queued",
        topic=_model_prompt(
            gender=payload.gender,
            style_id=payload.style_id,
            extra_prompt=extra_prompt,
        ),
        mode="photo",
        video_mode="photo",
        progress=0,
        aspect_ratio=payload.aspect_ratio,
        params=_model_task_params(
            payload,
            source=source,
            extra_prompt=extra_prompt,
            batch_id=batch_id,
        ),
    )
    db.add(task)
    db.flush()
    reserve_image_generation_quota(
        db,
        tenant_id=user.tenant_id,
        video_task_id=task.id,
        n=1,
    )
    return task


def _worker_payload(task: VideoTask) -> dict[str, object]:
    params = dict(task.params or {})
    params["tenant_id"] = task.tenant_id
    params["video_task_id"] = task.id
    params["topic"] = task.topic or _cutout_prompt(str(params.get("background") or "white"))
    return params


def _enqueue_image_task(task: VideoTask) -> None:
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


def _poster_disabled() -> None:
    raise AppError(
        "Marketing poster generation has been retired.",
        code="ECOM_POSTER_DISABLED",
        status_code=410,
    )


@router.post(
    "/replicate",
    response_model=ApiResponse[EcomReplicateAccepted],
    status_code=status.HTTP_201_CREATED,
)
def create_replicate_plan(
    request: Request,
    payload: EcomReplicateRequest,
    user: User = CreateEcomImagePermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[EcomReplicateAccepted]:
    job = ecom_replicate.create_replicate_plan(
        db,
        user=user,
        payload=payload,
        storage=storage,
    )
    db.commit()
    return ok(request, ecom_replicate.response_for_job(db, job))


@router.get(
    "/replicate/{job_id}",
    response_model=ApiResponse[EcomReplicateAccepted],
)
def get_replicate_job(
    request: Request,
    job_id: str,
    user: User = CreateEcomImagePermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[EcomReplicateAccepted]:
    job = ecom_replicate.job_or_404(db, tenant_id=user.tenant_id, job_id=job_id)
    return ok(request, ecom_replicate.response_for_job(db, job, storage=storage))


@router.post(
    "/replicate/{job_id}/confirm",
    response_model=ApiResponse[EcomReplicateConfirmAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def confirm_replicate_plan(
    request: Request,
    job_id: str,
    user: User = CreateEcomImagePermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[EcomReplicateConfirmAccepted]:
    job, should_enqueue = ecom_replicate.confirm_replicate_job(
        db,
        tenant_id=user.tenant_id,
        job_id=job_id,
    )
    db.commit()
    if should_enqueue:
        generate_ecom_replicate_task.apply_async(args=[job.id], task_id=job.id, queue="image")
    return ok(request, ecom_replicate.confirm_response(job))


@router.post(
    "/replicate/{job_id}/outputs/{output_index}/retry",
    response_model=ApiResponse[EcomReplicatePlanOutput],
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_replicate_output(
    request: Request,
    job_id: str,
    output_index: int,
    user: User = CreateEcomImagePermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[EcomReplicatePlanOutput]:
    job, output = ecom_replicate.prepare_output_retry(
        db,
        tenant_id=user.tenant_id,
        job_id=job_id,
        output_index=output_index,
    )
    db.commit()
    generate_ecom_replicate_task.apply_async(
        args=[job.id, output.index],
        task_id=job.id,
        queue="image",
    )
    return ok(request, ecom_replicate.output_response(output))


@router.get(
    "/model-styles",
    response_model=ApiResponse[EcomModelStylesResponse],
)
def list_model_styles(
    request: Request,
    _user: User = CreateEcomImagePermissionDependency,
) -> ApiResponse[EcomModelStylesResponse]:
    return ok(request, EcomModelStylesResponse(styles=list(_MODEL_STYLES)))


@router.get(
    "/poster-templates",
    response_model=ApiResponse[EcomPosterTemplatesResponse],
)
def list_poster_templates(
    request: Request,
    _user: User = CreateEcomImagePermissionDependency,
) -> ApiResponse[EcomPosterTemplatesResponse]:
    _poster_disabled()


@router.post(
    "/poster",
    response_model=ApiResponse[EcomPosterAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_poster(
    request: Request,
    payload: EcomPosterRequest,
    user: User = CreateEcomImagePermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[EcomPosterAccepted]:
    _poster_disabled()


@router.post(
    "/poster/batch",
    response_model=ApiResponse[EcomPosterBatchAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_poster_batch(
    request: Request,
    payload: EcomPosterBatchRequest,
    user: User = CreateEcomImagePermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[EcomPosterBatchAccepted]:
    _poster_disabled()


@router.post(
    "/model",
    response_model=ApiResponse[EcomModelAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_model_image(
    request: Request,
    payload: EcomModelRequest,
    user: User = CreateEcomImagePermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[EcomModelAccepted]:
    source = _source_asset_or_raise(
        db,
        tenant_id=user.tenant_id,
        source_asset_id=payload.source_asset_id,
    )
    task = _create_model_task(db, user=user, payload=payload, source=source)
    db.commit()
    _prune_photo_history_best_effort(db, tenant_id=user.tenant_id, storage=storage)
    _enqueue_image_task(task)
    return ok(request, EcomModelAccepted(task_id=task.id))


@router.post(
    "/model/batch",
    response_model=ApiResponse[EcomModelBatchAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_model_image_batch(
    request: Request,
    payload: EcomModelBatchRequest,
    user: User = CreateEcomImagePermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[EcomModelBatchAccepted]:
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
        _create_model_task(
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
        _enqueue_image_task(task)

    response_tasks = [
        EcomModelBatchItem(
            task_id=task.id,
            source_asset_id=str(task.params["source_asset_id"]),
        )
        for task in tasks
    ]
    return ok(request, EcomModelBatchAccepted(batch_id=batch_id, tasks=response_tasks))


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
    _enqueue_image_task(task)
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
        _enqueue_image_task(task)

    response_tasks = [
        EcomCutoutBatchItem(
            task_id=task.id,
            source_asset_id=str(task.params["source_asset_id"]),
        )
        for task in tasks
    ]
    return ok(request, EcomCutoutBatchAccepted(batch_id=batch_id, tasks=response_tasks))
