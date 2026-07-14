from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import Asset, ReversePromptJob, User
from app.providers.base import resolve
from app.schemas.reverse_prompt import ReversePromptResult
from app.services import quota
from app.services.storage.base import ObjectStorage

_TARGET_FORMAT = "seedance_2_0"
_SOURCE_IMAGE_TYPES = {"avatar_image", "product_image", "generated_image", "cover"}
_SOURCE_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_EMPTY_TEXT_PLACEHOLDERS = {"none", "n/a", "na", "null", "nil"}


def create_reverse_prompt_job(
    db: Session,
    *,
    user: User,
    source_asset_id: str,
    target_format: str,
    storage: ObjectStorage,
) -> ReversePromptJob:
    if target_format != _TARGET_FORMAT:
        raise AppError(
            "Unsupported reverse prompt target format.",
            code="REVERSE_PROMPT_TARGET_INVALID",
            status_code=422,
        )
    source_kind, source = source_asset_or_raise(
        db,
        tenant_id=user.tenant_id,
        asset_id=source_asset_id,
    )
    if source_kind == "video":
        job = ReversePromptJob(
            id=str(uuid4()),
            tenant_id=user.tenant_id,
            created_by_user_id=user.id,
            source_kind="video",
            source_asset_id=source.id,
            source_storage_key=source.storage_key,
            target_format=_TARGET_FORMAT,
            status="queued",
        )
        db.add(job)
        db.flush()
        quota.reserve_reverse_prompt_video_quota(
            db,
            tenant_id=user.tenant_id,
            reverse_prompt_job_id=job.id,
        )
        db.commit()
        db.refresh(job)
        return job
    quota.ensure_reverse_prompt_quota_available(db, tenant_id=user.tenant_id)
    job = ReversePromptJob(
        id=str(uuid4()),
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        source_kind="image",
        source_asset_id=source.id,
        source_storage_key=source.storage_key,
        target_format=_TARGET_FORMAT,
        status="running",
    )
    db.add(job)
    db.flush()
    try:
        result = _invoke_reverse_provider(
            db,
            tenant_id=user.tenant_id,
            image_url=storage.presign_get_url(
                source.storage_key,
                expires_in=settings.engine_s3_presign_ttl,
            ),
        )
        mark_reverse_prompt_job_succeeded(db, job=job, result=result)
        _record_usage(db, tenant_id=user.tenant_id, job=job, result=result)
    except AppError:
        mark_reverse_prompt_job_failed(db, job=job, message="Reverse prompt failed.")
        raise
    except Exception as exc:
        mark_reverse_prompt_job_failed(db, job=job, message=str(exc)[:1000])
        raise AppError(
            "Reverse prompt failed.",
            code="REVERSE_PROMPT_FAILED",
            status_code=502,
        ) from exc
    db.commit()
    db.refresh(job)
    return job


def regenerate_reverse_prompt_job(
    db: Session,
    *,
    user: User,
    job_id: str,
    storage: ObjectStorage,
) -> ReversePromptJob:
    job = reverse_prompt_job_or_404(db, tenant_id=user.tenant_id, job_id=job_id)
    if job.source_kind == "video":
        job = prepare_reverse_prompt_video_retry(
            db,
            tenant_id=user.tenant_id,
            job_id=job_id,
        )
        db.commit()
        db.refresh(job)
        return job
    if not job.source_asset_id:
        raise AppError(
            "Reverse prompt job has no image source.",
            code="REVERSE_PROMPT_SOURCE_NOT_FOUND",
            status_code=404,
        )
    source_image_asset_or_raise(db, tenant_id=user.tenant_id, asset_id=job.source_asset_id)
    quota.ensure_reverse_prompt_quota_available(db, tenant_id=user.tenant_id)
    job.status = "running"
    job.error_code = None
    job.error_message = None
    job.updated_at = datetime.now(UTC)
    try:
        result = _invoke_reverse_provider(
            db,
            tenant_id=user.tenant_id,
            image_url=storage.presign_get_url(
                str(job.source_storage_key),
                expires_in=settings.engine_s3_presign_ttl,
            ),
        )
        mark_reverse_prompt_job_succeeded(db, job=job, result=result)
        _record_usage(db, tenant_id=user.tenant_id, job=job, result=result)
    except AppError:
        mark_reverse_prompt_job_failed(db, job=job, message="Reverse prompt failed.")
        raise
    except Exception as exc:
        mark_reverse_prompt_job_failed(db, job=job, message=str(exc)[:1000])
        raise AppError(
            "Reverse prompt failed.",
            code="REVERSE_PROMPT_FAILED",
            status_code=502,
        ) from exc
    db.commit()
    db.refresh(job)
    return job


def prepare_reverse_prompt_video_retry(
    db: Session,
    *,
    tenant_id: str,
    job_id: str,
    failed_only: bool = False,
    reserve_quota: bool = True,
) -> ReversePromptJob:
    job = _reverse_prompt_job_for_update_or_404(
        db,
        tenant_id=tenant_id,
        job_id=job_id,
    )
    if job.source_kind != "video" or (failed_only and job.status != "failed"):
        raise AppError(
            "Reverse prompt job is not retryable.",
            code="TASK_NOT_RETRYABLE",
            status_code=409,
        )
    if job.status in {"queued", "running"}:
        raise AppError(
            "Reverse prompt job is already running.",
            code="REVERSE_PROMPT_ALREADY_RUNNING",
            status_code=409,
        )
    if not job.source_asset_id:
        raise AppError(
            "Reverse prompt job has no video source.",
            code="REVERSE_PROMPT_SOURCE_NOT_FOUND",
            status_code=404,
        )
    source_kind, source = source_asset_or_raise(
        db,
        tenant_id=tenant_id,
        asset_id=job.source_asset_id,
    )
    if source_kind != "video":
        raise AppError(
            "Reverse prompt source must be a ready video asset.",
            code="REVERSE_PROMPT_SOURCE_INVALID",
            status_code=422,
        )
    job.source_storage_key = source.storage_key
    job.status = "queued"
    job.result_json = None
    job.raw_model_json = None
    job.error_code = None
    job.error_message = None
    job.provider = None
    job.model = None
    job.prompt_tokens = 0
    job.completion_tokens = 0
    job.credits = Decimal("0")
    job.cost_cents = 0
    job.saved_at = None
    job.updated_at = datetime.now(UTC)
    db.flush()
    if reserve_quota:
        quota.reserve_reverse_prompt_video_quota(
            db,
            tenant_id=tenant_id,
            reverse_prompt_job_id=job.id,
        )
    db.flush()
    return job


def save_reverse_prompt_job(
    db: Session,
    *,
    tenant_id: str,
    job_id: str,
) -> ReversePromptJob:
    job = reverse_prompt_job_or_404(db, tenant_id=tenant_id, job_id=job_id)
    if job.status not in {"succeeded", "saved"}:
        raise AppError(
            "Only succeeded reverse prompt jobs can be saved.",
            code="REVERSE_PROMPT_NOT_READY",
            status_code=409,
        )
    job.status = "saved"
    job.saved_at = datetime.now(UTC)
    job.updated_at = job.saved_at
    db.commit()
    db.refresh(job)
    return job


def reverse_prompt_job_or_404(db: Session, *, tenant_id: str, job_id: str) -> ReversePromptJob:
    job = db.get(ReversePromptJob, job_id)
    if job is None or job.tenant_id != tenant_id:
        raise AppError(
            "Reverse prompt job not found.",
            code="REVERSE_PROMPT_JOB_NOT_FOUND",
            status_code=404,
        )
    return job


def _reverse_prompt_job_for_update_or_404(
    db: Session,
    *,
    tenant_id: str,
    job_id: str,
) -> ReversePromptJob:
    job = db.scalar(
        select(ReversePromptJob)
        .where(ReversePromptJob.id == job_id, ReversePromptJob.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise AppError(
            "Reverse prompt job not found.",
            code="REVERSE_PROMPT_JOB_NOT_FOUND",
            status_code=404,
        )
    return job


def source_asset_or_raise(
    db: Session,
    *,
    tenant_id: str,
    asset_id: str,
) -> tuple[str, Asset]:
    source = db.get(Asset, asset_id)
    if source is None or source.tenant_id != tenant_id:
        raise AppError(
            "Reverse prompt source asset not found.",
            code="REVERSE_PROMPT_SOURCE_NOT_FOUND",
            status_code=404,
        )
    if source.status != "ready" or source.deleted_at is not None:
        raise AppError(
            "Reverse prompt source must be a ready image or video asset.",
            code="REVERSE_PROMPT_SOURCE_INVALID",
            status_code=422,
        )
    mime_type = (source.mime_type or "").lower()
    if source.type in _SOURCE_IMAGE_TYPES and mime_type in _SOURCE_IMAGE_MIME_TYPES:
        source_kind = "image"
    elif (
        source.type == "video"
        and mime_type == "video/mp4"
        and (source.metadata_ or {}).get("purpose") == "reverse_prompt"
        and source.duration_ms is not None
        and 1_000 <= source.duration_ms <= 60_000
    ):
        source_kind = "video"
    else:
        raise AppError(
            "Reverse prompt source must be a supported ready image or video asset.",
            code="REVERSE_PROMPT_SOURCE_INVALID",
            status_code=422,
        )
    expected_prefix = f"tenants/{tenant_id}/"
    if (
        not source.storage_key.startswith(expected_prefix)
        or ".." in source.storage_key
        or "\\" in source.storage_key
    ):
        raise AppError(
            "Reverse prompt source storage key is invalid.",
            code="REVERSE_PROMPT_SOURCE_INVALID",
            status_code=422,
        )
    return source_kind, source


def source_image_asset_or_raise(db: Session, *, tenant_id: str, asset_id: str) -> Asset:
    source_kind, source = source_asset_or_raise(db, tenant_id=tenant_id, asset_id=asset_id)
    if source_kind != "image":
        raise AppError(
            "Reverse prompt source must be a ready image asset.",
            code="REVERSE_PROMPT_SOURCE_INVALID",
            status_code=422,
        )
    return source


def fail_reverse_prompt_video_dispatch(
    db: Session,
    *,
    job: ReversePromptJob,
    message: str,
) -> None:
    quota.release_reverse_prompt_video_quota(
        db,
        tenant_id=job.tenant_id,
        reverse_prompt_job_id=job.id,
    )
    mark_reverse_prompt_job_failed(
        db,
        job=job,
        message=message,
        code="REVERSE_PROMPT_QUEUE_FAILED",
    )


def job_to_read(job: ReversePromptJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "source_kind": job.source_kind,
        "source_asset_id": job.source_asset_id,
        "target_format": job.target_format,
        "result": job.result_json,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "provider": job.provider,
        "model": job.model,
        "prompt_tokens": int(job.prompt_tokens or 0),
        "completion_tokens": int(job.completion_tokens or 0),
        "credits": float(job.credits or 0),
        "cost_cents": int(job.cost_cents or 0),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "saved_at": job.saved_at,
    }


def _invoke_reverse_provider(db: Session, *, tenant_id: str, image_url: str) -> Mapping[str, Any]:
    provider = resolve(db, tenant_id=tenant_id, capability="reverse_prompt")
    operation = provider.reverse_image({"image_url": image_url, "target_format": _TARGET_FORMAT})
    if inspect.isawaitable(operation):
        return asyncio.run(operation)
    return operation


def mark_reverse_prompt_job_succeeded(
    db: Session,
    *,
    job: ReversePromptJob,
    result: Mapping[str, Any],
) -> None:
    result_json = _result_payload(result)
    job.status = "succeeded"
    job.result_json = result_json
    job.raw_model_json = _dict_or_empty(result.get("raw_model_json"))
    job.provider = str(result.get("provider") or "apimart")
    job.model = str(result.get("model") or settings.engine_apimart_reverse_prompt_model)
    job.prompt_tokens = nonnegative_int(result.get("prompt_tokens"))
    job.completion_tokens = nonnegative_int(result.get("completion_tokens"))
    job.credits = Decimal(str(result.get("credits") or "0"))
    job.cost_cents = nonnegative_int(result.get("cost_cents"))
    job.updated_at = datetime.now(UTC)
    db.flush()


def mark_reverse_prompt_job_failed(
    db: Session,
    *,
    job: ReversePromptJob,
    message: str,
    code: str = "REVERSE_PROMPT_FAILED",
) -> None:
    job.status = "failed"
    job.error_code = code
    job.error_message = message
    job.updated_at = datetime.now(UTC)
    db.commit()


def _record_usage(
    db: Session,
    *,
    tenant_id: str,
    job: ReversePromptJob,
    result: Mapping[str, Any],
) -> None:
    total_tokens = nonnegative_int(result.get("total_tokens")) or (
        nonnegative_int(result.get("prompt_tokens"))
        + nonnegative_int(result.get("completion_tokens"))
    )
    quota.charge_reverse_prompt_quota(
        db,
        tenant_id=tenant_id,
        provider=str(result.get("provider") or "apimart"),
        model=str(result.get("model") or settings.engine_apimart_reverse_prompt_model),
        total_tokens=total_tokens,
        cost_cents=nonnegative_int(result.get("cost_cents")),
    )


def _result_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "target_format": "seedance_2_0",
        "prompt_zh": _clean_text(result.get("prompt_zh")),
        "prompt_en": _clean_text(result.get("prompt_en")),
        "negative_prompt": _clean_text(result.get("negative_prompt")),
        "style_tags": _clean_list(result.get("style_tags")),
        "camera": _clean_text(result.get("camera")),
        "lighting": _clean_text(result.get("lighting")),
        "composition": _clean_text(result.get("composition")),
        "subject": _clean_text(result.get("subject")),
        "scene": _clean_text(result.get("scene")),
        "motion_hint": _clean_text(result.get("motion_hint")),
        "selling_points": _clean_list(result.get("selling_points")),
        "text_in_media": _clean_list(result.get("text_in_media")),
        "disclaimer": _clean_text(result.get("disclaimer")),
        "confidence": _confidence(result.get("confidence")),
        "video_analysis": (
            dict(result["video_analysis"])
            if isinstance(result.get("video_analysis"), Mapping)
            else None
        ),
    }
    payload["fill_targets"] = fill_targets(payload)
    # Normalize nested models before storing so provider-only fields never leak to the API.
    validated = ReversePromptResult.model_validate(payload)
    return validated.model_dump(mode="json")


def fill_targets(result: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    prompt_zh = _clean_text(result.get("prompt_zh"))
    prompt_en = _clean_text(result.get("prompt_en")) or prompt_zh
    seedance_prompt = _seedance_prompt(result)
    topic = _short_text(prompt_zh or prompt_en, 80)
    selling = "; ".join(_clean_list(result.get("selling_points")))
    poster_title = _short_text(topic, 30)
    poster_subtitle = _short_text(selling or _clean_text(result.get("scene")), 40)
    return {
        "avatar_talk": {"topic": topic, "script": _short_text(prompt_zh or prompt_en, 500)},
        "seedance_i2v": {"topic": topic, "scene_prompt": seedance_prompt},
        "video_gen": {"topic": topic, "prompt": prompt_en},
        "photo": {"topic": seedance_prompt},
        "ecom_model": {"extra_prompt": _short_text(seedance_prompt, 200)},
        "ecom_poster": {"title": poster_title, "subtitle": poster_subtitle},
    }


def _seedance_prompt(result: Mapping[str, Any]) -> str:
    parts = [
        result.get("subject"),
        result.get("scene"),
        result.get("composition"),
        result.get("camera"),
        result.get("lighting"),
        result.get("motion_hint"),
        ", ".join(_clean_list(result.get("style_tags"))),
    ]
    text = ", ".join(_clean_text(part) for part in parts if _clean_text(part))
    return text or _clean_text(result.get("prompt_en")) or _clean_text(result.get("prompt_zh"))


def _dict_or_empty(value: Any) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clean_text(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    return "" if text.casefold() in _EMPTY_TEXT_PLACEHOLDERS else text


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple):
        return []
    return [_clean_text(item) for item in value if _clean_text(item)]


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _short_text(value: str, limit: int) -> str:
    text = _clean_text(value)
    return text[:limit]
