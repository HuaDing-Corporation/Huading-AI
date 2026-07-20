from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import DbSessionDependency, get_object_storage, require_permission
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.models import Asset, User, VideoTask
from app.providers.base import (
    ImageProviderCapabilitiesError,
    ProviderResolutionError,
    ResolvedProvider,
    resolve_with_name,
    validate_image_provider_request,
)
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


@dataclass(frozen=True)
class _ModelStyleDefinition:
    name: str
    prompt: str


_MODEL_STYLE_DEFINITIONS = {
    "studio_white": _ModelStyleDefinition(
        name="棚拍白底",
        prompt="a clean studio white product catalog scene with controlled lighting",
    ),
    "lifestyle": _ModelStyleDefinition(
        name="生活场景",
        prompt="a natural lifestyle scene with realistic everyday styling",
    ),
    "street": _ModelStyleDefinition(
        name="街拍",
        prompt="a modern street fashion scene with editorial styling",
    ),
    "office_commute": _ModelStyleDefinition(
        name="通勤职场",
        prompt="a polished office commute scene with professional urban styling",
    ),
    "resort_travel": _ModelStyleDefinition(
        name="度假旅拍",
        prompt="a relaxed resort travel scene with bright natural light and destination styling",
    ),
    "high_fashion": _ModelStyleDefinition(
        name="高级时尚大片",
        prompt="a high-fashion editorial campaign with dramatic lighting and premium art direction",
    ),
}
_MODEL_STYLES: tuple[EcomModelStyle, ...] = tuple(
    EcomModelStyle(id=style_id, name=definition.name)
    for style_id, definition in _MODEL_STYLE_DEFINITIONS.items()
)
_PRODUCT_IMAGE_MODE_PROMPTS = {
    "multi_item": (
        "The product reference images show different products. Put, wear, or coordinate every "
        "product on one model in the same image. Do not merge products together and do not omit "
        "any product."
    ),
    "multi_angle": (
        "The product reference images show different angles of the same product. Reconstruct "
        "exactly one coherent product from all angles. Do not duplicate the product."
    ),
}
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
    definition = _MODEL_STYLE_DEFINITIONS.get(style_id)
    if definition is None:
        raise AppError(
            "Unknown AI model style.",
            code="ECOM_MODEL_STYLE_INVALID",
            status_code=422,
        )
    return definition.prompt


def _clamp_text(text: str, *, limit: int) -> str:
    return text[:limit]


def _model_prompt(
    *,
    gender: str,
    style_id: str | None,
    custom_style: str | None,
    product_images_mode: str,
    has_model_references: bool,
    extra_prompt: str | None,
) -> str:
    reference_prompt = (
        "Reference order is strict: product reference images come first, followed by model "
        "reference images. Use product references only for product design; use model references "
        "only for the model's identity and appearance."
        if has_model_references
        else "All supplied reference images are product references."
    )
    clauses = [
        "Create an e-commerce fashion image using the supplied reference images.",
        reference_prompt,
        _PRODUCT_IMAGE_MODE_PROMPTS[product_images_mode],
    ]
    if gender != "any":
        clauses.append(f"Use a {gender} fashion model.")
    if style_id is not None:
        style_prompt = _model_style_prompt_or_raise(style_id)
        clauses.append(f"Apply this visual style: {style_prompt}.")
    elif custom_style is not None:
        clauses.append(f"Apply this custom visual style: {custom_style}.")
    clauses.extend(
        [
            (
                "Preserve the exact shape, logos, colors, materials, proportions, and visible "
                "details of every product."
            ),
            "Do not alter product designs, brand marks, text, or colorways.",
        ]
    )
    if extra_prompt:
        clauses.append(f"Additional direction: {extra_prompt}")
    return " ".join(clauses)


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
    image_provider: str,
    source: Asset,
    batch_id: str | None = None,
) -> dict[str, object]:
    params: dict[str, object] = {
        "kind": _ECOM_CUTOUT_KIND,
        "background": payload.background,
        "source_asset_id": source.id,
        "source_storage_key": source.storage_key,
        "image_provider": image_provider,
        "image_resolution": "1k",
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


def _model_source_storage_keys(
    product_sources: list[Asset],
    model_sources: list[Asset],
) -> list[str]:
    return [
        *[source.storage_key for source in product_sources],
        *[source.storage_key for source in model_sources],
    ]


def _model_task_params(
    payload: EcomModelRequest,
    *,
    image_provider: str,
    product_sources: list[Asset],
    model_sources: list[Asset],
    extra_prompt: str | None,
    batch_id: str | None = None,
) -> dict[str, object]:
    primary_source = product_sources[0]
    source_storage_keys = _model_source_storage_keys(product_sources, model_sources)
    params: dict[str, object] = {
        "kind": _ECOM_MODEL_KIND,
        "gender": payload.gender,
        "style_id": payload.style_id,
        "product_images_mode": payload.product_images_mode,
        "product_asset_ids": [source.id for source in product_sources],
        "model_asset_ids": [source.id for source in model_sources],
        "source_asset_id": primary_source.id,
        "source_storage_key": primary_source.storage_key,
        "source_storage_keys": source_storage_keys,
        "image_provider": image_provider,
        "image_resolution": "1k",
        "aspect_ratio": payload.aspect_ratio,
        "requested_aspect_ratio": payload.aspect_ratio,
        "estimated": True,
        "apply_visible_label": payload.apply_visible_label,
    }
    if payload.custom_style is not None:
        params["custom_style"] = payload.custom_style
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
    image_provider: str,
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
        params=_task_params(
            payload,
            image_provider=image_provider,
            source=source,
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
        provider=image_provider,
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
    image_provider: str,
    product_sources: list[Asset],
    model_sources: list[Asset],
    batch_id: str | None = None,
) -> VideoTask:
    extra_prompt = payload.extra_prompt
    task = VideoTask(
        id=str(uuid4()),
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="queued",
        topic=_model_prompt(
            gender=payload.gender,
            style_id=payload.style_id,
            custom_style=payload.custom_style,
            product_images_mode=payload.product_images_mode,
            has_model_references=bool(model_sources),
            extra_prompt=extra_prompt,
        ),
        mode="photo",
        video_mode="photo",
        progress=0,
        aspect_ratio=payload.aspect_ratio,
        params=_model_task_params(
            payload,
            image_provider=image_provider,
            product_sources=product_sources,
            model_sources=model_sources,
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
        provider=image_provider,
    )
    return task


def _worker_payload(task: VideoTask) -> dict[str, object]:
    params = dict(task.params or {})
    params["tenant_id"] = task.tenant_id
    params["video_task_id"] = task.id
    params["topic"] = task.topic or _cutout_prompt(str(params.get("background") or "white"))
    return params


def _ecom_image_provider_or_raise(
    db: Session,
    *,
    tenant_id: str,
    source_storage_key_groups: list[list[str]],
) -> ResolvedProvider:
    try:
        selection = resolve_with_name(db, tenant_id=tenant_id, capability="image")
        for source_storage_keys in source_storage_key_groups:
            validate_image_provider_request(
                selection.provider,
                {
                    "image_resolution": "1k",
                    "source_storage_keys": source_storage_keys,
                },
            )
    except ProviderResolutionError as exc:
        raise AppError(
            "当前图片服务未配置，暂时无法生成图片。",
            code="IMAGE_PROVIDER_NOT_CONFIGURED",
            status_code=503,
        ) from exc
    except ImageProviderCapabilitiesError as exc:
        raise AppError(
            exc.user_message,
            code=exc.code,
            status_code=422,
        ) from exc
    return selection


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
    product_sources = [
        _source_asset_or_raise(
            db,
            tenant_id=user.tenant_id,
            source_asset_id=asset_id,
        )
        for asset_id in payload.resolved_product_asset_ids
    ]
    model_sources = [
        _source_asset_or_raise(
            db,
            tenant_id=user.tenant_id,
            source_asset_id=asset_id,
        )
        for asset_id in (payload.model_asset_ids or [])
    ]
    selection = _ecom_image_provider_or_raise(
        db,
        tenant_id=user.tenant_id,
        source_storage_key_groups=[
            _model_source_storage_keys(product_sources, model_sources)
        ],
    )
    task = _create_model_task(
        db,
        user=user,
        payload=payload,
        image_provider=selection.name,
        product_sources=product_sources,
        model_sources=model_sources,
    )
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
    product_source_groups = [
        [
            _source_asset_or_raise(
                db,
                tenant_id=user.tenant_id,
                source_asset_id=asset_id,
            )
            for asset_id in item.resolved_product_asset_ids
        ]
        for item in selected_items
    ]
    model_source_groups = [
        [
            _source_asset_or_raise(
                db,
                tenant_id=user.tenant_id,
                source_asset_id=asset_id,
            )
            for asset_id in (item.model_asset_ids or [])
        ]
        for item in selected_items
    ]
    source_storage_key_groups = [
        _model_source_storage_keys(product_sources, model_sources)
        for product_sources, model_sources in zip(
            product_source_groups,
            model_source_groups,
            strict=True,
        )
    ]
    selection = _ecom_image_provider_or_raise(
        db,
        tenant_id=user.tenant_id,
        source_storage_key_groups=source_storage_key_groups,
    )
    batch_id = str(uuid4())
    tasks = [
        _create_model_task(
            db,
            user=user,
            payload=item,
            image_provider=selection.name,
            product_sources=product_sources,
            model_sources=model_sources,
            batch_id=batch_id,
        )
        for item, product_sources, model_sources in zip(
            selected_items,
            product_source_groups,
            model_source_groups,
            strict=True,
        )
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
    selection = _ecom_image_provider_or_raise(
        db,
        tenant_id=user.tenant_id,
        source_storage_key_groups=[[source.storage_key]],
    )
    task = _create_cutout_task(
        db,
        user=user,
        payload=payload,
        image_provider=selection.name,
        source=source,
    )
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
    selection = _ecom_image_provider_or_raise(
        db,
        tenant_id=user.tenant_id,
        source_storage_key_groups=[[source.storage_key] for source in sources],
    )
    batch_id = str(uuid4())
    tasks = [
        _create_cutout_task(
            db,
            user=user,
            payload=item,
            image_provider=selection.name,
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
