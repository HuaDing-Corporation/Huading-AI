from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.image_aspect_ratio import IMAGE_ASPECT_RATIOS
from app.db.models import Asset, ReversePromptJob, User
from app.providers.base import resolve
from app.schemas.reverse_prompt import (
    ReversePromptClearResponse,
    ReversePromptClearScope,
    ReversePromptEstimateResponse,
    ReversePromptHistoryItem,
    ReversePromptHistoryListResponse,
    ReversePromptResult,
    ReversePromptSourceKind,
)
from app.services import quota
from app.services.storage.base import ObjectStorage
from app.services.storage.keys import (
    is_tenant_storage_key,
    presign_tenant_storage_key,
)

_TARGET_FORMAT = "seedance_2_0"
_SOURCE_IMAGE_TYPES = {"avatar_image", "product_image", "generated_image", "cover"}
_SOURCE_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_EMPTY_TEXT_PLACEHOLDERS = {"none", "n/a", "na", "null", "nil"}
_SEEDANCE_I2V_ASPECT_RATIOS = ("9:16", "16:9", "1:1")
_VIDEO_GEN_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
_VIDEO_GEN_PROMPT_LIMIT = 2_000
_PHOTO_PROMPT_LIMIT = 20_000
_ECOM_MODEL_PROMPT_LIMIT = 20_000
_JOB_PROGRESS_KEY = "_job_progress"
_STRUCTURED_ZH_FALLBACK_PREFIX = "[中文缺失，以下为英文原文]"


def live_reverse_prompt_job_condition():
    return ReversePromptJob.deleted_at.is_(None)


def select_live_reverse_prompt_jobs(*conditions):
    return select(ReversePromptJob).where(
        live_reverse_prompt_job_condition(),
        *conditions,
    )


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
            image_url=presign_tenant_storage_key(
                storage,
                tenant_id=user.tenant_id,
                storage_key=source.storage_key,
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


def estimate_reverse_prompt(
    db: Session,
    *,
    tenant_id: str,
    source_asset_id: str,
) -> ReversePromptEstimateResponse:
    source_kind, source = source_asset_or_raise(
        db,
        tenant_id=tenant_id,
        asset_id=source_asset_id,
    )
    if source_kind == "image":
        estimate = quota.estimate_reverse_prompt_quota(db, tenant_id=tenant_id)
        return ReversePromptEstimateResponse(
            credits=estimate.reservation_units,
            duration_sec=None,
            tier="image",
        )
    duration_ms = int(source.duration_ms or 0)
    tier = quota.reverse_prompt_video_tier(duration_ms)
    estimate = quota.estimate_reverse_prompt_video_quota(
        db,
        tenant_id=tenant_id,
        duration_ms=duration_ms,
    )
    return ReversePromptEstimateResponse(
        credits=estimate.reservation_units,
        duration_sec=round(duration_ms / 1000.0, 3),
        tier=tier,
    )


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
            image_url=presign_tenant_storage_key(
                storage,
                tenant_id=user.tenant_id,
                storage_key=job.source_storage_key,
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


def list_reverse_prompt_jobs(
    db: Session,
    *,
    tenant_id: str,
    source_kind: ReversePromptSourceKind | None,
    page: int,
    page_size: int,
    storage: ObjectStorage,
) -> ReversePromptHistoryListResponse:
    filters = [
        ReversePromptJob.tenant_id == tenant_id,
        live_reverse_prompt_job_condition(),
    ]
    if source_kind is not None:
        filters.append(ReversePromptJob.source_kind == source_kind)
    total = int(
        db.scalar(select(func.count()).select_from(ReversePromptJob).where(*filters)) or 0
    )
    jobs = list(
        db.scalars(
            select(ReversePromptJob)
            .where(*filters)
            .order_by(ReversePromptJob.created_at.desc(), ReversePromptJob.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return ReversePromptHistoryListResponse(
        items=[
            ReversePromptHistoryItem(
                id=job.id,
                source_kind=job.source_kind,
                status=job.status,
                created_at=job.created_at,
                source_thumbnail_url=_history_source_thumbnail_url(
                    job,
                    tenant_id=tenant_id,
                    storage=storage,
                ),
                summary=_history_summary(job.result_json),
            )
            for job in jobs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def _history_source_thumbnail_url(
    job: ReversePromptJob,
    *,
    tenant_id: str,
    storage: ObjectStorage,
) -> str | None:
    if job.source_kind != "image":
        return None
    storage_key = str(job.source_storage_key or "")
    if not _is_safe_tenant_storage_key(tenant_id, storage_key):
        return None
    return presign_tenant_storage_key(
        storage,
        tenant_id=tenant_id,
        storage_key=storage_key,
        expires_in=settings.engine_s3_presign_ttl,
    )


def _history_summary(result_json: Mapping[str, object] | None) -> str | None:
    if not isinstance(result_json, Mapping):
        return None
    for key in ("prompt_zh", "subject", "prompt_en"):
        value = str(result_json.get(key) or "").strip()
        if value:
            return value[:160]
    return None


def _is_safe_tenant_storage_key(tenant_id: str, storage_key: str) -> bool:
    return is_tenant_storage_key(tenant_id, storage_key)


def delete_reverse_prompt_job(
    db: Session,
    *,
    tenant_id: str,
    job_id: str,
) -> ReversePromptJob:
    job = reverse_prompt_job_or_404(db, tenant_id=tenant_id, job_id=job_id)
    deleted_at = datetime.now(UTC)
    job.deleted_at = deleted_at
    job.updated_at = deleted_at
    db.commit()
    db.refresh(job)
    return job


def clear_reverse_prompt_jobs(
    db: Session,
    *,
    tenant_id: str,
    scope: ReversePromptClearScope,
) -> ReversePromptClearResponse:
    """Clear the explicit scope; `all` always includes both image and video jobs."""
    filters = [
        ReversePromptJob.tenant_id == tenant_id,
        live_reverse_prompt_job_condition(),
    ]
    if scope != "all":
        filters.append(ReversePromptJob.source_kind == scope)

    result = db.execute(
        update(ReversePromptJob)
        .where(*filters)
        .values(deleted_at=datetime.now(UTC))
    )
    deleted_count = int(result.rowcount or 0)
    db.commit()
    return ReversePromptClearResponse(deleted_count=deleted_count)


def reverse_prompt_job_or_404(db: Session, *, tenant_id: str, job_id: str) -> ReversePromptJob:
    job = db.scalar(
        select_live_reverse_prompt_jobs(
            ReversePromptJob.id == job_id,
        )
    )
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
        select_live_reverse_prompt_jobs(
            ReversePromptJob.id == job_id,
            ReversePromptJob.tenant_id == tenant_id,
        )
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
        and 1_000 <= source.duration_ms <= 180_000
    ):
        source_kind = "video"
    else:
        raise AppError(
            "Reverse prompt source must be a supported ready image or video asset.",
            code="REVERSE_PROMPT_SOURCE_INVALID",
            status_code=422,
        )
    if not _is_safe_tenant_storage_key(tenant_id, source.storage_key):
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
    progress = _job_progress(job)
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
        "segments_total": progress.get("segments_total") if progress else None,
        "segments_done": progress.get("segments_done") if progress else None,
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
    source = db.get(Asset, job.source_asset_id) if job.source_asset_id else None
    if source is not None and source.tenant_id != job.tenant_id:
        source = None
    result_json = _result_payload(
        result,
        source=source,
        source_kind=job.source_kind,
    )
    job.status = "succeeded"
    job.result_json = result_json
    raw_model_json = _dict_or_empty(result.get("raw_model_json"))
    progress = _job_progress(job)
    if progress is not None:
        raw_model_json[_JOB_PROGRESS_KEY] = progress
    job.raw_model_json = raw_model_json
    job.provider = str(result.get("provider") or "apimart")
    job.model = str(result.get("model") or settings.engine_apimart_reverse_prompt_model)
    job.prompt_tokens = nonnegative_int(result.get("prompt_tokens"))
    job.completion_tokens = nonnegative_int(result.get("completion_tokens"))
    job.credits = Decimal(str(result.get("credits") or "0"))
    job.cost_cents = nonnegative_int(result.get("cost_cents"))
    job.updated_at = datetime.now(UTC)
    db.flush()


def set_reverse_prompt_segment_progress(
    db: Session,
    *,
    job: ReversePromptJob,
    segments_total: int,
    segments_done: int,
) -> None:
    raw_model_json = _dict_or_empty(job.raw_model_json)
    raw_model_json[_JOB_PROGRESS_KEY] = {
        "segments_total": max(1, int(segments_total)),
        "segments_done": max(0, min(int(segments_done), int(segments_total))),
    }
    job.raw_model_json = raw_model_json
    job.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(job)


def _job_progress(job: ReversePromptJob) -> dict[str, int] | None:
    raw_model_json = job.raw_model_json
    if not isinstance(raw_model_json, Mapping):
        return None
    raw_progress = raw_model_json.get(_JOB_PROGRESS_KEY)
    if not isinstance(raw_progress, Mapping):
        return None
    total = nonnegative_int(raw_progress.get("segments_total"))
    done = nonnegative_int(raw_progress.get("segments_done"))
    if total <= 0:
        return None
    return {
        "segments_total": total,
        "segments_done": min(done, total),
    }


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


def _result_payload(
    result: Mapping[str, Any],
    *,
    source: Asset | None,
    source_kind: str,
) -> dict[str, Any]:
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
    payload["source_media"] = _source_media_payload(source, source_kind=source_kind)
    structured_source = {
        **payload,
        "structured_fields_zh": result.get("structured_fields_zh"),
    }
    payload["structured_prompt"] = structured_prompt(structured_source)
    payload["fill_targets"] = fill_targets(payload)
    # Normalize nested models before storing so provider-only fields never leak to the API.
    validated = ReversePromptResult.model_validate(payload)
    return validated.model_dump(mode="json")


def fill_targets(result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    prompt_zh = _clean_text(result.get("prompt_zh"))
    prompt_en = _clean_text(result.get("prompt_en")) or prompt_zh
    structured = result.get("structured_prompt")
    structured_en = (
        _clean_multiline_text(structured.get("en"))
        if isinstance(structured, Mapping)
        else structured_prompt(result)["en"]
    )
    topic = _short_text(prompt_zh or prompt_en, 80)
    source_media = result.get("source_media")
    width = _positive_int(source_media.get("width")) if isinstance(source_media, Mapping) else None
    height = (
        _positive_int(source_media.get("height")) if isinstance(source_media, Mapping) else None
    )
    duration_sec = (
        _positive_float(source_media.get("duration_sec"))
        if isinstance(source_media, Mapping)
        else None
    )
    seedance_duration, seedance_clamped = _clamped_duration(
        duration_sec,
        minimum=5,
        maximum=120,
    )
    video_gen_duration, video_gen_clamped = _clamped_duration(
        duration_sec,
        minimum=4,
        maximum=15,
    )
    video_analysis = result.get("video_analysis")
    transcript = (
        _clean_text(video_analysis.get("audio_transcript"))
        if isinstance(video_analysis, Mapping)
        else ""
    )
    shot_section = _shot_section(video_analysis)
    return {
        "avatar_talk": {"topic": topic, "script": _short_text(prompt_zh or prompt_en, 500)},
        "seedance_i2v": {
            "topic": topic,
            "script": transcript or None,
            "scene_prompt": _whole_sections_within_limit(
                structured_en,
                _PHOTO_PROMPT_LIMIT,
            ),
            "negative_prompt": _clean_text(result.get("negative_prompt")),
            "aspect_ratio": _closest_supported_aspect_ratio(
                width,
                height,
                _SEEDANCE_I2V_ASPECT_RATIOS,
            ),
            "duration_sec": seedance_duration,
            "duration_clamped": seedance_clamped,
            "shot_section": shot_section,
        },
        "video_gen": {
            "topic": topic,
            "prompt": _whole_sections_within_limit(
                structured_en,
                _VIDEO_GEN_PROMPT_LIMIT,
            ),
            "negative_prompt": _clean_text(result.get("negative_prompt")),
            "aspect_ratio": _closest_supported_aspect_ratio(
                width,
                height,
                _VIDEO_GEN_ASPECT_RATIOS,
            ),
            "duration_sec": video_gen_duration,
            "duration_clamped": video_gen_clamped,
            "generate_audio": bool(transcript),
            "shot_section": shot_section,
        },
        "photo": {
            "topic": _whole_sections_within_limit(structured_en, _PHOTO_PROMPT_LIMIT),
            "master_prompt": None,
            "negative_prompt": _clean_text(result.get("negative_prompt")),
            "aspect_ratio": _closest_supported_aspect_ratio(
                width,
                height,
                IMAGE_ASPECT_RATIOS,
            ),
        },
        "ecom_model": {
            "extra_prompt": _whole_sections_within_limit(
                structured_en,
                _ECOM_MODEL_PROMPT_LIMIT,
            ),
            "aspect_ratio": _closest_supported_aspect_ratio(
                width,
                height,
                IMAGE_ASPECT_RATIOS,
            ),
        },
    }


def structured_prompt(result: Mapping[str, Any]) -> dict[str, str]:
    style = ", ".join(_clean_list(result.get("style_tags")))
    raw_zh_fields = result.get("structured_fields_zh")
    zh_fields = raw_zh_fields if isinstance(raw_zh_fields, Mapping) else {}
    sections = [
        ("Subject", "主体", "subject", _clean_text(result.get("subject"))),
        ("Scene", "场景", "scene", _clean_text(result.get("scene"))),
        (
            "Composition",
            "构图",
            "composition",
            _clean_text(result.get("composition")),
        ),
        ("Camera", "镜头", "camera", _clean_text(result.get("camera"))),
        ("Lighting", "光线", "lighting", _clean_text(result.get("lighting"))),
        ("Motion", "运动", "motion", _clean_text(result.get("motion_hint"))),
        ("Style", "风格", "style", style),
    ]
    return {
        "en": "\n".join(
            f"{label}: {english_value}"
            for label, _, _, english_value in sections
        ),
        "zh": "\n".join(
            f"{label}: {_structured_zh_value(zh_fields.get(key), english_value)}"
            for _, label, key, english_value in sections
        ),
    }


def _structured_zh_value(value: Any, english_value: str) -> str:
    localized = _clean_text(value)
    if localized and _contains_han(localized):
        return localized
    return f"{_STRUCTURED_ZH_FALLBACK_PREFIX} {english_value}".rstrip()


def _contains_han(value: str) -> bool:
    return any(
        "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
        for character in value
    )


def _source_media_payload(source: Asset | None, *, source_kind: str) -> dict[str, Any]:
    width = _positive_int(source.width) if source is not None else None
    height = _positive_int(source.height) if source is not None else None
    duration_sec = (
        round(float(source.duration_ms) / 1000.0, 3)
        if source_kind == "video" and source is not None and source.duration_ms
        else None
    )
    return {
        "kind": source_kind,
        "width": width,
        "height": height,
        "duration_sec": duration_sec,
        "aspect_ratio_raw": _raw_aspect_ratio(width, height),
    }


def _raw_aspect_ratio(width: int | None, height: int | None) -> str | None:
    if width is None or height is None:
        return None
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _closest_supported_aspect_ratio(
    width: int | None,
    height: int | None,
    allowed: tuple[str, ...],
) -> str | None:
    if width is None or height is None:
        return None
    actual = width / height
    return min(
        allowed,
        key=lambda ratio: abs(
            math.log(
                actual
                / (
                    int(ratio.split(":", 1)[0])
                    / int(ratio.split(":", 1)[1])
                )
            )
        ),
    )


def _clamped_duration(
    duration_sec: float | None,
    *,
    minimum: int,
    maximum: int,
) -> tuple[int | None, bool]:
    if duration_sec is None:
        return None, False
    rounded = int(round(duration_sec))
    clamped = max(minimum, min(maximum, rounded))
    return clamped, not math.isclose(float(clamped), duration_sec)


def _shot_section(video_analysis: Any) -> str | None:
    if not isinstance(video_analysis, Mapping):
        return None
    summary = _clean_text(video_analysis.get("shot_summary"))
    return f"Shots:\n{summary}" if summary else None


def _whole_sections_within_limit(value: str, limit: int) -> str:
    text = _clean_multiline_text(value)
    if len(text) <= limit:
        return text
    selected: list[str] = []
    length = 0
    for section in text.splitlines():
        added = len(section) + (1 if selected else 0)
        if length + added <= limit:
            selected.append(section)
            length += added
    return "\n".join(selected)


def _clean_multiline_text(value: Any) -> str:
    return "\n".join(
        cleaned
        for line in str(value or "").strip().splitlines()
        if (cleaned := " ".join(line.split()))
    )


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


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
