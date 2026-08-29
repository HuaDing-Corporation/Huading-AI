from __future__ import annotations

import json
import math
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models import (
    ERROR_PAYLOAD_MAX_BYTES,
    RESULT_PAYLOAD_MAX_BYTES,
    BillingOperation,
    UsageRecord,
)
from app.services import quota
from app.services.billing_quotes import VerifiedQuote, canonical_json
from app.services.pricing import PricingInvariantError, PricingSnapshot, validate_pricing_snapshot

logger = structlog.get_logger(__name__)

_RESULT_SCHEMAS: dict[str, type[BaseModel]] = {}
_RESULT_SCHEMAS_LOCK = threading.Lock()
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
    }
)


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
    detail: object | None


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
    BillingInProgressLookup
    | BillingSucceededLookup
    | BillingRejectedLookup
    | BillingFailedLookup
)


class _StoredErrorPayload(_BillingModel):
    detail: object | None = None


def register_billing_result_schema(result_type: str, schema: type[BaseModel]) -> None:
    if not result_type or len(result_type) > 64 or not issubclass(schema, BaseModel):
        raise ValueError("invalid billing result schema registration")
    with _RESULT_SCHEMAS_LOCK:
        existing = _RESULT_SCHEMAS.get(result_type)
        if existing is not None and existing is not schema:
            raise ValueError(f"billing result type is already registered: {result_type}")
        _RESULT_SCHEMAS[result_type] = schema


def _integer_amount(value: Decimal, *, field: str) -> int:
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
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
        status: Literal["reserved", "settled", "partially_settled", "released"] = (
            "reserved"
        )
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


def _finite_decimal(value: Decimal, *, field: str, allow_zero: bool) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AppError(
            f"Invalid {field}.", code="BILLING_ALLOCATION_INVALID", status_code=500
        ) from exc
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        raise AppError(
            f"Invalid {field}.", code="BILLING_ALLOCATION_INVALID", status_code=500
        )
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
        quantity = _finite_decimal(item.quantity, field="quantity", allow_zero=False)
        credits = _finite_decimal(item.credits, field="credits", allow_zero=True)
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
    elif any(
        item.item_index != item.pricing_line_index
        for item in items
    ) or len(items) != line_count:
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


def _usage_for_update(db: Session, operation_id: str) -> list[UsageRecord]:
    return list(
        db.scalars(
            select(UsageRecord)
            .where(UsageRecord.billing_operation_id == operation_id)
            .order_by(UsageRecord.billing_item_index)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


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


def _sanitize_detail(value: object, *, key: str | None = None) -> object:
    if key is not None and key.casefold() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite detail")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite detail")
        return str(value)
    if isinstance(value, BaseException):
        raise ValueError("raw exceptions are not safe billing details")
    if isinstance(value, Mapping):
        cleaned: dict[str, object] = {}
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise ValueError("detail keys must be strings")
            cleaned[nested_key] = _sanitize_detail(nested_value, key=nested_key)
        return cleaned
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_sanitize_detail(item) for item in value]
    raise ValueError("detail is not schema-safe JSON")


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
        payload = _StoredErrorPayload(detail=_sanitize_detail(detail)).model_dump(mode="json")
    except (ValueError, ValidationError) as exc:
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
    operation = _operation_for_update(db, operation_id)
    if operation.status == "completed":
        return operation
    stored_result = _validate_result_payload(result_type, result_payload)
    snapshot = _snapshot_from_operation(operation)
    usages = _usage_for_update(db, operation.id)
    _validate_stored_allocations(snapshot, usages, require_reserved=True)
    if not usages:
        raise BillingInvariantError("operation has no usage allocations")
    subscription_ids = {usage.subscription_id for usage in usages}
    if len(subscription_ids) != 1 or None in subscription_ids:
        raise BillingInvariantError("operation usages do not share a subscription")
    subscription = quota.lock_subscription_for_billing(
        db,
        subscription_id=next(iter(subscription_ids)),
    )
    usage_by_item = {int(usage.billing_item_index): usage for usage in usages}
    exact_subtotal = Decimal("0")
    parsed_actual: dict[int, Decimal] = {}
    invalid_code: str | None = None
    for item_index, value in actual_quantities.items():
        if isinstance(item_index, bool) or not isinstance(item_index, int):
            invalid_code = "BILLING_ACTUAL_QUANTITY_INVALID"
            break
        usage = usage_by_item.get(item_index)
        if usage is None:
            invalid_code = "BILLING_ACTUAL_QUANTITY_INVALID"
            break
        try:
            actual = _finite_decimal(value, field="actual quantity", allow_zero=False)
        except AppError:
            invalid_code = "BILLING_ACTUAL_QUANTITY_INVALID"
            break
        if actual > Decimal(usage.quantity):
            invalid_code = "BILLING_QUOTE_EXCEEDED"
            break
        line = snapshot.pricing_lines[int(usage.billing_pricing_line_index)]
        parsed_actual[item_index] = actual
        exact_subtotal += actual * line.unit_credits
    if not parsed_actual and snapshot.payable_credits > 0:
        invalid_code = invalid_code or "BILLING_NO_SUCCESSFUL_ALLOCATION"
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
    settled = int(exact_subtotal.to_integral_value(rounding=ROUND_CEILING))
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
            line = snapshot.pricing_lines[int(usage.billing_pricing_line_index)]
            usage.quantity = parsed_actual[item_index]
            usage.credits = parsed_actual[item_index] * line.unit_credits
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
    operation = _operation_for_update(db, operation_id)
    if operation.status == "completed":
        return operation
    error_payload = _validated_error_payload(code, http_status, sanitized_detail)
    snapshot = _snapshot_from_operation(operation)
    usages = _usage_for_update(db, operation.id)
    _validate_stored_allocations(snapshot, usages, require_reserved=True)
    subscription_ids = {usage.subscription_id for usage in usages}
    if len(subscription_ids) != 1 or None in subscription_ids:
        raise BillingInvariantError("operation usages do not share a subscription")
    subscription = quota.lock_subscription_for_billing(
        db,
        subscription_id=next(iter(subscription_ids)),
    )
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


def _validate_lookup_usages(
    operation: BillingOperation,
    snapshot: PricingSnapshot,
    usages: Sequence[UsageRecord],
) -> None:
    if not usages:
        raise BillingInvariantError("billing operation has no usage rows")
    if operation.status == "in_progress":
        _validate_stored_allocations(snapshot, usages, require_reserved=True)
        return
    if operation.completion_kind in {"failed", "rejected"}:
        if any(usage.status != "released" for usage in usages):
            raise BillingInvariantError("released operation has non-released usage")
        return
    if operation.completion_kind == "succeeded":
        if any(usage.status == "reserved" for usage in usages):
            raise BillingInvariantError("succeeded operation retains a reservation")
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


def lookup_operation(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    operation: str,
    idempotency_key: UUID,
) -> BillingOperationLookup | None:
    row = _operation_for_key(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        operation=operation,
        idempotency_key=idempotency_key,
    )
    if row is None:
        return None
    summary = billing_summary(row)
    snapshot = _snapshot_from_operation(row)
    usages = list(
        db.scalars(
            select(UsageRecord)
            .where(UsageRecord.billing_operation_id == row.id)
            .order_by(UsageRecord.billing_item_index)
        )
    )
    _validate_lookup_usages(row, snapshot, usages)
    common = {
        "operation": row.operation,
        "idempotency_key": UUID(row.idempotency_key),
        "billing": summary,
    }
    if row.status == "in_progress":
        resource = None
        if row.result_type is not None or row.result_payload is not None:
            resource = _validate_stored_result(row)
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
        result = _validate_stored_result(row)
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
        resource = _validate_stored_result(row)
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
        try:
            stored_error = _StoredErrorPayload.model_validate(row.error_payload)
        except ValidationError as exc:
            raise BillingInvariantError("stored error payload is invalid") from exc
        if len(_payload_bytes(stored_error.model_dump(mode="json"))) > ERROR_PAYLOAD_MAX_BYTES:
            raise BillingInvariantError("stored error payload exceeds its byte limit")
        return BillingFailedLookup(
            **common,
            failure=BillingFailure(
                code=row.error_code,
                original_http_status=row.error_http_status,
                detail=stored_error.detail,
            ),
        )
    raise BillingInvariantError("operation lookup state is invalid")
