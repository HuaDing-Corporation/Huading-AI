from __future__ import annotations

from decimal import Decimal
from typing import TypeAlias

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TaskAsset, UsageRecord, VideoTask
from app.schemas.ecom_images import (
    EcomCutoutBatchRequest,
    EcomCutoutRequest,
    EcomModelBatchRequest,
    EcomModelRequest,
)
from app.services.billing_operations import (
    EcomImageBatchStoredItem,
    EcomImageBatchStoredResult,
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
    """Complete one parent reservation once every e-commerce task is terminal."""
    expected_item_indexes = set(
        db.scalars(
            select(UsageRecord.billing_item_index).where(
                UsageRecord.billing_operation_id == billing_operation_id
            )
        )
    )
    tasks = _operation_tasks(db, billing_operation_id=billing_operation_id, for_update=False)
    if not tasks or any(task.status not in {"done", "failed"} for task in tasks):
        return None

    task_item_indexes = {int(task.params["billing_item_index"]) for task in tasks}
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
        _operation_tasks(db, billing_operation_id=billing_operation_id, for_update=True)
        db.commit()
        return operation

    asset_by_task = {
        task_id: asset_id
        for task_id, asset_id in db.execute(
            select(TaskAsset.video_task_id, TaskAsset.asset_id).where(
                TaskAsset.video_task_id.in_([task.id for task in tasks]),
                TaskAsset.role == "output_image",
            )
        )
    }
    stored_items = [
        EcomImageBatchStoredItem(
            item_index=int(task.params["billing_item_index"]),
            task_id=task.id,
            source_asset_id=str(task.params["source_asset_id"]),
            status="done" if task.status == "done" else "failed",
            asset_id=asset_by_task.get(task.id),
        )
        for task in tasks
    ]
    operation = complete_succeeded(
        db,
        operation_id=billing_operation_id,
        actual_quantities=successful,
        result_type="ecom_image_batch",
        result_id=str(tasks[0].params.get("batch_id") or ""),
        result_payload=EcomImageBatchStoredResult(items=stored_items),
    )
    _operation_tasks(db, billing_operation_id=billing_operation_id, for_update=True)
    db.commit()
    return operation
