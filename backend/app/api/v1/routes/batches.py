from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DbSessionDependency, get_object_storage, require_permission
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.models import Asset, BatchJob, TaskAsset, User, VideoTask
from app.schemas.batches import (
    BatchCancelResponse,
    BatchCreateResponse,
    BatchDetailResponse,
    BatchEstimateResponse,
    BatchListResponse,
    BatchRequest,
    BatchSummary,
    BatchTaskRead,
)
from app.schemas.response import ApiResponse, ok
from app.services.batches import (
    BatchImageDownloadError,
    PromptBatchRow,
    batch_row_index,
    bgm_asset_or_track_or_raise,
    download_image_url_to_asset,
    ecom_rows_or_raise,
    ecom_topic,
    estimate_batch,
    image_asset_or_raise,
    output_video_url,
    prompt_rows_or_raise,
    refresh_batch_job,
    tenant_relative_upload_key,
    validate_voice_or_raise,
    video_gen_reference_assets_or_raise,
)
from app.services.plan_access import (
    require_doubao_voice_clone_access,
    uses_doubao_voice_clone,
)
from app.services.quota import (
    release_reserved_quota,
    reserve_seedance_i2v_quota,
    reserve_video_gen_quota,
    seedance_i2v_billable_seconds,
    seedance_i2v_target_seconds,
)
from app.services.storage.base import ObjectStorage
from app.workers.avatar_talk import generate_seedance_i2v_task
from app.workers.video_gen import generate_video_gen_task

router = APIRouter()
logger = get_logger(__name__)
BatchPermissionDependency = Depends(require_permission("video:create"))
ObjectStorageDependency = Depends(get_object_storage)
_URL_PATTERN = re.compile(r"(https?)://([^/\s?#]+)[^\s]*")


def _insufficient_credits() -> AppError:
    return AppError(
        "Insufficient tenant quota for this batch.",
        code="INSUFFICIENT_CREDITS",
        status_code=422,
    )


def _summary(batch: BatchJob) -> BatchSummary:
    return BatchSummary(
        id=batch.id,
        kind=batch.kind,
        status=batch.status,
        total=int(batch.total or 0),
        succeeded=int(batch.succeeded or 0),
        failed=int(batch.failed or 0),
        common_params=dict(batch.common_params or {}),
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


@router.post("/estimate", response_model=ApiResponse[BatchEstimateResponse])
def estimate_batch_endpoint(
    request: Request,
    payload: BatchRequest,
    user: User = BatchPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[BatchEstimateResponse]:
    per_row, total_units, balance = estimate_batch(db, tenant_id=user.tenant_id, payload=payload)
    return ok(
        request,
        BatchEstimateResponse(
            total_rows=len(payload.rows),
            per_row_credits=per_row.reservation_units,
            total_credits=total_units,
            insufficient=balance < total_units,
            balance_credits=balance,
        ),
    )


@router.post(
    "",
    response_model=ApiResponse[BatchCreateResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_batch_endpoint(
    request: Request,
    payload: BatchRequest,
    user: User = BatchPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[BatchCreateResponse]:
    _per_row, total_units, balance = estimate_batch(db, tenant_id=user.tenant_id, payload=payload)
    if balance < total_units:
        raise _insufficient_credits()

    batch = BatchJob(
        id=str(uuid4()),
        tenant_id=user.tenant_id,
        user_id=user.id,
        kind=payload.kind,
        status="running",
        total=len(payload.rows),
        succeeded=0,
        failed=0,
        common_params=payload.common.model_dump(exclude_none=True),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(batch)
    db.flush()
    queued_payloads: list[tuple[str, dict[str, object], object]] = []
    task_ids: list[str] = []
    try:
        if payload.kind == "prompt_set":
            task_ids.extend(
                _create_prompt_set_tasks(
                    db,
                    user=user,
                    batch=batch,
                    payload=payload,
                    storage=storage,
                    queued_payloads=queued_payloads,
                )
            )
        else:
            task_ids.extend(
                _create_ecom_table_tasks(
                    db,
                    user=user,
                    batch=batch,
                    payload=payload,
                    storage=storage,
                    queued_payloads=queued_payloads,
                )
            )
        refresh_batch_job(db, batch_id=batch.id)
        db.commit()
    except AppError as exc:
        db.rollback()
        if exc.code == "TENANT_QUOTA_EXCEEDED":
            raise _insufficient_credits() from exc
        raise
    except Exception:
        db.rollback()
        raise

    for task_id, worker_payload, task in queued_payloads:
        task.apply_async(args=[worker_payload], task_id=task_id, queue="video")

    return ok(request, BatchCreateResponse(batch_id=batch.id, task_ids=task_ids))


def _create_prompt_set_tasks(
    db: Session,
    *,
    user: User,
    batch: BatchJob,
    payload: BatchRequest,
    storage: ObjectStorage,
    queued_payloads: list[tuple[str, dict[str, object], object]],
) -> list[str]:
    rows = prompt_rows_or_raise(payload)
    common = payload.common
    common_reference_assets: list[Asset] = []
    if any(row.image_asset_id is None for row in rows):
        common_reference_assets = video_gen_reference_assets_or_raise(
            db,
            tenant_id=user.tenant_id,
            asset_ids=list(common.reference_image_asset_ids),
        )
    bgm = common.bgm.model_dump(exclude_none=True) if common.bgm is not None else None
    bgm_asset = bgm_asset_or_track_or_raise(
        db,
        tenant_id=user.tenant_id,
        bgm=bgm,
        storage=storage,
    )
    task_ids: list[str] = []
    for index, row in enumerate(rows):
        task_id = str(uuid4())
        reference_assets = _prompt_reference_assets_for_row(
            db,
            tenant_id=user.tenant_id,
            row=row,
            row_index=index,
            common_reference_assets=common_reference_assets,
        )
        reference_asset_ids = [asset.id for asset in reference_assets]
        params: dict[str, object] = {
            "video_mode": "video_gen",
            "prompt": row.prompt,
            "topic": row.prompt,
            "reference_image_asset_ids": reference_asset_ids,
            "duration_sec": int(common.duration_sec or 5),
            "resolution": common.resolution,
            "apply_visible_label": common.apply_visible_label,
            "batch_id": batch.id,
            "batch_row_index": index,
        }
        if bgm is not None:
            params["bgm"] = bgm
        task = VideoTask(
            id=task_id,
            tenant_id=user.tenant_id,
            created_by_user_id=user.id,
            status="queued",
            topic=row.prompt,
            mode="video_gen",
            video_mode="video_gen",
            progress=0,
            aspect_ratio=common.aspect_ratio,
            duration_sec=float(common.duration_sec or 5),
            batch_id=batch.id,
            params=params,
        )
        db.add(task)
        db.flush()
        for asset in reference_assets:
            db.add(
                TaskAsset(
                    video_task_id=task.id,
                    asset_id=asset.id,
                    role="input_reference_image",
                )
            )
        if bgm_asset is not None:
            db.add(TaskAsset(video_task_id=task.id, asset_id=bgm_asset.id, role="input_bgm"))
        reserve_video_gen_quota(
            db,
            tenant_id=user.tenant_id,
            video_task_id=task.id,
            duration_sec=int(common.duration_sec or 5),
            resolution=common.resolution,
        )
        worker_payload = dict(params)
        worker_payload["tenant_id"] = user.tenant_id
        worker_payload["video_task_id"] = task.id
        queued_payloads.append((task.id, worker_payload, generate_video_gen_task))
        task_ids.append(task.id)
    return task_ids


def _prompt_reference_assets_for_row(
    db: Session,
    *,
    tenant_id: str,
    row: PromptBatchRow,
    row_index: int,
    common_reference_assets: list[Asset],
) -> list[Asset]:
    if not row.image_asset_id:
        return common_reference_assets
    try:
        return video_gen_reference_assets_or_raise(
            db,
            tenant_id=tenant_id,
            asset_ids=[row.image_asset_id],
        )
    except AppError as exc:
        if exc.code == "REFERENCE_IMAGE_NOT_FOUND":
            raise AppError(
                f"Invalid batch row {row_index}: image_asset_id not found.",
                code="BATCH_ROW_INVALID",
                status_code=422,
            ) from exc
        raise


def _create_ecom_table_tasks(
    db: Session,
    *,
    user: User,
    batch: BatchJob,
    payload: BatchRequest,
    storage: ObjectStorage,
    queued_payloads: list[tuple[str, dict[str, object], object]],
) -> list[str]:
    rows = ecom_rows_or_raise(payload)
    common = payload.common
    voice, brand_voice = validate_voice_or_raise(
        db,
        tenant_id=user.tenant_id,
        voice_id=common.voice_id,
    )
    if brand_voice is not None and uses_doubao_voice_clone(brand_voice.provider):
        require_doubao_voice_clone_access(
            db,
            tenant_id=user.tenant_id,
        )
    brand_voice_params: dict[str, object] = {}
    if brand_voice is not None:
        brand_voice_params = {
            "voice_source": "brand_voice",
            "brand_voice_id": brand_voice.id,
            "tts_speaker_id": brand_voice.speaker_id,
            "brand_voice_provider": brand_voice.provider,
        }
    target_duration_sec = seedance_i2v_target_seconds(common.duration_sec)
    task_ids: list[str] = []
    for index, row in enumerate(rows):
        task_id = str(uuid4())
        row_topic = ecom_topic(row)
        try:
            source_asset = (
                image_asset_or_raise(db, tenant_id=user.tenant_id, asset_id=row.image_asset_id)
                if row.image_asset_id
                else download_image_url_to_asset(
                    db,
                    tenant_id=user.tenant_id,
                    image_url=str(row.image_url),
                    storage=storage,
                )
            )
            image_key = tenant_relative_upload_key(source_asset, tenant_id=user.tenant_id)
        except Exception as exc:
            error_message = _batch_image_download_message(index, exc)
            logger.warning(
                "batch_image_download_failed",
                batch_id=batch.id,
                tenant_id=user.tenant_id,
                row_index=index,
                image_url_host=urlparse(str(row.image_url or "")).hostname,
                error=_redact_urls(str(exc)),
            )
            failed = VideoTask(
                id=task_id,
                tenant_id=user.tenant_id,
                created_by_user_id=user.id,
                status="failed",
                error=error_message,
                error_code="BATCH_IMAGE_DOWNLOAD_FAILED",
                error_message=error_message,
                topic=row_topic,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                progress=100,
                voice_id=voice.id if voice is not None else None,
                brand_voice_id=brand_voice.id if brand_voice is not None else None,
                aspect_ratio=common.aspect_ratio,
                subtitle_enabled=common.subtitle_enabled,
                duration_sec=target_duration_sec,
                batch_id=batch.id,
                params={
                    "batch_id": batch.id,
                    "batch_row_index": index,
                    "resolution": common.resolution,
                    **brand_voice_params,
                },
            )
            db.add(failed)
            db.flush()
            task_ids.append(failed.id)
            continue

        params: dict[str, object] = {
            "image_key": image_key,
            "scene_prompt": row_topic,
            "duration_sec": target_duration_sec,
            "resolution": common.resolution,
            "speed": common.speed,
            "estimated": True,
            "apply_visible_label": common.apply_visible_label,
            "batch_id": batch.id,
            "batch_row_index": index,
            "source_asset_id": source_asset.id,
            **brand_voice_params,
        }
        task = VideoTask(
            id=task_id,
            tenant_id=user.tenant_id,
            created_by_user_id=user.id,
            status="queued",
            topic=row_topic,
            mode="seedance_i2v",
            video_mode="seedance_i2v",
            progress=0,
            voice_id=voice.id if voice is not None else None,
            brand_voice_id=brand_voice.id if brand_voice is not None else None,
            aspect_ratio=common.aspect_ratio,
            subtitle_enabled=common.subtitle_enabled,
            duration_sec=target_duration_sec,
            batch_id=batch.id,
            params=params,
        )
        db.add(task)
        db.flush()
        db.add(
            TaskAsset(
                video_task_id=task.id,
                asset_id=source_asset.id,
                role="input_reference_image",
            )
        )
        reserve_seedance_i2v_quota(
            db,
            tenant_id=user.tenant_id,
            video_task_id=task.id,
            script=row_topic,
            speed=common.speed,
            estimated_seconds=seedance_i2v_billable_seconds(target_duration_sec),
            resolution=common.resolution,
        )
        worker_payload = dict(params)
        worker_payload.update(
            {
                "tenant_id": user.tenant_id,
                "video_task_id": task.id,
                "topic": row_topic,
                "video_mode": "seedance_i2v",
                "voice_id": common.voice_id,
            }
        )
        queued_payloads.append((task.id, worker_payload, generate_seedance_i2v_task))
        task_ids.append(task.id)
    return task_ids


def _batch_image_download_message(index: int, exc: Exception) -> str:
    reason = exc.reason if isinstance(exc, BatchImageDownloadError) else "下载失败"
    return f"参考图下载失败(第{index + 1}行): {reason}"


def _redact_urls(value: str) -> str:
    return _URL_PATTERN.sub(
        lambda match: f"{match.group(1)}://{match.group(2)}/[redacted]",
        value,
    )


@router.get("", response_model=ApiResponse[BatchListResponse])
def list_batches(
    request: Request,
    user: User = BatchPermissionDependency,
    db: Session = DbSessionDependency,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[BatchListResponse]:
    query = select(BatchJob).where(BatchJob.tenant_id == user.tenant_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    batches = list(
        db.scalars(query.order_by(BatchJob.created_at.desc()).offset(offset).limit(limit))
    )
    return ok(request, BatchListResponse(items=[_summary(batch) for batch in batches], total=total))


@router.get("/{batch_id}", response_model=ApiResponse[BatchDetailResponse])
def get_batch(
    request: Request,
    batch_id: str,
    user: User = BatchPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[BatchDetailResponse]:
    batch = db.get(BatchJob, batch_id)
    if batch is None or batch.tenant_id != user.tenant_id:
        raise AppError("Batch not found.", code="BATCH_NOT_FOUND", status_code=404)
    tasks = list(
        db.scalars(
            select(VideoTask)
            .where(VideoTask.batch_id == batch.id, VideoTask.tenant_id == user.tenant_id)
            .order_by(VideoTask.created_at.asc(), VideoTask.id.asc())
        )
    )
    task_items = [
        BatchTaskRead(
            task_id=task.id,
            row_index=batch_row_index(task),
            status=task.status,
            video_url=output_video_url(task, storage=storage),
            error=task.error,
            error_code=task.error_code,
            error_message=task.error_message,
        )
        for task in sorted(tasks, key=batch_row_index)
    ]
    return ok(request, BatchDetailResponse(batch=_summary(batch), tasks=task_items))


@router.post("/{batch_id}/cancel", response_model=ApiResponse[BatchCancelResponse])
def cancel_batch(
    request: Request,
    batch_id: str,
    user: User = BatchPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[BatchCancelResponse]:
    batch = db.get(BatchJob, batch_id)
    if batch is None or batch.tenant_id != user.tenant_id:
        raise AppError("Batch not found.", code="BATCH_NOT_FOUND", status_code=404)
    tasks = list(
        db.scalars(
            select(VideoTask).where(
                VideoTask.batch_id == batch.id,
                VideoTask.tenant_id == user.tenant_id,
            )
        )
    )
    cancelled = 0
    running = 0
    for task in tasks:
        if task.status == "queued":
            task.status = "cancelled"
            task.error_code = "BATCH_CANCELLED"
            task.error_message = "Batch cancelled before this task started."
            task.error = task.error_message
            task.finished_at = datetime.now(UTC)
            release_reserved_quota(db, tenant_id=user.tenant_id, video_task_id=task.id)
            cancelled += 1
        elif task.status == "running":
            running += 1
    refresh_batch_job(db, batch_id=batch.id)
    db.commit()
    return ok(
        request,
        BatchCancelResponse(batch_id=batch.id, cancelled=cancelled, running=running),
    )
