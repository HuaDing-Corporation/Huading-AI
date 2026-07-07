from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import (
    Asset,
    EcomReplicateJob,
    EcomReplicateOutput,
    Subscription,
    UsageRecord,
    User,
)
from app.providers.base import resolve_named_provider
from app.schemas.ecom_images import (
    EcomReplicateAccepted,
    EcomReplicateConfirmAccepted,
    EcomReplicatePlanOutput,
    EcomReplicatePlanPayload,
    EcomReplicateRequest,
)
from app.services.quota import remaining_credits
from app.services.storage.base import ObjectStorage

_SOURCE_IMAGE_TYPES = {"avatar_image", "product_image", "generated_image"}
_SOURCE_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_SECTION_13_BANNED_COPY_TERMS = (
    "\u7b2c\u4e00",
    "\u9876\u7ea7",
    "\u6700\u597d",
    "\u5929\u82b1\u677f",
    "\u7206\u6b3e",
    "\u552f\u4e00",
    "\u72ec\u5bb6",
    "\u56fd\u5bb6\u7ea7",
    "\u6c38\u4e45",
    "100%",
    "\u7edd\u5bf9",
    "\u7acb\u523b\u89c1\u6548",
    "\u5168\u7f51\u6700\u4f4e",
    "\u9500\u91cf\u7b2c\u4e00",
    "\u6700\u4fbf\u5b9c",
    "\u5305\u6cbb",
    "\u6839\u6cbb",
)
_MAIN_THEMES = (
    "layout_match",
    "color_match",
    "campaign_match",
    "social_match",
    "white_background",
)
_DETAIL_THEMES = (
    "hero",
    "material",
    "function",
    "size",
    "scenario",
    "detail",
    "comparison",
    "packing",
    "care",
    "selling_point",
    "white_background",
    "closing",
)
_ALLOWED_SIZES = {"768x1024", "1024x1024", "1024x1536"}


def source_asset_or_raise(db: Session, *, tenant_id: str, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None or asset.tenant_id != tenant_id:
        raise AppError(
            "Replicate source asset not found.",
            code="ECOM_REPLICATE_ASSET_NOT_FOUND",
            status_code=404,
        )
    if not _is_valid_source_image(asset):
        raise AppError(
            "Replicate source asset must be a ready image.",
            code="ECOM_REPLICATE_ASSET_INVALID",
            status_code=422,
        )
    expected_prefix = f"tenants/{tenant_id}/"
    if (
        not asset.storage_key.startswith(expected_prefix)
        or ".." in asset.storage_key
        or "\\" in asset.storage_key
    ):
        raise AppError(
            "Replicate source asset storage key is invalid.",
            code="ECOM_REPLICATE_ASSET_INVALID",
            status_code=422,
        )
    return asset


def create_replicate_plan(
    db: Session,
    *,
    user: User,
    payload: EcomReplicateRequest,
    storage: ObjectStorage,
) -> EcomReplicateJob:
    if not settings.engine_ecom_replicate_enabled:
        raise AppError(
            "E-commerce replicate is disabled.",
            code="ECOM_REPLICATE_DISABLED",
            status_code=404,
        )
    _validate_copy(payload)

    references = [
        source_asset_or_raise(db, tenant_id=user.tenant_id, asset_id=asset_id)
        for asset_id in payload.reference_image_asset_ids
    ]
    products = [
        source_asset_or_raise(db, tenant_id=user.tenant_id, asset_id=asset_id)
        for asset_id in payload.product_image_asset_ids
    ]
    requested_size = _requested_size(payload.output_mode, payload.size)
    requested_aspect = _aspect_from_size(requested_size)
    themes = _themes(payload.output_mode)

    analyses = [
        _analyze_reference(db, tenant_id=user.tenant_id, reference=reference, storage=storage)
        for reference in references
    ]
    analysis_json = [
        _analysis_payload(result, fallback_id=reference.id)
        for result, reference in zip(analyses, references, strict=True)
    ]
    generation_outputs: list[dict[str, object]] = []
    job = EcomReplicateJob(
        id=str(uuid4()),
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        status="plan_ready",
        output_mode=payload.output_mode,
        requested_size=requested_size,
        requested_aspect=requested_aspect,
        detail_fallback_size=settings.engine_ecom_replicate_detail_fallback_size,
        reference_image_asset_ids=[asset.id for asset in references],
        product_image_asset_ids=[asset.id for asset in products],
        product_info=dict(payload.product_info),
        selling_points=list(payload.selling_points),
        reference_analysis_json=analysis_json,
        template_mapping_json={
            "strategy": "cycle_references_and_products",
            "renderer": "apimart:gpt-image-2",
        },
        generation_plan_json={"outputs": generation_outputs},
        output_count=len(themes),
        total_credits=_tenant_charge_credits(len(themes)),
        credit_rate=_credit_rate(),
        analysis_provider=str(analyses[0].get("provider") or "apimart") if analyses else None,
        analysis_model=(
            str(analyses[0].get("model") or settings.engine_apimart_reverse_prompt_model)
            if analyses
            else None
        ),
        render_provider="apimart",
        render_model=settings.engine_apimart_image_model,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(job)
    db.flush()

    for index, theme in enumerate(themes):
        reference = references[index % len(references)]
        product = products[index % len(products)]
        analysis = analysis_json[index % len(analysis_json)]
        prompt = _render_prompt(
            theme=theme,
            analysis=analysis,
            product_info=dict(payload.product_info),
            selling_points=list(payload.selling_points),
        )
        generation_outputs.append(
            {
                "index": index,
                "theme": theme,
                "reference_asset_id": reference.id,
                "product_asset_id": product.id,
                "requested_size": requested_size,
                "prompt": prompt,
            }
        )
        db.add(
            EcomReplicateOutput(
                id=str(uuid4()),
                job_id=job.id,
                tenant_id=user.tenant_id,
                index=index,
                theme=theme,
                status="planned",
                reference_asset_id=reference.id,
                product_asset_id=product.id,
                requested_size=requested_size,
                requested_aspect=requested_aspect,
                prompt=prompt,
                reference_analysis_json=analysis,
                template_mapping_json={"theme": theme, "source": "reference_analysis"},
                validation_json={"status": "planned"},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    job.generation_plan_json = {"outputs": generation_outputs}
    db.flush()
    return job


def confirm_replicate_job(
    db: Session,
    *,
    tenant_id: str,
    job_id: str,
) -> tuple[EcomReplicateJob, bool]:
    job = db.scalar(
        select(EcomReplicateJob)
        .where(EcomReplicateJob.id == job_id, EcomReplicateJob.tenant_id == tenant_id)
        .with_for_update()
    )
    if job is None:
        raise AppError(
            "Replicate job not found.",
            code="ECOM_REPLICATE_JOB_NOT_FOUND",
            status_code=404,
        )
    if job.status in {"generating", "completed", "partial_failed", "failed", "cancelled"}:
        return job, False
    if job.status != "plan_ready":
        raise AppError(
            "Replicate job is not ready to confirm.",
            code="ECOM_REPLICATE_JOB_NOT_READY",
            status_code=409,
        )

    subscription = _active_subscription_for_update(db, tenant_id)
    charge_units = int(Decimal(job.total_credits).to_integral_value(rounding=ROUND_HALF_UP))
    if remaining_credits(subscription) < charge_units:
        raise AppError(
            "Insufficient tenant quota.",
            code="TENANT_QUOTA_EXCEEDED",
            status_code=403,
        )
    subscription.quota_credits_used += charge_units
    job.status = "generating"
    job.confirmed_at = datetime.now(UTC)
    job.updated_at = datetime.now(UTC)
    db.add(
        UsageRecord(
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            capability="image",
            provider="huading",
            model="ecom-replicate",
            unit="image",
            quantity=Decimal(job.output_count),
            credits=Decimal(job.total_credits),
            cost_cents=0,
            status="settled",
            settled_at=datetime.now(UTC),
        )
    )
    db.flush()
    return job, True


def _active_subscription_for_update(db: Session, tenant_id: str) -> Subscription:
    now = datetime.now(UTC)
    subscription = db.scalar(
        select(Subscription)
        .where(
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
            Subscription.period_start <= now,
            Subscription.period_end >= now,
        )
        .order_by(Subscription.period_end.desc())
        .with_for_update()
    )
    if subscription is None:
        raise AppError(
            "Active subscription not found.",
            code="SUBSCRIPTION_NOT_FOUND",
            status_code=404,
        )
    return subscription


def job_or_404(db: Session, *, tenant_id: str, job_id: str) -> EcomReplicateJob:
    job = db.get(EcomReplicateJob, job_id)
    if job is None or job.tenant_id != tenant_id:
        raise AppError(
            "Replicate job not found.",
            code="ECOM_REPLICATE_JOB_NOT_FOUND",
            status_code=404,
        )
    return job


def outputs_for_job(db: Session, job_id: str) -> list[EcomReplicateOutput]:
    return list(
        db.scalars(
            select(EcomReplicateOutput)
            .where(EcomReplicateOutput.job_id == job_id)
            .order_by(EcomReplicateOutput.index.asc())
        )
    )


def response_for_job(
    db: Session,
    job: EcomReplicateJob,
    *,
    storage: ObjectStorage | None = None,
) -> EcomReplicateAccepted:
    outputs = outputs_for_job(db, job.id)
    return EcomReplicateAccepted(
        job_id=job.id,
        status=job.status,
        output_mode=job.output_mode,
        output_count=job.output_count,
        total_credits=float(job.total_credits),
        credit_rate=float(job.credit_rate),
        requested_size=job.requested_size,
        requested_aspect=job.requested_aspect,
        plan=EcomReplicatePlanPayload(
            outputs=[_output_response(output, storage=storage) for output in outputs],
            reference_analysis_json=list(job.reference_analysis_json or []),
            template_mapping_json=dict(job.template_mapping_json or {}),
            generation_plan_json=dict(job.generation_plan_json or {}),
        ),
    )


def confirm_response(job: EcomReplicateJob) -> EcomReplicateConfirmAccepted:
    return EcomReplicateConfirmAccepted(
        job_id=job.id,
        status=job.status,
        output_count=job.output_count,
        total_credits=float(job.total_credits),
    )


def prepare_output_retry(
    db: Session,
    *,
    tenant_id: str,
    job_id: str,
    output_index: int,
) -> tuple[EcomReplicateJob, EcomReplicateOutput]:
    job = db.scalar(
        select(EcomReplicateJob)
        .where(EcomReplicateJob.id == job_id, EcomReplicateJob.tenant_id == tenant_id)
        .with_for_update()
    )
    if job is None:
        raise AppError(
            "Replicate job not found.",
            code="ECOM_REPLICATE_JOB_NOT_FOUND",
            status_code=404,
        )
    output = db.scalar(
        select(EcomReplicateOutput)
        .where(
            EcomReplicateOutput.job_id == job.id,
            EcomReplicateOutput.tenant_id == tenant_id,
            EcomReplicateOutput.index == output_index,
        )
        .with_for_update()
    )
    if output is None:
        raise AppError(
            "Replicate output not found.",
            code="ECOM_REPLICATE_OUTPUT_NOT_FOUND",
            status_code=404,
        )
    if job.status not in {"partial_failed", "completed"} or output.status != "failed":
        raise AppError(
            "Replicate output is not retryable.",
            code="ECOM_REPLICATE_OUTPUT_NOT_RETRYABLE",
            status_code=409,
        )
    output.status = "planned"
    output.error_code = None
    output.error_message = None
    output.updated_at = datetime.now(UTC)
    job.status = "generating"
    job.error_code = None
    job.error_message = None
    job.finished_at = None
    job.updated_at = datetime.now(UTC)
    db.flush()
    return job, output


def output_response(
    output: EcomReplicateOutput,
    *,
    storage: ObjectStorage | None = None,
) -> EcomReplicatePlanOutput:
    return _output_response(output, storage=storage)


def record_analysis_cost(
    db: Session,
    *,
    tenant_id: str,
    result: dict[str, Any],
) -> None:
    total_tokens = _int(result.get("total_tokens")) or (
        _int(result.get("prompt_tokens")) + _int(result.get("completion_tokens"))
    )
    db.add(
        UsageRecord(
            tenant_id=tenant_id,
            subscription_id=None,
            capability="reverse_prompt",
            provider=str(result.get("provider") or "apimart"),
            model=str(result.get("model") or settings.engine_apimart_reverse_prompt_model),
            unit="token" if total_tokens else "call",
            quantity=Decimal(total_tokens or 1),
            credits=Decimal("0"),
            cost_cents=_cost_cents_from_result(
                result,
                fallback_cny=settings.engine_ecom_replicate_analysis_cny_per_call,
            ),
            status="settled",
            settled_at=datetime.now(UTC),
        )
    )


def record_render_cost(
    db: Session,
    *,
    tenant_id: str,
    result: dict[str, Any],
    fallback_cost_cents: int,
) -> None:
    db.add(
        UsageRecord(
            tenant_id=tenant_id,
            subscription_id=None,
            capability="image",
            provider=str(result.get("provider") or "apimart"),
            model=str(result.get("model") or settings.engine_apimart_image_model),
            unit="image",
            quantity=Decimal("1"),
            credits=Decimal("0"),
            cost_cents=_int(result.get("cost_cents")) or fallback_cost_cents,
            status="settled",
            settled_at=datetime.now(UTC),
        )
    )


def _is_valid_source_image(asset: Asset) -> bool:
    mime_type = (asset.mime_type or "").split(";", 1)[0].strip().lower()
    return (
        asset.status == "ready"
        and asset.deleted_at is None
        and asset.type in _SOURCE_IMAGE_TYPES
        and mime_type in _SOURCE_IMAGE_MIME_TYPES
    )


def _validate_copy(payload: EcomReplicateRequest) -> None:
    text = " ".join(
        [
            str(payload.product_info.get("name") or ""),
            " ".join(str(item) for item in payload.selling_points),
        ]
    )
    if any(term in text for term in _SECTION_13_BANNED_COPY_TERMS):
        raise AppError(
            "Replicate copy contains forbidden marketing language.",
            code="ECOM_REPLICATE_TEXT_FORBIDDEN",
            status_code=422,
        )


def _requested_size(output_mode: str, requested: str | None) -> str:
    normalized = str(requested or "").strip().lower()
    if normalized in _ALLOWED_SIZES:
        return normalized
    if output_mode == "detail":
        return settings.engine_ecom_replicate_detail_size
    return settings.engine_ecom_replicate_main_size


def _aspect_from_size(size: str) -> str:
    width, height = (int(part) for part in size.lower().split("x", 1))
    divisor = _gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _gcd(left: int, right: int) -> int:
    while right:
        left, right = right, left % right
    return left or 1


def _themes(output_mode: str) -> tuple[str, ...]:
    return _DETAIL_THEMES if output_mode == "detail" else _MAIN_THEMES


def _credit_rate() -> Decimal:
    return Decimal(str(settings.engine_ecom_replicate_credits_per_image)).quantize(
        Decimal("0.0001"),
    )


def _tenant_charge_credits(count: int) -> Decimal:
    return (Decimal(count) * _credit_rate()).quantize(Decimal("0.01"))


def _analyze_reference(
    db: Session,
    *,
    tenant_id: str,
    reference: Asset,
    storage: ObjectStorage,
) -> dict[str, Any]:
    provider = resolve_named_provider(
        db,
        tenant_id=tenant_id,
        capability="reverse_prompt",
        provider="apimart-gemini",
    )
    result = asyncio.run(
        provider.reverse_image(
            {
                "image_url": storage.presign_get_url(
                    reference.storage_key,
                    expires_in=settings.engine_s3_presign_ttl,
                ),
                "target_format": "seedance_2_0",
                "source_reference_image_id": reference.id,
            }
        )
    )
    result_dict = dict(result)
    record_analysis_cost(db, tenant_id=tenant_id, result=result_dict)
    return result_dict


def _analysis_payload(result: dict[str, Any], *, fallback_id: str) -> dict[str, object]:
    raw = result.get("reference_analysis_json")
    if isinstance(raw, dict):
        analysis = dict(raw)
    else:
        analysis = {
            "template_id": result.get("template_id") or fallback_id,
            "description": result.get("prompt") or result.get("gpt_image_2_prompt") or "",
        }
    analysis.setdefault("source_reference_image_id", fallback_id)
    return analysis


def _render_prompt(
    *,
    theme: str,
    analysis: dict[str, object],
    product_info: dict[str, object],
    selling_points: list[str],
) -> str:
    name = str(product_info.get("name") or "the product").strip()
    points = " / ".join(str(point).strip() for point in selling_points if str(point).strip())
    return (
        "Use two input images: image 1 is the layout/style reference, image 2 is the exact "
        "product to preserve. Recreate the reference's ecommerce composition for theme "
        f"{theme}. Replace the reference product with {name}; keep product color, logo, "
        "shape, material, proportions, and visible text unchanged. Do not invent product "
        f"details. Selling points to place if text is needed: {points}. "
        f"Reference analysis: {analysis}"
    )


def _output_download_url(
    output: EcomReplicateOutput,
    *,
    storage: ObjectStorage | None,
) -> str | None:
    if storage is None or output.status != "succeeded" or not output.storage_key:
        return None
    return storage.presign_get_url(
        output.storage_key,
        expires_in=settings.engine_s3_presign_ttl,
        download_filename=f"ecom-replicate-{output.job_id}-{output.index:02d}.png",
    )


def _output_response(
    output: EcomReplicateOutput,
    *,
    storage: ObjectStorage | None = None,
) -> EcomReplicatePlanOutput:
    return EcomReplicatePlanOutput(
        id=output.id,
        index=output.index,
        theme=output.theme,
        reference_asset_id=output.reference_asset_id,
        product_asset_id=output.product_asset_id,
        requested_size=output.requested_size,
        requested_aspect=output.requested_aspect,
        status=output.status,
        prompt=output.prompt,
        asset_id=output.asset_id,
        download_url=_output_download_url(output, storage=storage),
        actual_width=output.actual_width,
        actual_height=output.actual_height,
    )


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _cost_cents_from_result(result: dict[str, Any], *, fallback_cny: float) -> int:
    value = _int(result.get("cost_cents"))
    if value > 0:
        return value
    return int(
        (Decimal(str(fallback_cny)) * Decimal("100")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
