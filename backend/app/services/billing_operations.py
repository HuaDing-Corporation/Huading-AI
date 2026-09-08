from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models import (
    BILLING_CREDITS_MAX,
    ERROR_PAYLOAD_MAX_BYTES,
    RESULT_PAYLOAD_MAX_BYTES,
    BillingOperation,
    BrandVoiceOrder,
    Subscription,
    UsageRecord,
    VideoTask,
)
from app.schemas.brand_voice_orders import BrandVoiceOrderRead
from app.schemas.brand_voices import BrandVoiceRead
from app.services import quota
from app.services.billing_quotes import VerifiedQuote, canonical_json
from app.services.pricing import (
    PricingInvariantError,
    PricingLine,
    PricingSnapshot,
    RateSource,
    resolution_snapshot_fields,
    validate_pricing_snapshot,
)

logger = structlog.get_logger(__name__)

_RESULT_SCHEMAS: dict[str, type[BaseModel]] = {}
_RESULT_SCHEMAS_LOCK = threading.Lock()
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_QUANTITY_SCALE = 3
_QUANTITY_UPPER_BOUND = Decimal("1000000000")
_CREDITS_SCALE = 6
_CREDITS_UPPER_BOUND = Decimal("1000000000000")
_ECOM_IMAGE_OPERATIONS = frozenset({"ecom_cutout", "ecom_model"})


class BillingInvariantError(AppError):
    def __init__(self, message: str) -> None:
        logger.critical("billing_invariant_violation", reason=message)
        super().__init__(
            "Stored billing state is invalid.",
            code="BILLING_INVARIANT_VIOLATION",
            status_code=500,
        )


@dataclass(frozen=True)
class BillingStart:
    operation: BillingOperation
    replayed: bool


@dataclass(frozen=True)
class UsageAllocation:
    item_index: int
    pricing_line_index: int
    quantity: Decimal
    credits: Decimal
    provider: str
    model: str | None
    video_task_id: str | None


class _BillingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ErrorDetail(_BillingModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    requires_new_quote: bool | None = None


class BillingSummary(_BillingModel):
    operation_id: str
    idempotency_key: UUID
    status: Literal["reserved", "settled", "partially_settled", "released"]
    requested_credits: int
    held_credits: int
    settled_credits: int
    released_credits: int


class BillingFailure(_BillingModel):
    code: str
    original_http_status: int
    detail: _ErrorDetail | None


class BillingInProgressLookup(_BillingModel):
    operation: str
    idempotency_key: UUID
    state: Literal["in_progress"] = "in_progress"
    completion_kind: None = None
    billing: BillingSummary
    result_type: str | None = None
    result_id: str | None = None
    resource: Any | None = None
    result: None = None
    failure: None = None

    @model_validator(mode="after")
    def _validate_pending_ecom_response(self):
        if self.operation in _ECOM_IMAGE_OPERATIONS:
            if (
                self.result_type != "ecom_image_batch"
                or self.result_id is None
                or self.resource is not None
            ):
                raise ValueError("pending e-commerce lookup requires a batch id and no resource")
            if str(UUID(self.result_id)) != self.result_id:
                raise ValueError("pending e-commerce batch identifier is not canonical")
        return self


class BillingSucceededLookup(_BillingModel):
    operation: str
    idempotency_key: UUID
    state: Literal["completed"] = "completed"
    completion_kind: Literal["succeeded"] = "succeeded"
    billing: BillingSummary
    result_type: str
    result_id: str | None = None
    result: Any
    resource: Any | None = None
    failure: None = None


class BillingRejectedLookup(_BillingModel):
    operation: str
    idempotency_key: UUID
    state: Literal["completed"] = "completed"
    completion_kind: Literal["rejected"] = "rejected"
    billing: BillingSummary
    result_type: str
    result_id: str | None = None
    result: None = None
    resource: Any
    failure: None = None


class BillingFailedLookup(_BillingModel):
    operation: str
    idempotency_key: UUID
    state: Literal["completed"] = "completed"
    completion_kind: Literal["failed"] = "failed"
    billing: BillingSummary
    result_type: None = None
    result_id: None = None
    result: None = None
    resource: None = None
    failure: BillingFailure


BillingOperationLookup = (
    BillingInProgressLookup | BillingSucceededLookup | BillingRejectedLookup | BillingFailedLookup
)


class _StoredErrorPayload(_BillingModel):
    detail: _ErrorDetail | None = None


def _dump_error_payload(payload: _StoredErrorPayload) -> dict[str, object]:
    detail = payload.detail
    return {"detail": None if detail is None else detail.model_dump(mode="json", exclude_none=True)}


def register_billing_result_schema(result_type: str, schema: type[BaseModel]) -> None:
    if not result_type or len(result_type) > 64 or not issubclass(schema, BaseModel):
        raise ValueError("invalid billing result schema registration")
    with _RESULT_SCHEMAS_LOCK:
        existing = _RESULT_SCHEMAS.get(result_type)
        if existing is not None and existing is not schema:
            raise ValueError(f"billing result type is already registered: {result_type}")
        _RESULT_SCHEMAS[result_type] = schema


class ScriptGenerateStoredResult(_BillingModel):
    script: str


class ScenePromptStoredResult(_BillingModel):
    scene_prompt: str
    negative_prompt: str


class EcomImageBatchStoredItem(_BillingModel):
    item_index: int = Field(ge=0, le=19)
    task_id: str = Field(min_length=1, max_length=36)
    source_asset_id: str = Field(min_length=1, max_length=36)
    status: Literal["done", "failed"]
    asset_id: str | None = Field(default=None, min_length=1, max_length=36)


class EcomImageBatchStoredResult(_BillingModel):
    items: list[EcomImageBatchStoredItem] = Field(min_length=1, max_length=20)


class VideoTaskBillingResource(_BillingModel):
    task_id: str = Field(min_length=1, max_length=36)
    status: Literal["queued", "running", "done", "failed", "cancelled"]


register_billing_result_schema("script_generate_result", ScriptGenerateStoredResult)
register_billing_result_schema("scene_prompt_result", ScenePromptStoredResult)
register_billing_result_schema("ecom_image_batch", EcomImageBatchStoredResult)
register_billing_result_schema("video_task", VideoTaskBillingResource)
register_billing_result_schema("brand_voice_order", BrandVoiceOrderRead)
register_billing_result_schema("brand_voice", BrandVoiceRead)


def _integer_amount(value: Decimal, *, field: str) -> int:
    amount = Decimal(value)
    if (
        not amount.is_finite()
        or amount < 0
        or amount > BILLING_CREDITS_MAX
        or amount != amount.to_integral_value()
    ):
        raise BillingInvariantError(f"{field} is not a non-negative wallet integer")
    return int(amount)


def held_credits(operation: BillingOperation) -> int:
    if operation.status == "completed":
        return 0
    return _integer_amount(operation.requested_credits, field="requested_credits")


def billing_summary(operation: BillingOperation) -> BillingSummary:
    requested = _integer_amount(operation.requested_credits, field="requested_credits")
    settled = _integer_amount(operation.settled_credits, field="settled_credits")
    released = _integer_amount(operation.released_credits, field="released_credits")
    if operation.status == "in_progress":
        if operation.completion_kind is not None or settled or released:
            raise BillingInvariantError("in-progress operation has terminal amounts")
        status: Literal["reserved", "settled", "partially_settled", "released"] = "reserved"
    elif operation.status == "completed":
        if settled + released != requested:
            raise BillingInvariantError("terminal operation does not conserve requested credits")
        if operation.completion_kind == "succeeded":
            status = "partially_settled" if settled and released else "settled"
        elif operation.completion_kind in {"failed", "rejected"} and not settled:
            status = "released"
        else:
            raise BillingInvariantError("terminal operation completion kind is invalid")
    else:
        raise BillingInvariantError("operation state is invalid")
    return BillingSummary(
        operation_id=operation.id,
        idempotency_key=UUID(operation.idempotency_key),
        status=status,
        requested_credits=requested,
        held_credits=held_credits(operation),
        settled_credits=settled,
        released_credits=released,
    )


def _operation_for_key(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    operation: str,
    idempotency_key: UUID,
) -> BillingOperation | None:
    return db.scalar(
        select(BillingOperation).where(
            BillingOperation.tenant_id == tenant_id,
            BillingOperation.user_id == user_id,
            BillingOperation.operation == operation,
            BillingOperation.idempotency_key == str(idempotency_key),
        )
    )


def find_replay(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    operation: str,
    idempotency_key: UUID,
    request_hash: str,
) -> BillingStart | None:
    existing = _operation_for_key(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        operation=operation,
        idempotency_key=idempotency_key,
    )
    if existing is None:
        return None
    if existing.request_hash != request_hash:
        raise AppError(
            "Idempotency key was already used for a different request.",
            code="IDEMPOTENCY_KEY_REUSED",
            status_code=409,
        )
    return BillingStart(operation=existing, replayed=True)


def _stored_snapshot(snapshot: PricingSnapshot) -> dict[str, object]:
    pricing_lines = []
    for line in snapshot.pricing_lines:
        pricing_lines.append(
            {
                "operation": line.operation,
                "capability": line.capability,
                "unit": line.unit,
                "quantity": line.quantity,
                "unit_credits": line.unit_credits,
                "subtotal_credits": line.subtotal_credits,
                "rate_scope": line.rate_scope.value,
                "rate_source": line.rate.source.value,
                "rate_id": line.rate.rate_id,
                "effective_at": line.rate.effective_at,
                "policy_key": line.rate.policy_key,
                "policy_version": line.rate.policy_version,
                "label": line.label,
                **resolution_snapshot_fields(line),
            }
        )
    disclosures = []
    for disclosure in snapshot.disclosures:
        disclosures.append(
            {
                "key": disclosure.key,
                "rendered_text": disclosure.rendered_text,
                "copy_version": disclosure.copy_version,
                "unit": disclosure.unit,
                "rate_scope": disclosure.rate_scope.value,
                "rate_source": disclosure.rate.source.value,
                "rate_id": disclosure.rate.rate_id,
                "effective_at": disclosure.rate.effective_at,
                "policy_key": disclosure.rate.policy_key,
                "policy_version": disclosure.rate.policy_version,
                "reference_unit_credits": disclosure.rate.unit_credits,
            }
        )
    payload = {
        "operation": snapshot.operation,
        "pricing_shape": snapshot.pricing_shape,
        "pricing_lines": pricing_lines,
        "disclosures": disclosures,
        "subtotal_credits": snapshot.subtotal_credits,
        "payable_credits": snapshot.payable_credits,
        "rounding": snapshot.rounding,
    }
    return json.loads(canonical_json(payload).decode("utf-8"))


def _finite_decimal(
    value: Decimal,
    *,
    field: str,
    allow_zero: bool,
    scale: int,
    upper_bound: Decimal,
) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AppError(
            f"Invalid {field}.", code="BILLING_ALLOCATION_INVALID", status_code=500
        ) from exc
    quantum = Decimal(1).scaleb(-scale)
    try:
        scale_is_valid = parsed == parsed.quantize(quantum)
    except InvalidOperation:
        scale_is_valid = False
    if (
        not parsed.is_finite()
        or parsed < 0
        or parsed >= upper_bound
        or not scale_is_valid
        or (not allow_zero and parsed == 0)
    ):
        raise AppError(f"Invalid {field}.", code="BILLING_ALLOCATION_INVALID", status_code=500)
    return parsed


def _validate_allocations(
    snapshot: PricingSnapshot,
    allocations: Sequence[UsageAllocation],
) -> tuple[UsageAllocation, ...]:
    raw_items = tuple(allocations)
    if any(
        isinstance(item.item_index, bool) or not isinstance(item.item_index, int)
        for item in raw_items
    ):
        raise AppError(
            "Billing allocation item index is invalid.",
            code="BILLING_ALLOCATION_INVALID",
            status_code=500,
        )
    items = tuple(sorted(raw_items, key=lambda item: item.item_index))
    if not items or [item.item_index for item in items] != list(range(len(items))):
        raise AppError(
            "Billing allocations must use contiguous item indexes.",
            code="BILLING_ALLOCATION_INVALID",
            status_code=500,
        )
    line_count = len(snapshot.pricing_lines)
    for item in items:
        if not 0 <= item.pricing_line_index < line_count:
            raise AppError(
                "Billing allocation line is invalid.",
                code="BILLING_ALLOCATION_INVALID",
                status_code=500,
            )
        if not item.provider.strip():
            raise AppError(
                "Billing allocation provider is required.",
                code="BILLING_ALLOCATION_INVALID",
                status_code=500,
            )
        quantity = _finite_decimal(
            item.quantity,
            field="quantity",
            allow_zero=False,
            scale=_QUANTITY_SCALE,
            upper_bound=_QUANTITY_UPPER_BOUND,
        )
        credits = _finite_decimal(
            item.credits,
            field="credits",
            allow_zero=True,
            scale=_CREDITS_SCALE,
            upper_bound=_CREDITS_UPPER_BOUND,
        )
        line = snapshot.pricing_lines[item.pricing_line_index]
        if credits != quantity * line.unit_credits:
            raise AppError(
                "Billing allocation arithmetic is invalid.",
                code="BILLING_ALLOCATION_INVALID",
                status_code=500,
            )
        if credits == 0 and snapshot.operation != "cosyvoice_brand_voice_create":
            raise AppError(
                "Zero-credit allocation is not allowed.",
                code="BILLING_ALLOCATION_INVALID",
                status_code=500,
            )
    for index, line in enumerate(snapshot.pricing_lines):
        matching = [item for item in items if item.pricing_line_index == index]
        if sum((Decimal(item.quantity) for item in matching), Decimal("0")) != line.quantity:
            raise AppError(
                "Billing allocation quantity does not match the quote.",
                code="BILLING_ALLOCATION_INVALID",
                status_code=500,
            )
    if snapshot.pricing_shape == "simple":
        if any(item.pricing_line_index != 0 for item in items):
            raise AppError(
                "Simple pricing must use line zero.",
                code="BILLING_ALLOCATION_INVALID",
                status_code=500,
            )
        line = snapshot.pricing_lines[0]
        if line.unit in {"call", "image"} and line.quantity == line.quantity.to_integral_value():
            expected_items = int(line.quantity)
            if len(items) != expected_items or any(
                Decimal(item.quantity) != Decimal("1") for item in items
            ):
                raise AppError(
                    "Simple per-item pricing must use one allocation per quoted unit.",
                    code="BILLING_ALLOCATION_INVALID",
                    status_code=500,
                )
    elif (
        any(item.item_index != item.pricing_line_index for item in items)
        or len(items) != line_count
    ):
        raise AppError(
            "Composite pricing must map one item to each line.",
            code="BILLING_ALLOCATION_INVALID",
            status_code=500,
        )
    if sum((Decimal(item.credits) for item in items), Decimal("0")) != snapshot.subtotal_credits:
        raise AppError(
            "Billing allocations do not conserve the quoted subtotal.",
            code="BILLING_ALLOCATION_INVALID",
            status_code=500,
        )
    return items


def create_reserved_operation(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    operation: str,
    idempotency_key: UUID,
    request_hash: str,
    verified_quote: VerifiedQuote,
    usage_allocations: Sequence[UsageAllocation],
    result_type: str | None = None,
    result_id: str | None = None,
) -> BillingOperation:
    replay = find_replay(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay.operation
    if verified_quote.snapshot.operation != operation:
        raise AppError(
            "Quote operation does not match the request.",
            code="QUOTE_REQUEST_MISMATCH",
            status_code=422,
        )
    if result_type is not None and result_type not in _RESULT_SCHEMAS:
        raise AppError(
            "Billing result schema is not registered.",
            code="BILLING_RESULT_SCHEMA_NOT_REGISTERED",
            status_code=500,
        )
    if result_id is not None and len(result_id) > 128:
        raise AppError(
            "Billing result identifier is too large.",
            code="BILLING_RESULT_ID_INVALID",
            status_code=500,
        )
    subscription = quota.lock_active_subscription(db, tenant_id=tenant_id)
    replay = find_replay(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay.operation
    stored_snapshot = _stored_snapshot(verified_quote.snapshot)
    try:
        snapshot = validate_pricing_snapshot(stored_snapshot)
    except PricingInvariantError as exc:
        raise AppError(
            "Verified quote contains invalid pricing.",
            code="BILLING_QUOTE_INVALID",
            status_code=500,
        ) from exc
    allocations = _validate_allocations(snapshot, usage_allocations)
    requested = snapshot.payable_credits
    quota.reserve_locked_subscription_credits(
        subscription,
        requested_credits=requested,
    )
    operation_row = BillingOperation(
        tenant_id=tenant_id,
        user_id=user_id,
        operation=operation,
        idempotency_key=str(idempotency_key),
        request_hash=request_hash,
        quote_hash=verified_quote.quote_hash,
        pricing_snapshot=stored_snapshot,
        requested_credits=Decimal(requested),
        settled_credits=Decimal("0"),
        released_credits=Decimal("0"),
        status="in_progress",
        result_type=result_type,
        result_id=result_id,
    )
    db.add(operation_row)
    db.flush()
    for allocation in allocations:
        line = snapshot.pricing_lines[allocation.pricing_line_index]
        db.add(
            UsageRecord(
                tenant_id=tenant_id,
                subscription_id=subscription.id,
                video_task_id=allocation.video_task_id,
                billing_operation_id=operation_row.id,
                billing_item_index=allocation.item_index,
                billing_pricing_line_index=allocation.pricing_line_index,
                capability=line.capability,
                provider=allocation.provider.strip(),
                model=allocation.model,
                unit=line.unit,
                quantity=Decimal(allocation.quantity),
                credits=Decimal(allocation.credits),
                cost_cents=0,
                status="reserved",
            )
        )
    db.flush()
    return operation_row


def _operation_for_update(db: Session, operation_id: str) -> BillingOperation:
    operation = db.scalar(
        select(BillingOperation)
        .where(BillingOperation.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if operation is None:
        raise AppError(
            "Billing operation not found.",
            code="BILLING_OPERATION_NOT_FOUND",
            status_code=404,
        )
    return operation


def _subscription_ids_for_operation(db: Session, operation_id: str) -> list[str]:
    return list(
        db.scalars(
            select(UsageRecord.subscription_id)
            .where(
                UsageRecord.billing_operation_id == operation_id,
                UsageRecord.subscription_id.is_not(None),
            )
            .distinct()
            .order_by(UsageRecord.subscription_id)
        )
    )


def _usage_for_update(db: Session, operation_id: str) -> list[UsageRecord]:
    return list(
        db.scalars(
            select(UsageRecord)
            .where(UsageRecord.billing_operation_id == operation_id)
            .order_by(UsageRecord.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def _operation_subscription(
    subscriptions: Sequence[Subscription],
    usages: Sequence[UsageRecord],
) -> Subscription:
    subscription_ids = {usage.subscription_id for usage in usages}
    if len(subscription_ids) != 1 or None in subscription_ids:
        raise BillingInvariantError("operation usages do not share a subscription")
    subscription_id = next(iter(subscription_ids))
    by_id = {subscription.id: subscription for subscription in subscriptions}
    subscription = by_id.get(subscription_id)
    if subscription is None or set(by_id) != subscription_ids:
        raise BillingInvariantError("operation subscription lock set is invalid")
    return subscription


def _snapshot_from_operation(operation: BillingOperation) -> PricingSnapshot:
    try:
        return validate_pricing_snapshot(operation.pricing_snapshot)
    except (PricingInvariantError, TypeError, ValueError) as exc:
        raise BillingInvariantError("stored pricing snapshot is invalid") from exc


def _payload_bytes(payload: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AppError(
            "Billing payload is not valid JSON.",
            code="BILLING_PAYLOAD_INVALID",
            status_code=500,
        ) from exc


def _validate_result_payload(result_type: str, payload: BaseModel) -> dict[str, object]:
    schema = _RESULT_SCHEMAS.get(result_type)
    if schema is None:
        raise AppError(
            "Billing result schema is not registered.",
            code="BILLING_RESULT_SCHEMA_NOT_REGISTERED",
            status_code=500,
        )
    try:
        validated = schema.model_validate(payload.model_dump(mode="python"))
        stored = validated.model_dump(mode="json")
    except (AttributeError, ValidationError) as exc:
        raise AppError(
            "Billing result payload is invalid.",
            code="BILLING_RESULT_PAYLOAD_INVALID",
            status_code=500,
        ) from exc
    if len(_payload_bytes(stored)) > RESULT_PAYLOAD_MAX_BYTES:
        raise AppError(
            "Billing result payload is too large.",
            code="BILLING_RESULT_PAYLOAD_TOO_LARGE",
            status_code=500,
        )
    return stored


def _validated_error_payload(
    code: str,
    http_status: int,
    detail: object | None,
) -> dict[str, object]:
    if not _SAFE_CODE.fullmatch(code) or not 400 <= http_status <= 599:
        raise AppError(
            "Billing application error metadata is invalid.",
            code="BILLING_ERROR_PAYLOAD_INVALID",
            status_code=500,
        )
    try:
        payload = _dump_error_payload(_StoredErrorPayload.model_validate({"detail": detail}))
    except ValidationError as exc:
        raise AppError(
            "Billing error payload is invalid.",
            code="BILLING_ERROR_PAYLOAD_INVALID",
            status_code=500,
        ) from exc
    if len(_payload_bytes(payload)) > ERROR_PAYLOAD_MAX_BYTES:
        raise AppError(
            "Billing error payload is too large.",
            code="BILLING_ERROR_PAYLOAD_TOO_LARGE",
            status_code=500,
        )
    return payload


def _validate_stored_allocations(
    snapshot: PricingSnapshot,
    usages: Sequence[UsageRecord],
    *,
    require_reserved: bool,
) -> None:
    allocations = [
        UsageAllocation(
            item_index=usage.billing_item_index if usage.billing_item_index is not None else -1,
            pricing_line_index=(
                usage.billing_pricing_line_index
                if usage.billing_pricing_line_index is not None
                else -1
            ),
            quantity=Decimal(usage.quantity),
            credits=Decimal(usage.credits),
            provider=usage.provider,
            model=usage.model,
            video_task_id=usage.video_task_id,
        )
        for usage in usages
    ]
    if require_reserved and any(usage.status != "reserved" for usage in usages):
        raise BillingInvariantError("non-terminal operation has non-reserved usage")
    try:
        _validate_allocations(snapshot, allocations)
    except AppError as exc:
        raise BillingInvariantError("stored billing allocations are invalid") from exc


def _is_explicit_fixed_zero_line(snapshot: PricingSnapshot, line: PricingLine) -> bool:
    return (
        snapshot.operation == "cosyvoice_brand_voice_create"
        and line.operation == snapshot.operation
        and line.unit_credits == 0
        and line.rate.source == RateSource.FIXED_POLICY
        and line.rate.policy_key == snapshot.operation
    )


def _release_operation(
    operation: BillingOperation,
    usages: Sequence[UsageRecord],
    subscription,
    *,
    code: str,
    http_status: int,
    error_payload: dict[str, object],
) -> BillingOperation:
    requested = _integer_amount(operation.requested_credits, field="requested_credits")
    quota.settle_locked_subscription_credits(
        subscription,
        requested_credits=requested,
        settled_credits=0,
    )
    now = datetime.now(UTC)
    for usage in usages:
        usage.status = "released"
        usage.settled_at = now
    operation.status = "completed"
    operation.completion_kind = "failed"
    operation.completed_at = now
    operation.settled_credits = Decimal("0")
    operation.released_credits = operation.requested_credits
    operation.result_type = None
    operation.result_id = None
    operation.result_payload = None
    operation.error_code = code
    operation.error_http_status = http_status
    operation.error_payload = error_payload
    operation.updated_at = now
    return operation


def complete_succeeded(
    db: Session,
    *,
    operation_id: str,
    actual_quantities: Mapping[int, Decimal],
    result_type: str,
    result_id: str | None,
    result_payload: BaseModel,
) -> BillingOperation:
    discovered_subscription_ids = _subscription_ids_for_operation(db, operation_id)
    operation = _operation_for_update(db, operation_id)
    if operation.status == "completed":
        return operation
    stored_result = _validate_result_payload(result_type, result_payload)
    snapshot = _snapshot_from_operation(operation)
    subscriptions = quota.lock_subscriptions_for_billing(
        db,
        subscription_ids=discovered_subscription_ids,
    )
    usages = _usage_for_update(db, operation.id)
    _validate_stored_allocations(snapshot, usages, require_reserved=True)
    if not usages:
        raise BillingInvariantError("operation has no usage allocations")
    subscription = _operation_subscription(subscriptions, usages)
    usage_by_item = {int(usage.billing_item_index): usage for usage in usages}
    exact_subtotal = Decimal("0")
    parsed_actual: dict[int, Decimal] = {}
    parsed_credits: dict[int, Decimal] = {}
    invalid_code: str | None = None
    for item_index, value in actual_quantities.items():
        if isinstance(item_index, bool) or not isinstance(item_index, int):
            invalid_code = "BILLING_ACTUAL_QUANTITY_INVALID"
            break
        usage = usage_by_item.get(item_index)
        if usage is None:
            invalid_code = "BILLING_ACTUAL_QUANTITY_INVALID"
            break
        line = snapshot.pricing_lines[int(usage.billing_pricing_line_index)]
        try:
            actual = _finite_decimal(
                value,
                field="actual quantity",
                allow_zero=_is_explicit_fixed_zero_line(snapshot, line),
                scale=_QUANTITY_SCALE,
                upper_bound=_QUANTITY_UPPER_BOUND,
            )
        except AppError:
            invalid_code = "BILLING_ACTUAL_QUANTITY_INVALID"
            break
        if actual > Decimal(usage.quantity):
            invalid_code = "BILLING_QUOTE_EXCEEDED"
            break
        try:
            actual_credits = _finite_decimal(
                actual * line.unit_credits,
                field="actual credits",
                allow_zero=_is_explicit_fixed_zero_line(snapshot, line),
                scale=_CREDITS_SCALE,
                upper_bound=_CREDITS_UPPER_BOUND,
            )
        except AppError:
            invalid_code = "BILLING_SETTLEMENT_AMOUNT_INVALID"
            break
        parsed_actual[item_index] = actual
        parsed_credits[item_index] = actual_credits
        exact_subtotal += actual_credits
    if not parsed_actual:
        invalid_code = invalid_code or "BILLING_NO_SUCCESSFUL_ALLOCATION"
    if (
        invalid_code is None
        and exact_subtotal.to_integral_value(rounding=ROUND_CEILING) > BILLING_CREDITS_MAX
    ):
        invalid_code = "BILLING_SETTLEMENT_AMOUNT_INVALID"
    if invalid_code is not None:
        error_payload = _validated_error_payload(
            invalid_code,
            409,
            {"requires_new_quote": True},
        )
        return _release_operation(
            operation,
            usages,
            subscription,
            code=invalid_code,
            http_status=409,
            error_payload=error_payload,
        )
    settled = _integer_amount(
        exact_subtotal.to_integral_value(rounding=ROUND_CEILING),
        field="settled_credits",
    )
    requested = _integer_amount(operation.requested_credits, field="requested_credits")
    if settled > requested:
        raise BillingInvariantError("settlement exceeds the immutable reservation")
    quota.settle_locked_subscription_credits(
        subscription,
        requested_credits=requested,
        settled_credits=settled,
    )
    now = datetime.now(UTC)
    for item_index, usage in usage_by_item.items():
        if item_index in parsed_actual:
            usage.quantity = parsed_actual[item_index]
            usage.credits = parsed_credits[item_index]
            usage.status = "settled"
        else:
            usage.status = "released"
        usage.settled_at = now
    operation.status = "completed"
    operation.completion_kind = "succeeded"
    operation.completed_at = now
    operation.settled_credits = Decimal(settled)
    operation.released_credits = Decimal(requested - settled)
    operation.result_type = result_type
    operation.result_id = result_id
    operation.result_payload = stored_result
    operation.error_code = None
    operation.error_http_status = None
    operation.error_payload = None
    operation.updated_at = now
    db.flush()
    return operation


def complete_failed(
    db: Session,
    *,
    operation_id: str,
    code: str,
    http_status: int,
    sanitized_detail: object | None,
) -> BillingOperation:
    discovered_subscription_ids = _subscription_ids_for_operation(db, operation_id)
    operation = _operation_for_update(db, operation_id)
    if operation.status == "completed":
        return operation
    error_payload = _validated_error_payload(code, http_status, sanitized_detail)
    snapshot = _snapshot_from_operation(operation)
    subscriptions = quota.lock_subscriptions_for_billing(
        db,
        subscription_ids=discovered_subscription_ids,
    )
    usages = _usage_for_update(db, operation.id)
    _validate_stored_allocations(snapshot, usages, require_reserved=True)
    subscription = _operation_subscription(subscriptions, usages)
    released = _release_operation(
        operation,
        usages,
        subscription,
        code=code,
        http_status=http_status,
        error_payload=error_payload,
    )
    db.flush()
    return released


def _validate_stored_result(operation: BillingOperation) -> BaseModel:
    if not operation.result_type or operation.result_payload is None:
        raise BillingInvariantError("typed billing result is missing")
    schema = _RESULT_SCHEMAS.get(operation.result_type)
    if schema is None:
        raise BillingInvariantError("stored result type is not registered")
    try:
        result = schema.model_validate(operation.result_payload)
    except ValidationError as exc:
        raise BillingInvariantError("stored result payload fails its schema") from exc
    stored = result.model_dump(mode="json")
    if len(_payload_bytes(stored)) > RESULT_PAYLOAD_MAX_BYTES:
        raise BillingInvariantError("stored result payload exceeds its byte limit")
    return result


def _immutable_manual_order_result(result: BrandVoiceOrderRead) -> dict[str, object]:
    snapshot = result.model_dump(
        mode="python",
        exclude={
            "refund_disposition",
            "refund_grant_status",
            "refund_applied_at",
        },
    )
    for field in ("fulfilled_at", "expires_at", "rejected_at", "created_at", "updated_at"):
        value = snapshot[field]
        if isinstance(value, datetime):
            snapshot[field] = (
                value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
            )
    return snapshot


def _lookup_resource(
    db: Session,
    operation: BillingOperation,
    *,
    allow_stale_reread: bool = True,
) -> BaseModel:
    if operation.result_type == "brand_voice_order":
        if operation.result_id is None:
            raise BillingInvariantError("manual order result identifier is missing")
        # The order is payment-user scoped and its refund grant may change after
        # the operation became terminal, so never return the stale stored JSON.
        from app.services.brand_voice_orders import user_brand_voice_order_read

        resource = user_brand_voice_order_read(
            db,
            tenant_id=operation.tenant_id,
            user_id=operation.user_id,
            order_id=operation.result_id,
            allow_stale_reread=allow_stale_reread,
        )
        _validate_brand_voice_order_resource(operation, resource)
        if operation.status == "in_progress":
            if operation.result_payload is not None:
                raise BillingInvariantError("in-progress manual order contains a stored result")
        else:
            stored = _validate_stored_result(operation)
            if not isinstance(stored, BrandVoiceOrderRead) or _immutable_manual_order_result(
                stored
            ) != _immutable_manual_order_result(resource):
                raise BillingInvariantError("stored manual order result identity is invalid")
        return resource
    return _validate_stored_result(operation)


def _validate_brand_voice_order_resource(
    operation: BillingOperation,
    resource: BrandVoiceOrderRead,
) -> None:
    expected_operation = f"doubao_brand_voice_order_{resource.order_type}"
    expected_state = {
        "awaiting_fulfillment": ("in_progress", None),
        "fulfilled": ("completed", "succeeded"),
        "rejected": ("completed", "rejected"),
    }[resource.status]
    if (
        operation.operation != expected_operation
        or (operation.status, operation.completion_kind) != expected_state
        or operation.result_type != "brand_voice_order"
        or operation.result_id != resource.id
        or operation.tenant_id != resource.tenant_id
        or operation.user_id != resource.ordered_by_user_id
        or resource.billing.operation_id != operation.id
    ):
        raise BillingInvariantError("manual order billing link is invalid")


def _validate_stored_error_payload(operation: BillingOperation) -> _StoredErrorPayload:
    if (
        operation.error_payload is None
        or operation.error_code is None
        or not _SAFE_CODE.fullmatch(operation.error_code)
        or operation.error_http_status is None
        or not 400 <= operation.error_http_status <= 599
    ):
        raise BillingInvariantError("failed operation is missing its safe error")
    try:
        stored_error = _StoredErrorPayload.model_validate(operation.error_payload)
    except ValidationError as exc:
        raise BillingInvariantError("stored error payload is invalid") from exc
    if len(_payload_bytes(_dump_error_payload(stored_error))) > ERROR_PAYLOAD_MAX_BYTES:
        raise BillingInvariantError("stored error payload exceeds its byte limit")
    return stored_error


def _validate_lookup_usages(
    operation: BillingOperation,
    snapshot: PricingSnapshot,
    usages: Sequence[UsageRecord],
) -> None:
    if not usages:
        raise BillingInvariantError("billing operation has no usage rows")
    ordered = sorted(
        usages,
        key=lambda usage: usage.billing_item_index if usage.billing_item_index is not None else -1,
    )
    if [usage.billing_item_index for usage in ordered] != list(range(len(ordered))):
        raise BillingInvariantError("billing usage item allocation is not contiguous")
    source_subscription_ids = {usage.subscription_id for usage in ordered}
    if None in source_subscription_ids or len(source_subscription_ids) != 1:
        raise BillingInvariantError("billing operation source subscription is invalid")
    line_indexes: set[int] = set()
    quantities_by_line: dict[int, Decimal] = {}
    for usage in ordered:
        if (
            usage.billing_operation_id != operation.id
            or usage.tenant_id != operation.tenant_id
            or usage.subscription_id is None
            or usage.billing_pricing_line_index is None
            or not 0 <= usage.billing_pricing_line_index < len(snapshot.pricing_lines)
        ):
            raise BillingInvariantError("billing usage allocation identity is invalid")
        if (
            not usage.provider.strip()
            or len(usage.provider) > 40
            or (usage.model is not None and len(usage.model) > 80)
            or usage.cost_cents < 0
        ):
            raise BillingInvariantError("billing usage provider data is invalid")
        line_index = usage.billing_pricing_line_index
        line = snapshot.pricing_lines[line_index]
        if usage.capability != line.capability or usage.unit != line.unit:
            raise BillingInvariantError("billing usage line metadata is invalid")
        quantity = Decimal(usage.quantity)
        credits = Decimal(usage.credits)
        if not quantity.is_finite() or quantity < 0 or not credits.is_finite() or credits < 0:
            raise BillingInvariantError("billing usage amount is invalid")
        if credits != quantity * line.unit_credits:
            raise BillingInvariantError("billing usage arithmetic is invalid")
        if line.unit_credits > 0 and quantity <= 0:
            raise BillingInvariantError("positive-price billing usage must have quantity")
        line_indexes.add(line_index)
        quantities_by_line[line_index] = quantities_by_line.get(line_index, Decimal("0")) + quantity
    if line_indexes != set(range(len(snapshot.pricing_lines))):
        raise BillingInvariantError("billing usage does not cover every pricing line")
    if snapshot.pricing_shape == "simple":
        if any(usage.billing_pricing_line_index != 0 for usage in ordered):
            raise BillingInvariantError("simple billing usage must use pricing line zero")
        line = snapshot.pricing_lines[0]
        if line.unit in {"call", "image"} and line.quantity == line.quantity.to_integral_value():
            if len(ordered) != int(line.quantity):
                raise BillingInvariantError("simple billing usage item coverage is invalid")
            if any(
                usage.status == "released" and Decimal(usage.quantity) != Decimal("1")
                for usage in ordered
            ):
                raise BillingInvariantError("released per-item billing usage is invalid")
            if any(
                usage.status == "settled" and Decimal(usage.quantity) > Decimal("1")
                for usage in ordered
            ):
                raise BillingInvariantError("settled per-item billing usage exceeds its quote")
    elif len(ordered) != len(snapshot.pricing_lines) or any(
        usage.billing_item_index != usage.billing_pricing_line_index for usage in ordered
    ):
        raise BillingInvariantError("composite billing usage mapping is invalid")
    if operation.status == "in_progress":
        if any(usage.settled_at is not None for usage in usages):
            raise BillingInvariantError("reserved billing usage has a settlement time")
        _validate_stored_allocations(snapshot, usages, require_reserved=True)
        return
    if any(usage.settled_at is None for usage in usages):
        raise BillingInvariantError("terminal billing usage is missing settlement time")
    if operation.completion_kind in {"failed", "rejected"}:
        if any(usage.status != "released" for usage in usages):
            raise BillingInvariantError("released operation has non-released usage")
        _validate_stored_allocations(snapshot, usages, require_reserved=False)
        return
    if operation.completion_kind == "succeeded":
        if any(usage.status not in {"settled", "released"} for usage in usages):
            raise BillingInvariantError("succeeded operation has invalid usage state")
        if not any(usage.status == "settled" for usage in usages):
            raise BillingInvariantError("succeeded operation has no settled usage")
        if snapshot.pricing_shape == "composite":
            for usage in usages:
                if usage.status != "released":
                    continue
                line = snapshot.pricing_lines[int(usage.billing_pricing_line_index)]
                if (
                    Decimal(usage.quantity) != line.quantity
                    or Decimal(usage.credits) != line.subtotal_credits
                ):
                    raise BillingInvariantError("released composite usage differs from its quote")
        for line_index, line in enumerate(snapshot.pricing_lines):
            if quantities_by_line[line_index] > line.quantity:
                raise BillingInvariantError("terminal usage exceeds quoted line quantity")
        exact = Decimal("0")
        for usage in usages:
            if usage.status == "settled":
                line = snapshot.pricing_lines[int(usage.billing_pricing_line_index)]
                exact += Decimal(usage.quantity) * line.unit_credits
        expected = int(exact.to_integral_value(rounding=ROUND_CEILING))
        if expected != _integer_amount(operation.settled_credits, field="settled_credits"):
            raise BillingInvariantError("settled usage does not match parent settlement")
        return
    raise BillingInvariantError("lookup completion kind is invalid")


def _lookup_operation_once(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    operation: str,
    idempotency_key: UUID,
    allow_stale_reread: bool = True,
) -> BillingOperationLookup | None:
    ecom_tasks: list[VideoTask | None] = []
    if operation in _ECOM_IMAGE_OPERATIONS:
        # One statement observes parent, allocations and immutable task links at
        # the same PostgreSQL READ COMMITTED snapshot while a worker settles.
        records = db.execute(
            select(BillingOperation, UsageRecord, VideoTask)
            .outerjoin(UsageRecord, UsageRecord.billing_operation_id == BillingOperation.id)
            .outerjoin(VideoTask, VideoTask.id == UsageRecord.video_task_id)
            .where(
                BillingOperation.tenant_id == tenant_id,
                BillingOperation.user_id == user_id,
                BillingOperation.operation == operation,
                BillingOperation.idempotency_key == str(idempotency_key),
            )
            .order_by(UsageRecord.billing_item_index)
            .execution_options(populate_existing=not (db.new or db.dirty or db.deleted))
        ).all()
        row = records[0][0] if records else None
        usages = [usage for _, usage, _ in records if usage is not None]
        ecom_tasks = [task for _, usage, task in records if usage is not None]
    else:
        row = _operation_for_key(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            operation=operation,
            idempotency_key=idempotency_key,
        )
        usages = (
            []
            if row is None
            else list(
                db.scalars(
                    select(UsageRecord)
                    .where(UsageRecord.billing_operation_id == row.id)
                    .order_by(UsageRecord.billing_item_index)
                )
            )
        )
    if row is None:
        return None
    summary = billing_summary(row)
    snapshot = _snapshot_from_operation(row)
    _validate_lookup_usages(row, snapshot, usages)
    common = {
        "operation": row.operation,
        "idempotency_key": UUID(row.idempotency_key),
        "billing": summary,
    }
    if row.status == "in_progress":
        resource = None
        if row.operation in _ECOM_IMAGE_OPERATIONS:
            _validate_pending_ecom_batch(row, usages, ecom_tasks)
        elif row.result_type is not None or row.result_payload is not None:
            resource = _lookup_resource(db, row, allow_stale_reread=allow_stale_reread)
        if row.error_code or row.error_http_status or row.error_payload:
            raise BillingInvariantError("in-progress operation contains an error")
        return BillingInProgressLookup(
            **common,
            result_type=row.result_type,
            result_id=row.result_id,
            resource=resource,
        )
    if row.completion_kind == "succeeded":
        if row.error_code or row.error_http_status or row.error_payload:
            raise BillingInvariantError("successful operation contains an error")
        result = _lookup_resource(db, row, allow_stale_reread=allow_stale_reread)
        return BillingSucceededLookup(
            **common,
            result_type=row.result_type,
            result_id=row.result_id,
            result=result,
            resource=result if row.result_id is not None else None,
        )
    if row.completion_kind == "rejected":
        if row.error_code or row.error_http_status or row.error_payload:
            raise BillingInvariantError("rejected operation contains an error payload")
        resource = _lookup_resource(db, row, allow_stale_reread=allow_stale_reread)
        return BillingRejectedLookup(
            **common,
            result_type=row.result_type,
            result_id=row.result_id,
            resource=resource,
        )
    if row.completion_kind == "failed":
        if row.result_type or row.result_id or row.result_payload:
            raise BillingInvariantError("failed operation contains a result")
        if row.error_code is None or row.error_http_status is None or row.error_payload is None:
            raise BillingInvariantError("failed operation is missing its safe error")
        stored_error = _validate_stored_error_payload(row)
        return BillingFailedLookup(
            **common,
            failure=BillingFailure(
                code=row.error_code,
                original_http_status=row.error_http_status,
                detail=stored_error.detail,
            ),
        )
    raise BillingInvariantError("operation lookup state is invalid")


def _validate_pending_ecom_batch(
    operation: BillingOperation,
    usages: Sequence[UsageRecord],
    tasks: Sequence[VideoTask | None],
) -> None:
    if operation.result_type != "ecom_image_batch" or operation.result_payload is not None:
        raise BillingInvariantError("pending e-commerce batch result is invalid")
    try:
        if str(UUID(operation.result_id or "")) != operation.result_id:
            raise ValueError("noncanonical batch identifier")
    except ValueError as exc:
        raise BillingInvariantError("pending e-commerce batch identifier is invalid") from exc
    seen_task_ids: set[str] = set()
    # Tasks can finish before the separate batch settlement commits, so validate
    # immutable allocation links without inferring a result from task status.
    for usage, task in zip(usages, tasks, strict=True):
        if task is None or task.id in seen_task_ids:
            raise BillingInvariantError("pending e-commerce batch task is missing or duplicated")
        params = task.params
        if (
            task.tenant_id != operation.tenant_id
            or task.created_by_user_id != operation.user_id
            or task.mode != "photo"
            or task.video_mode != "photo"
            or not isinstance(params, dict)
            or params.get("kind") != operation.operation
            or params.get("billing_operation_id") != operation.id
            or params.get("batch_id") != operation.result_id
            or type(params.get("billing_item_index")) is not int
            or params["billing_item_index"] != usage.billing_item_index
            or not isinstance(params.get("source_asset_id"), str)
            or not 1 <= len(params["source_asset_id"]) <= 36
        ):
            raise BillingInvariantError("pending e-commerce batch task link is invalid")
        seen_task_ids.add(task.id)


@dataclass(frozen=True)
class _ManualOrderLifecycleView:
    order_id: str
    order_tenant_id: str
    order_user_id: str
    order_type: str
    order_status: str
    billing_operation_id: str
    operation_id: str
    operation_tenant_id: str
    operation_user_id: str
    operation_name: str
    result_type: str | None
    result_id: str | None
    operation_status: str
    completion_kind: str | None
    completed_at: datetime | None


def _manual_order_lifecycle_view(
    db: Session,
    *,
    order_id: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    operation: str | None = None,
    idempotency_key: UUID | None = None,
) -> _ManualOrderLifecycleView | None:
    criteria = [BrandVoiceOrder.billing_operation_id == BillingOperation.id]
    if order_id is not None:
        criteria.append(BrandVoiceOrder.id == order_id)
    if tenant_id is not None:
        criteria.append(BillingOperation.tenant_id == tenant_id)
    if user_id is not None:
        criteria.append(BillingOperation.user_id == user_id)
    if operation is not None:
        criteria.append(BillingOperation.operation == operation)
    if idempotency_key is not None:
        criteria.append(BillingOperation.idempotency_key == str(idempotency_key))
    row = db.execute(
        select(
            BrandVoiceOrder.id,
            BrandVoiceOrder.tenant_id,
            BrandVoiceOrder.user_id,
            BrandVoiceOrder.order_type,
            BrandVoiceOrder.status,
            BrandVoiceOrder.billing_operation_id,
            BillingOperation.id,
            BillingOperation.tenant_id,
            BillingOperation.user_id,
            BillingOperation.operation,
            BillingOperation.result_type,
            BillingOperation.result_id,
            BillingOperation.status,
            BillingOperation.completion_kind,
            BillingOperation.completed_at,
        ).where(*criteria)
    ).one_or_none()
    if row is None:
        return None
    return _ManualOrderLifecycleView(*row)


def _manual_order_lifecycle_is_consistent(view: _ManualOrderLifecycleView) -> bool:
    expected_state = {
        "awaiting_fulfillment": ("in_progress", None, False),
        "fulfilled": ("completed", "succeeded", True),
        "rejected": ("completed", "rejected", True),
    }.get(view.order_status)
    if expected_state is None:
        return False
    expected_status, expected_completion_kind, terminal = expected_state
    return (
        view.order_tenant_id == view.operation_tenant_id
        and view.order_user_id == view.operation_user_id
        and view.operation_name == f"doubao_brand_voice_order_{view.order_type}"
        and view.result_type == "brand_voice_order"
        and view.result_id == view.order_id
        and view.operation_status == expected_status
        and view.completion_kind == expected_completion_kind
        and (view.completed_at is not None) == terminal
    )


def _identity_map_lifecycle_differs(db: Session, view: _ManualOrderLifecycleView) -> bool:
    for candidate in db.identity_map.values():
        state = sqlalchemy_inspect(candidate)
        values = state.dict
        if isinstance(candidate, BrandVoiceOrder) and state.identity == (view.order_id,):
            if "status" in values and values["status"] != view.order_status:
                return True
        elif isinstance(candidate, BillingOperation) and state.identity == (view.operation_id,):
            if {"status", "completion_kind"}.issubset(values) and (
                values["status"] != view.operation_status
                or values["completion_kind"] != view.completion_kind
            ):
                return True
    return False


def manual_order_lifecycle_is_proven_stale(db: Session, *, order_id: str) -> bool:
    """Use a column-only query to distinguish a stale lifecycle view from corruption."""
    view = _manual_order_lifecycle_view(db, order_id=order_id)
    return view is not None and _manual_order_lifecycle_is_consistent(view) and (
        _identity_map_lifecycle_differs(db, view)
    )


def lookup_manual_order_lifecycle_is_proven_stale(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    operation: str,
    idempotency_key: UUID,
) -> bool:
    view = _manual_order_lifecycle_view(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        operation=operation,
        idempotency_key=idempotency_key,
    )
    return view is not None and _manual_order_lifecycle_is_consistent(view) and (
        _identity_map_lifecycle_differs(db, view)
    )


def lookup_operation(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    operation: str,
    idempotency_key: UUID,
) -> BillingOperationLookup | None:
    lookup_args = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "operation": operation,
        "idempotency_key": idempotency_key,
    }
    if (
        not db.new
        and not db.dirty
        and not db.deleted
        and lookup_manual_order_lifecycle_is_proven_stale(db, **lookup_args)
    ):
        db.expire_all()
    return _lookup_operation_once(db, **lookup_args, allow_stale_reread=False)
