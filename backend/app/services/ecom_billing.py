from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import TypeAlias

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Asset, BillingOperation, TaskAsset, Tenant, VideoTask
from app.db.session import SessionLocal
from app.schemas.ecom_images import (
    EcomCutoutBatchRequest,
    EcomCutoutRequest,
    EcomModelBatchRequest,
    EcomModelRequest,
)
from app.services import quota
from app.services.billing_operations import (
    EcomImageBatchStoredItem,
    EcomImageBatchStoredResult,
    _operation_for_update,
    _subscription_ids_for_operation,
    _usage_for_update,
    complete_failed,
    complete_succeeded,
)
from app.services.billing_quotes import request_sha256
from app.services.pricing import PRICING_POLICIES, build_simple_pricing, resolve_rate

EcomCutoutPayload: TypeAlias = EcomCutoutRequest | EcomCutoutBatchRequest
EcomModelPayload: TypeAlias = EcomModelRequest | EcomModelBatchRequest


def normalize_cutout_items(payload: EcomCutoutPayload) -> tuple[EcomCutoutRequest, ...]:
    if isinstance(payload, EcomCutoutRequest):
        return (payload,)
    return tuple(payload.items)


def normalize_model_items(payload: EcomModelPayload) -> tuple[EcomModelRequest, ...]:
    if isinstance(payload, EcomModelRequest):
        return (payload,)
    return tuple(payload.items)


def ecom_request_hash(
    *, operation: str, items: tuple[EcomCutoutRequest | EcomModelRequest, ...]
) -> str:
    return request_sha256(
        {
            "operation": operation,
            "items": [item.model_dump(mode="json") for item in items],
        }
    )


def ecom_pricing_draft(db: Session, *, tenant_id: str, operation: str, quantity: int):
    policy = PRICING_POLICIES[operation]
    return build_simple_pricing(
        policy=policy,
        rate=resolve_rate(db, tenant_id=tenant_id, policy=policy),
        quantity=Decimal(quantity),
    )


def _operation_tasks(
    db: Session, *, billing_operation_id: str, for_update: bool
) -> list[VideoTask]:
    statement = (
        select(VideoTask)
        .where(VideoTask.params["billing_operation_id"].as_string() == billing_operation_id)
        .order_by(VideoTask.id)
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return list(db.scalars(statement))


def try_finalize_ecom_operation(db: Session, *, billing_operation_id: str):
    """Finalize within the caller's transaction; this function never commits."""
    tenant_id = db.scalar(
        select(BillingOperation.tenant_id).where(BillingOperation.id == billing_operation_id)
    )
    if tenant_id is None:
        return None
    db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    operation = _operation_for_update(db, billing_operation_id)
    if operation.status == "completed":
        return operation
    subscription_ids = _subscription_ids_for_operation(db, billing_operation_id)
    quota.lock_subscriptions_for_billing(db, subscription_ids=subscription_ids)
    usages = _usage_for_update(db, operation.id)
    expected_item_indexes = {int(usage.billing_item_index) for usage in usages}
    tasks = _operation_tasks(db, billing_operation_id=billing_operation_id, for_update=True)
    if not tasks or any(task.status not in {"done", "failed"} for task in tasks):
        return None

    try:
        task_item_indexes = {int(task.params["billing_item_index"]) for task in tasks}
    except (KeyError, TypeError, ValueError):
        return None
    if task_item_indexes != expected_item_indexes or len(tasks) != len(expected_item_indexes):
        return None

    successful = {
        int(task.params["billing_item_index"]): Decimal("1")
        for task in tasks
        if task.status == "done"
    }
    if not successful:
        operation = complete_failed(
            db,
            operation_id=billing_operation_id,
            code="ECOM_IMAGE_BATCH_FAILED",
            http_status=502,
            sanitized_detail=None,
        )
        return operation

    asset_rows = list(
        db.execute(
            select(TaskAsset.video_task_id, Asset.id)
            .join(Asset, Asset.id == TaskAsset.asset_id)
            .where(
                TaskAsset.video_task_id.in_([task.id for task in tasks]),
                TaskAsset.role == "output_image",
                Asset.status == "ready",
                Asset.deleted_at.is_(None),
                Asset.storage_key != "",
            )
        )
    )
    asset_ids_by_task: dict[str, list[str]] = {}
    for task_id, asset_id in asset_rows:
        asset_ids_by_task.setdefault(task_id, []).append(asset_id)
    successful_tasks = [task for task in tasks if task.status == "done"]
    if any(len(asset_ids_by_task.get(task.id, [])) != 1 for task in successful_tasks):
        return None

    asset_by_task = {task_id: asset_ids[0] for task_id, asset_ids in asset_ids_by_task.items()}
    stored_items = [
        EcomImageBatchStoredItem(
            item_index=int(task.params["billing_item_index"]),
            task_id=task.id,
            source_asset_id=str(task.params["source_asset_id"]),
            status="done" if task.status == "done" else "failed",
            asset_id=asset_by_task.get(task.id),
        )
        for task in sorted(tasks, key=lambda task: int(task.params["billing_item_index"]))
    ]
    operation = complete_succeeded(
        db,
        operation_id=billing_operation_id,
        actual_quantities=successful,
        result_type="ecom_image_batch",
        result_id=operation.result_id,
        result_payload=EcomImageBatchStoredResult(items=stored_items),
    )
    return operation


def finalize_ecom_operation(
    *,
    billing_operation_id: str,
    session_factory: Callable[[], Session] = SessionLocal,
):
    """Run terminal settlement after an item transaction has committed."""
    with session_factory() as db:
        operation = try_finalize_ecom_operation(db, billing_operation_id=billing_operation_id)
        db.commit()
        return operation
