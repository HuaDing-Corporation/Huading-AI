from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import uniform
from time import sleep
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.models import (
    Asset,
    BillingOperation,
    CreditRefundGrant,
    EcomReplicateJob,
    EcomReplicateOutput,
    ReversePromptJob,
    TaskAsset,
    Tenant,
    VideoTask,
)
from app.db.session import SessionLocal
from app.providers.base import ProviderResolutionError, resolve_named_provider
from app.providers.voice_clone.cosyvoice import _request_recovery_marker
from app.services import ecom_replicate, quota
from app.services.aibrain import recover_stale_reasoning_reservations
from app.services.batches import refresh_batch_job
from app.services.billing_operations import (
    BillingInvariantError,
    _subscription_ids_for_operation,
    _usage_for_update,
    _validate_stored_result,
    complete_failed,
)
from app.services.cosyvoice_recovery import reconcile_cosyvoice_operation
from app.services.ecom_billing import try_finalize_ecom_operation
from app.services.quota import (
    recover_stale_copy_quota_reservations,
    release_reserved_quota,
    release_reverse_prompt_video_quota,
)
from app.services.transaction_retry import _RETRYABLE_SQLSTATES, _sqlstate

logger = get_logger(__name__)

_IMAGE_WORKER_LOST_MESSAGE = "Image generation worker stopped before completion."
_VIDEO_GEN_WORKER_LOST_MESSAGE = "Video generation worker stopped before completion."
_REVERSE_WORKER_LOST_MESSAGE = "Video reverse-prompt worker stopped before completion."
_REPLICATE_WORKER_LOST_MESSAGE = (
    "E-commerce replicate worker stopped before completion."
)
_VIDEO_TASK_RECOVERY_ERRORS = {
    "photo": ("IMAGE_GEN_FAILED", _IMAGE_WORKER_LOST_MESSAGE),
    "video_gen": ("VIDEO_GEN_FAILED", _VIDEO_GEN_WORKER_LOST_MESSAGE),
}
_MANUAL_DOUBAO_OPERATIONS = {
    "doubao_brand_voice_order_create",
    "doubao_brand_voice_order_renew",
}
_ECOM_OPERATIONS = {"ecom_cutout", "ecom_model"}
_SYNCHRONOUS_AUTOMATIC_OPERATIONS = {"script_generate", "scene_prompt"}


@dataclass(frozen=True)
class ImageQueueRecoveryResult:
    photo_tasks: int = 0
    video_gen_tasks: int = 0
    reverse_prompt_jobs: int = 0
    ecom_replicate_jobs: int = 0
    aibrain_reservations: int = 0
    copy_reservations: int = 0
    billing_operations_released: int = 0
    billing_operations_settled: int = 0


@dataclass(frozen=True)
class RecoverySummary:
    """Terminal outcomes for automatic billing reservations only."""

    released_operation_ids: tuple[str, ...] = ()
    settled_operation_ids: tuple[str, ...] = ()
    exempt_manual_order_ids: tuple[str, ...] = ()
    held_operation_ids: tuple[str, ...] = ()


def _billing_task_timeout_seconds(operation: BillingOperation, task: VideoTask | None) -> float:
    """Return the supplier timeout that owns this persisted automatic task."""
    if operation.operation in _ECOM_OPERATIONS:
        return settings.engine_apimart_timeout_seconds
    if operation.operation != "video_create" or task is None:
        return settings.engine_orphan_task_stale_seconds
    if task.video_mode in {"avatar_talk", "avatar"}:
        return settings.engine_omnihuman_timeout_seconds
    if task.video_mode in {"seedance_t2v", "seedance_i2v", "video_gen"}:
        return max(
            settings.engine_seedance_timeout_seconds,
            settings.engine_apimart_video_timeout_seconds,
        )
    if task.video_mode == "photo":
        return settings.engine_image_provider_timeout_seconds
    return settings.engine_orphan_task_stale_seconds


def _operation_tasks_for_update(
    db: Session,
    *,
    operation_id: str,
) -> list[VideoTask]:
    return list(
        db.scalars(
            select(VideoTask)
            .where(VideoTask.params["billing_operation_id"].as_string() == operation_id)
            .order_by(VideoTask.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def _task_is_stale(task: VideoTask, *, timeout_seconds: float, now: datetime) -> bool:
    changed_at = task.updated_at or task.created_at
    return _timestamp_is_stale(changed_at, timeout_seconds=timeout_seconds, now=now)


def _timestamp_is_stale(
    changed_at: datetime,
    *,
    timeout_seconds: float,
    now: datetime,
) -> bool:
    if changed_at.tzinfo is None:
        changed_at = changed_at.replace(tzinfo=UTC)
    return changed_at <= now - timedelta(seconds=timeout_seconds)


def _release_stale_operation(db: Session, *, operation_id: str) -> None:
    complete_failed(
        db,
        operation_id=operation_id,
        code="BILLING_OPERATION_STALE",
        http_status=504,
        sanitized_detail=None,
    )


def _fail_closed_video_recovery(
    db: Session,
    *,
    operation_id: str,
    task: VideoTask,
) -> None:
    """Atomically revoke a delivered-looking output when settlement evidence is invalid."""
    from app.workers.avatar_talk import _hide_billed_video_output

    _hide_billed_video_output(db, task=task, mark_task_failed=True)
    _release_stale_operation(db, operation_id=operation_id)


def _has_persisted_video_deliverable(db: Session, *, task: VideoTask) -> bool:
    if not task.storage_key:
        return False
    return (
        db.scalar(
            select(TaskAsset.id)
            .join(Asset, Asset.id == TaskAsset.asset_id)
            .where(
                TaskAsset.video_task_id == task.id,
                TaskAsset.role == "output_video",
                Asset.status == "ready",
                Asset.deleted_at.is_(None),
                Asset.storage_key == task.storage_key,
            )
        )
        is not None
    )


def _recover_stale_billing_operations_once(
    db: Session,
    *,
    now: datetime,
    operation_id: str | None = None,
) -> RecoverySummary:
    """Recover only stale, automatic reservations from their durable task state.

    Candidate IDs are captured and locked in one global order. The state is then
    read again under the row lock so repeated workers cannot double-settle or
    release a reservation that became valid meanwhile. Manual Doubao orders are
    excluded before any generic recovery action.
    """
    if now.tzinfo is None:
        raise ValueError("recovery timestamp must be timezone-aware")
    candidate_statement = (
        select(BillingOperation.id, BillingOperation.tenant_id)
        .where(BillingOperation.status == "in_progress")
        .order_by(BillingOperation.id)
    )
    if operation_id is not None:
        candidate_statement = candidate_statement.where(BillingOperation.id == operation_id)
    candidate_ids = list(db.execute(candidate_statement))
    released: list[str] = []
    settled: list[str] = []
    exempt: list[str] = []
    held: list[str] = []

    for operation_id, tenant_id in candidate_ids:
        # Recovery's lock order is Tenant -> BillingOperation -> Subscription ->
        # UsageRecord -> refund grants -> business task rows.  Completion helpers
        # re-read these same locked rows but do not introduce a reverse order.
        db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        operation = db.scalar(
            select(BillingOperation)
            .where(BillingOperation.id == operation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if operation is None or operation.status != "in_progress":
            continue
        if operation.operation in _MANUAL_DOUBAO_OPERATIONS:
            exempt.append(operation.id)
            continue

        subscription_ids = _subscription_ids_for_operation(db, operation.id)
        quota.lock_subscriptions_for_billing(db, subscription_ids=subscription_ids)
        _usage_for_update(db, operation.id)
        list(
            db.scalars(
                select(CreditRefundGrant)
                .where(CreditRefundGrant.billing_operation_id == operation.id)
                .order_by(CreditRefundGrant.id)
                .with_for_update()
            )
        )

        tasks = _operation_tasks_for_update(db, operation_id=operation.id)
        if operation.operation in _ECOM_OPERATIONS:
            # E-commerce batches own their authoritative all-items settlement.
            finalized = try_finalize_ecom_operation(db, billing_operation_id=operation.id)
            if finalized is not None and finalized.completion_kind == "succeeded":
                settled.append(operation.id)
                continue
            if finalized is not None and finalized.status == "completed":
                released.append(operation.id)
                continue

        if operation.operation == "video_create" and len(tasks) == 1:
            task = tasks[0]
            if operation.result_type is not None and operation.result_payload is not None:
                try:
                    _validate_stored_result(operation)
                except BillingInvariantError:
                    _release_stale_operation(db, operation_id=operation.id)
                    released.append(operation.id)
                    continue
            if task.status == "done":
                if not _has_persisted_video_deliverable(db, task=task):
                    _fail_closed_video_recovery(db, operation_id=operation.id, task=task)
                    released.append(operation.id)
                    continue
                try:
                    # Reuse the worker's canonical actual-duration, TTS telemetry,
                    # and completion contract.  Reserved quote quantities are not
                    # evidence of delivered usage.
                    from app.workers.avatar_talk import (
                        _authoritative_billing_actual_seconds,
                        _billing_video_base_capability,
                        _complete_billing_quote_video,
                    )

                    usages = _usage_for_update(db, operation.id)
                    expected_capability = _billing_video_base_capability(task=task)
                    base_usage = next(
                        usage for usage in usages if usage.capability == expected_capability
                    )
                    _complete_billing_quote_video(
                        db,
                        task=task,
                        actual_seconds=_authoritative_billing_actual_seconds(task=task),
                        base_cost_cents=base_usage.cost_cents,
                        base_provider=base_usage.provider,
                        base_model=base_usage.model,
                    )
                except (BillingInvariantError, AppError, StopIteration):
                    if operation.status == "in_progress":
                        _fail_closed_video_recovery(db, operation_id=operation.id, task=task)
                    released.append(operation.id)
                else:
                    settled.append(operation.id)
                continue
            if task.status in {"failed", "cancelled"}:
                _release_stale_operation(db, operation_id=operation.id)
                released.append(operation.id)
                continue

        if not tasks:
            if operation.operation in _SYNCHRONOUS_AUTOMATIC_OPERATIONS and _timestamp_is_stale(
                operation.updated_at or operation.created_at,
                timeout_seconds=_billing_task_timeout_seconds(operation, None),
                now=now,
            ):
                _release_stale_operation(db, operation_id=operation.id)
                released.append(operation.id)
                continue
            held.append(operation.id)
            continue
        timeout = max(
            _billing_task_timeout_seconds(operation, task)
            for task in tasks
        )
        if any(not _task_is_stale(task, timeout_seconds=timeout, now=now) for task in tasks):
            held.append(operation.id)
            continue
        for task in tasks:
            if task.status in {"queued", "running"}:
                task.status = "failed"
                task.error_code = "BILLING_OPERATION_STALE"
                task.error_message = "Billing operation exceeded its authoritative timeout."
                task.error = task.error_message
                task.finished_at = now
                task.updated_at = now
        if operation.operation in _ECOM_OPERATIONS:
            # A timeout changes only unfinished items. Re-run the batch's
            # canonical finalizer so persisted deliverables settle precisely.
            db.flush(tasks)
            finalized = try_finalize_ecom_operation(db, billing_operation_id=operation.id)
            if finalized is not None and finalized.completion_kind == "succeeded":
                settled.append(operation.id)
            elif finalized is not None and finalized.status == "completed":
                released.append(operation.id)
            else:
                held.append(operation.id)
            continue
        _release_stale_operation(db, operation_id=operation.id)
        released.append(operation.id)

    db.flush()
    return RecoverySummary(
        released_operation_ids=tuple(released),
        settled_operation_ids=tuple(settled),
        exempt_manual_order_ids=tuple(exempt),
        held_operation_ids=tuple(held),
    )


def _is_retryable_postgres_transaction_error(exc: OperationalError) -> bool:
    return _sqlstate(exc) in _RETRYABLE_SQLSTATES


def recover_stale_billing_operations(
    db: Session,
    *,
    now: datetime,
) -> RecoverySummary:
    """Recover automatic reservations; CosyVoice performs inventory reads, never POSTs."""
    candidate_ids = list(
        db.scalars(
            select(BillingOperation.id)
            .where(BillingOperation.status == "in_progress")
            .order_by(BillingOperation.id)
        )
    )
    # The candidate scan must not become the transaction that settles every
    # operation.  Each candidate below gets its own root transaction.
    db.commit()
    released: list[str] = []
    settled: list[str] = []
    exempt: list[str] = []
    held: list[str] = []
    for operation_id in candidate_ids:
        identity = db.execute(
            select(BillingOperation.operation, BillingOperation.tenant_id).where(
                BillingOperation.id == operation_id
            )
        ).one_or_none()
        db.commit()
        if identity is not None and identity[0] == "cosyvoice_brand_voice_create":
            try:
                provider = resolve_named_provider(
                    db,
                    tenant_id=identity[1],
                    capability="voice_clone",
                    provider="cosyvoice-voice-clone",
                )
            except ProviderResolutionError:
                provider = None
            finally:
                db.rollback()
            outcome = reconcile_cosyvoice_operation(
                db,
                operation_id=operation_id,
                provider=provider,
                now=now,
                marker_for_request=_request_recovery_marker,
            )
            if outcome.state == "succeeded":
                settled.append(operation_id)
            elif outcome.state == "failed":
                released.append(operation_id)
            else:
                held.append(operation_id)
            continue
        for attempt in range(3):
            try:
                with db.begin():
                    summary = _recover_stale_billing_operations_once(
                        db,
                        now=now,
                        operation_id=operation_id,
                    )
                released.extend(summary.released_operation_ids)
                settled.extend(summary.settled_operation_ids)
                exempt.extend(summary.exempt_manual_order_ids)
                held.extend(summary.held_operation_ids)
                break
            except OperationalError as exc:
                if not _is_retryable_postgres_transaction_error(exc) or attempt == 2:
                    raise
                sleep(uniform(0.01, 0.05) * (attempt + 1))
    return RecoverySummary(
        released_operation_ids=tuple(released),
        settled_operation_ids=tuple(settled),
        exempt_manual_order_ids=tuple(exempt),
        held_operation_ids=tuple(held),
    )


def recover_orphaned_image_queue_tasks(
    *,
    session_factory: Any = SessionLocal,
    now: datetime | None = None,
    stale_after_seconds: float | None = None,
    aibrain_stale_after_seconds: float | None = None,
    progress_store: Any | None = None,
) -> ImageQueueRecoveryResult:
    recovered_at = now or datetime.now(UTC)
    stale_seconds = (
        settings.engine_orphan_task_stale_seconds
        if stale_after_seconds is None
        else stale_after_seconds
    )
    cutoff = recovered_at - timedelta(seconds=stale_seconds)
    aibrain_stale_seconds = (
        settings.engine_aibrain_reservation_stale_minutes * 60
        if aibrain_stale_after_seconds is None
        else aibrain_stale_after_seconds
    )
    aibrain_cutoff = recovered_at - timedelta(seconds=aibrain_stale_seconds)
    recovered_progress: list[tuple[str, str, str, str]] = []
    recovered_video_gen_batches: set[tuple[str, str]] = set()
    photo_task_count = 0
    video_gen_task_count = 0
    billing_recovery = RecoverySummary()

    with session_factory() as db:
        video_tasks = list(
            db.scalars(
                select(VideoTask)
                .where(
                    VideoTask.video_mode.in_(_VIDEO_TASK_RECOVERY_ERRORS),
                    VideoTask.status == "running",
                    VideoTask.deleted_at.is_(None),
                    VideoTask.updated_at <= cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for task in video_tasks:
            error_code, error_message = _VIDEO_TASK_RECOVERY_ERRORS[task.video_mode]
            if task.video_mode == "photo":
                photo_task_count += 1
            else:
                video_gen_task_count += 1
                if task.batch_id:
                    recovered_video_gen_batches.add((task.tenant_id, task.batch_id))
            release_reserved_quota(
                db,
                tenant_id=task.tenant_id,
                video_task_id=task.id,
            )
            task.status = "failed"
            task.error = error_message
            task.error_code = error_code
            task.error_message = error_message
            task.finished_at = recovered_at
            task.updated_at = recovered_at
            recovered_progress.append(
                (task.tenant_id, task.id, error_code, error_message)
            )
        for tenant_id, batch_id in sorted(recovered_video_gen_batches):
            refresh_batch_job(db, batch_id=batch_id, tenant_id=tenant_id)

        reverse_prompt_jobs = list(
            db.scalars(
                select(ReversePromptJob)
                .where(
                    ReversePromptJob.source_kind == "video",
                    ReversePromptJob.status == "running",
                    ReversePromptJob.updated_at <= cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in reverse_prompt_jobs:
            release_reverse_prompt_video_quota(
                db,
                tenant_id=job.tenant_id,
                reverse_prompt_job_id=job.id,
            )
            job.status = "failed"
            job.error_code = "REVERSE_PROMPT_FAILED"
            job.error_message = _REVERSE_WORKER_LOST_MESSAGE
            job.updated_at = recovered_at

        recent_replicate_output = exists().where(
            EcomReplicateOutput.job_id == EcomReplicateJob.id,
            EcomReplicateOutput.updated_at > cutoff,
        )
        replicate_jobs = list(
            db.scalars(
                select(EcomReplicateJob)
                .where(
                    EcomReplicateJob.status == "generating",
                    EcomReplicateJob.started_at.is_not(None),
                    EcomReplicateJob.updated_at <= cutoff,
                    ~recent_replicate_output,
                    ecom_replicate.live_ecom_replicate_job_condition(),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in replicate_jobs:
            outputs = list(
                db.scalars(
                    select(EcomReplicateOutput)
                    .where(EcomReplicateOutput.job_id == job.id)
                    .with_for_update()
                )
            )
            for output in outputs:
                if output.status not in {"planned", "generating"}:
                    continue
                output.status = "failed"
                output.error_code = "ECOM_REPLICATE_RENDER_FAILED"
                output.error_message = _REPLICATE_WORKER_LOST_MESSAGE
                output.updated_at = recovered_at

            succeeded = sum(output.status == "succeeded" for output in outputs)
            failed = sum(output.status == "failed" for output in outputs)
            if outputs and succeeded == len(outputs):
                job.status = "completed"
                job.error_code = None
                job.error_message = None
            elif succeeded:
                job.status = "partial_failed"
                job.error_code = "ECOM_REPLICATE_PARTIAL_FAILED"
                job.error_message = f"{failed} outputs failed after worker stopped."
            else:
                job.status = "failed"
                job.error_code = "ECOM_REPLICATE_FAILED"
                job.error_message = _REPLICATE_WORKER_LOST_MESSAGE
            job.finished_at = recovered_at
            job.updated_at = recovered_at
        aibrain_reservations = recover_stale_reasoning_reservations(
            db,
            cutoff=aibrain_cutoff,
            recovered_at=recovered_at,
        )
        copy_reservations = recover_stale_copy_quota_reservations(
            db,
            cutoff=cutoff,
            recovered_at=recovered_at,
        )
        db.commit()

    # Billing recovery has its own transaction so it never enters after this
    # generic orphan scan has locked unrelated business rows.
    with session_factory() as billing_db:
        billing_recovery = recover_stale_billing_operations(
            billing_db,
            now=recovered_at,
        )
        billing_db.commit()

    if progress_store is not None:
        for tenant_id, task_id, error_code, error_message in recovered_progress:
            try:
                progress_store.update(
                    f"{tenant_id}:{task_id}",
                    status="failed",
                    stage="failed",
                    error=error_message,
                    error_code=error_code,
                    error_message=error_message,
                )
            except Exception as exc:  # pragma: no cover - Redis is best effort
                logger.warning(
                    "orphan_task.progress_update_failed",
                    task_id=task_id,
                    error_type=type(exc).__name__,
                )

    return ImageQueueRecoveryResult(
        photo_tasks=photo_task_count,
        video_gen_tasks=video_gen_task_count,
        reverse_prompt_jobs=len(reverse_prompt_jobs),
        ecom_replicate_jobs=len(replicate_jobs),
        aibrain_reservations=aibrain_reservations,
        copy_reservations=copy_reservations,
        billing_operations_released=len(billing_recovery.released_operation_ids),
        billing_operations_settled=len(billing_recovery.settled_operation_ids),
    )
