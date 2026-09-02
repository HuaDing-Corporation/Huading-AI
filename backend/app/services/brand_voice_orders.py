from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Literal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session, sessionmaker

from app.api.deps import BillingSubmissionHeaders
from app.core.exceptions import AppError
from app.core.utils import base_mime
from app.db.models import (
    AdminAuditLog,
    Asset,
    BillingOperation,
    BrandVoice,
    BrandVoiceOrder,
    BrandVoiceProviderId,
    CreditRefundGrant,
    Subscription,
    Tenant,
    UsageRecord,
    User,
)
from app.schemas.brand_voice_orders import BrandVoiceOrderCreateRequest, BrandVoiceOrderRead
from app.schemas.common import Page
from app.services import provider_voice_registry, quota
from app.services.billing_operations import (
    BillingInvariantError,
    UsageAllocation,
    billing_summary,
    create_reserved_operation,
    find_replay,
    manual_order_lifecycle_is_proven_stale,
    register_billing_result_schema,
)
from app.services.billing_quotes import VerifiedQuote, request_sha256
from app.services.plan_access import (
    is_authorized_platform_admin,
    require_doubao_voice_clone_access,
)
from app.services.pricing import (
    PRICING_POLICIES,
    RateScope,
    RateSource,
    build_simple_pricing,
    resolve_rate,
    validate_pricing_snapshot,
)
from app.services.storage.keys import is_tenant_storage_key
from app.services.subscription import (
    decide_credit_refund,
    lock_tenant_for_subscription_lifecycle,
    refund_subscriptions_for_update,
)
from app.services.transaction_retry import run_db_transaction_with_retry

DOUBAO_PROVIDER = "doubao-voice-clone"
MANUAL_MODEL = "manual_fulfillment"
ORDER_CREDITS = 30_000
_ALLOWED_AUDIO_TYPES = {
    "audio/aac",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
}

register_billing_result_schema("brand_voice_order", BrandVoiceOrderRead)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def brand_voice_order_operation(payload: BrandVoiceOrderCreateRequest) -> str:
    return f"doubao_brand_voice_order_{payload.order_type}"


def brand_voice_order_request_hash(payload: BrandVoiceOrderCreateRequest) -> str:
    return request_sha256(
        {
            "operation": brand_voice_order_operation(payload),
            "payload": payload.model_dump(mode="json"),
        }
    )


def brand_voice_order_pricing_draft(
    db: Session, *, tenant_id: str, payload: BrandVoiceOrderCreateRequest
):
    policy = PRICING_POLICIES[brand_voice_order_operation(payload)]
    return build_simple_pricing(
        policy=policy,
        rate=resolve_rate(db, tenant_id=tenant_id, policy=policy),
        quantity=Decimal("1"),
    )


def _assert_verified_manual_quote(verified_quote: VerifiedQuote, *, operation_name: str) -> None:
    snapshot = verified_quote.snapshot
    if len(snapshot.pricing_lines) != 1:
        raise AppError(
            "Quote operation or amount is invalid.",
            code="QUOTE_REQUEST_MISMATCH",
            status_code=422,
        )
    line = snapshot.pricing_lines[0]
    if (
        snapshot.operation != operation_name
        or snapshot.pricing_shape != "simple"
        or snapshot.payable_credits != ORDER_CREDITS
        or snapshot.subtotal_credits != ORDER_CREDITS
        or line.operation != operation_name
        or line.capability != "voice_clone"
        or line.unit != "call"
        or line.quantity != 1
        or line.unit_credits != ORDER_CREDITS
        or line.subtotal_credits != ORDER_CREDITS
        or line.rate_scope is not RateScope.PLATFORM_FIXED
        or line.rate.source not in {RateSource.PLATFORM_RATE, RateSource.CODE_DEFAULT}
        or (line.rate.source is RateSource.CODE_DEFAULT and line.rate.policy_key != operation_name)
    ):
        raise AppError(
            "Quote operation or amount is invalid.",
            code="QUOTE_REQUEST_MISMATCH",
            status_code=422,
        )


def _source_asset_statement(*, tenant_id: str, asset_id: str, for_update: bool):
    statement = select(Asset).where(Asset.id == asset_id, Asset.tenant_id == tenant_id)
    return statement.with_for_update() if for_update else statement


def _validate_source_asset(asset: Asset | None, *, tenant_id: str) -> Asset:
    if (
        asset is None
        or asset.type != "audio"
        or asset.status != "ready"
        or asset.deleted_at is not None
        or not asset.storage_key
    ):
        raise AppError(
            "Source audio asset not found.",
            code="SOURCE_AUDIO_ASSET_NOT_FOUND",
            status_code=404,
        )
    if base_mime(asset.mime_type) not in _ALLOWED_AUDIO_TYPES:
        raise AppError(
            "Unsupported source audio type.",
            code="UNSUPPORTED_SOURCE_AUDIO_TYPE",
            status_code=415,
        )
    if not is_tenant_storage_key(tenant_id, asset.storage_key):
        raise AppError(
            "Invalid source audio storage key.",
            code="INVALID_SOURCE_AUDIO_KEY",
            status_code=422,
        )
    if asset.duration_ms is not None and asset.duration_ms < 5_000:
        raise AppError(
            "Source audio is too short.",
            code="SOURCE_AUDIO_TOO_SHORT",
            status_code=422,
        )
    return asset


def _renewal_voice_statement(*, voice_id: str, tenant_id: str, for_update: bool):
    statement = select(BrandVoice).where(
        BrandVoice.id == voice_id,
        BrandVoice.tenant_id == tenant_id,
    )
    return statement.with_for_update() if for_update else statement


def _validate_renewal_voice(
    voice: BrandVoice | None,
    *,
    user_id: str,
    now: datetime,
) -> BrandVoice:
    expires_at = _as_utc(voice.expires_at) if voice is not None and voice.expires_at else None
    if (
        voice is None
        or voice.owner_user_id != user_id
        or voice.provider != DOUBAO_PROVIDER
        or voice.speaker_id is None
        or not voice.speaker_id.strip()
        or voice.status != "ready"
        or voice.deleted_at is not None
        or expires_at is None
        or expires_at > _as_utc(now)
    ):
        raise AppError(
            "Renewal brand voice not found or not eligible.",
            code="BRAND_VOICE_RENEWAL_NOT_ELIGIBLE",
            status_code=422,
        )
    return voice


def validate_brand_voice_order_materials(
    db: Session,
    *,
    user: User,
    payload: BrandVoiceOrderCreateRequest,
    now: datetime | None = None,
    for_update: bool = False,
) -> tuple[Asset, BrandVoice | None]:
    current_time = now or datetime.now(UTC)
    if not user.is_active or user.status != "active" or user.deleted_at is not None:
        raise AppError(
            "User is inactive or does not exist.",
            code="USER_NOT_FOUND",
            status_code=401,
        )
    require_doubao_voice_clone_access(db, tenant_id=user.tenant_id)
    source = _validate_source_asset(
        db.scalar(
            _source_asset_statement(
                tenant_id=user.tenant_id,
                asset_id=payload.source_audio_asset_id,
                for_update=for_update,
            )
        ),
        tenant_id=user.tenant_id,
    )
    renewal = None
    if payload.order_type == "renew":
        renewal = _validate_renewal_voice(
            db.scalar(
                _renewal_voice_statement(
                    voice_id=str(payload.existing_brand_voice_id),
                    tenant_id=user.tenant_id,
                    for_update=for_update,
                )
            ),
            user_id=user.id,
            now=current_time,
        )
        if renewal.source_audio_asset_id == source.id:
            raise AppError(
                "Renewal requires a different source audio asset.",
                code="BRAND_VOICE_RENEWAL_SOURCE_AUDIO_REUSED",
                status_code=422,
            )
        awaiting = db.scalar(
            select(BrandVoiceOrder.id).where(
                BrandVoiceOrder.existing_brand_voice_id == renewal.id,
                BrandVoiceOrder.status == "awaiting_fulfillment",
            )
        )
        if awaiting is not None:
            raise AppError(
                "This brand voice already has a pending renewal order.",
                code="BRAND_VOICE_RENEWAL_ALREADY_PENDING",
                status_code=409,
            )
    return source, renewal


def _create_brand_voice_order_in_transaction(
    db: Session,
    *,
    user: User,
    payload: BrandVoiceOrderCreateRequest,
    verified_quote: VerifiedQuote,
    submission_headers: BillingSubmissionHeaders,
    now: datetime | None = None,
) -> BrandVoiceOrder:
    current_time = now or datetime.now(UTC)
    operation_name = brand_voice_order_operation(payload)
    request_hash = brand_voice_order_request_hash(payload)
    replay = find_replay(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation=operation_name,
        idempotency_key=submission_headers.idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        order = db.scalar(
            select(BrandVoiceOrder).where(
                BrandVoiceOrder.billing_operation_id == replay.operation.id,
                BrandVoiceOrder.tenant_id == user.tenant_id,
                BrandVoiceOrder.user_id == user.id,
            )
        )
        if order is None:
            raise AppError(
                "Stored manual order is missing.",
                code="BILLING_INVARIANT_VIOLATION",
                status_code=500,
            )
        return order

    tenant = lock_tenant_for_subscription_lifecycle(db, tenant_id=user.tenant_id)
    quota.lock_active_subscription(db, tenant_id=user.tenant_id)
    locked_user = db.scalar(
        select(User)
        .where(User.id == user.id, User.tenant_id == user.tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not isinstance(tenant, Tenant)
        or tenant.status != "active"
        or tenant.deleted_at is not None
        or locked_user is None
        or not locked_user.is_active
        or locked_user.status != "active"
        or locked_user.deleted_at is not None
    ):
        raise AppError(
            "Tenant or user is inactive.",
            code="ORDER_PRINCIPAL_INACTIVE",
            status_code=409,
        )
    provider_voice_registry.assert_doubao_registry_ready(db)
    source, renewal = validate_brand_voice_order_materials(
        db,
        user=locked_user,
        payload=payload,
        now=current_time,
        for_update=True,
    )
    _assert_verified_manual_quote(verified_quote, operation_name=operation_name)
    order_id = str(uuid4())
    operation = create_reserved_operation(
        db,
        tenant_id=locked_user.tenant_id,
        user_id=locked_user.id,
        operation=operation_name,
        idempotency_key=submission_headers.idempotency_key,
        request_hash=request_hash,
        verified_quote=verified_quote,
        usage_allocations=(
            UsageAllocation(
                item_index=0,
                pricing_line_index=0,
                quantity=Decimal("1"),
                credits=Decimal(ORDER_CREDITS),
                provider=DOUBAO_PROVIDER,
                model=MANUAL_MODEL,
                video_task_id=None,
            ),
        ),
        result_type="brand_voice_order",
        result_id=order_id,
    )
    if operation.result_id != order_id:
        existing = db.scalar(
            select(BrandVoiceOrder).where(
                BrandVoiceOrder.billing_operation_id == operation.id,
                BrandVoiceOrder.tenant_id == locked_user.tenant_id,
                BrandVoiceOrder.user_id == locked_user.id,
            )
        )
        if existing is None:
            raise AppError(
                "Stored manual order is missing.",
                code="BILLING_INVARIANT_VIOLATION",
                status_code=500,
            )
        return existing
    order = BrandVoiceOrder(
        id=order_id,
        tenant_id=locked_user.tenant_id,
        user_id=locked_user.id,
        order_type=payload.order_type,
        requested_name=payload.requested_name,
        source_audio_asset_id=source.id,
        source_metadata_snapshot={
            "mime_type": source.mime_type,
            "size_bytes": source.size_bytes,
            "duration_ms": source.duration_ms,
            "original_filename": str((source.metadata_ or {}).get("original_filename") or "")[:255],
        },
        consent_confirmed_at=current_time,
        existing_brand_voice_id=renewal.id if renewal is not None else None,
        billing_operation_id=operation.id,
        status="awaiting_fulfillment",
        created_at=current_time,
        updated_at=current_time,
    )
    db.add(order)
    db.flush()
    return order


def create_brand_voice_order(
    db: Session,
    *,
    user: User,
    payload: BrandVoiceOrderCreateRequest,
    verified_quote: VerifiedQuote,
    submission_headers: BillingSubmissionHeaders,
    now: datetime | None = None,
) -> BrandVoiceOrder:
    operation_name = brand_voice_order_operation(payload)
    request_hash = brand_voice_order_request_hash(payload)
    replay = find_replay(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation=operation_name,
        idempotency_key=submission_headers.idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        order = db.scalar(
            select(BrandVoiceOrder).where(
                BrandVoiceOrder.billing_operation_id == replay.operation.id,
                BrandVoiceOrder.tenant_id == user.tenant_id,
                BrandVoiceOrder.user_id == user.id,
            )
        )
        if order is None:
            raise BillingInvariantError("replayed manual order is missing")
        return order
    user_id = user.id
    tenant_id = user.tenant_id
    bind = db.get_bind()
    db.rollback()
    factory = sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def transaction(transaction_db: Session) -> BrandVoiceOrder:
        current_user = transaction_db.scalar(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        )
        if current_user is None:
            raise AppError(
                "User is inactive or does not exist.",
                code="USER_NOT_FOUND",
                status_code=401,
            )
        return _create_brand_voice_order_in_transaction(
            transaction_db,
            user=current_user,
            payload=payload,
            verified_quote=verified_quote,
            submission_headers=submission_headers,
            now=now,
        )

    return run_db_transaction_with_retry(factory, transaction)


def list_user_brand_voice_orders(
    db: Session, *, tenant_id: str, user_id: str
) -> list[BrandVoiceOrder]:
    return list(
        db.scalars(
            select(BrandVoiceOrder)
            .where(
                BrandVoiceOrder.tenant_id == tenant_id,
                BrandVoiceOrder.user_id == user_id,
            )
            .order_by(BrandVoiceOrder.created_at.desc(), BrandVoiceOrder.id.desc())
        )
    )


def list_admin_brand_voice_orders(
    db: Session,
    *,
    status: str | None,
    page: int,
    page_size: int,
) -> Page[BrandVoiceOrder]:
    criteria = []
    if status is not None:
        criteria.append(BrandVoiceOrder.status == status)
    total = int(db.scalar(select(func.count()).select_from(BrandVoiceOrder).where(*criteria)) or 0)
    items = list(
        db.scalars(
            select(BrandVoiceOrder)
            .where(*criteria)
            .order_by(BrandVoiceOrder.created_at.asc(), BrandVoiceOrder.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page.model_construct(items=items, total=total)


def get_admin_brand_voice_order(db: Session, *, order_id: str) -> BrandVoiceOrder:
    order = db.get(BrandVoiceOrder, order_id)
    if order is None:
        raise AppError(
            "Brand voice order not found.",
            code="BRAND_VOICE_ORDER_NOT_FOUND",
            status_code=404,
        )
    return order


def get_user_brand_voice_order(
    db: Session, *, tenant_id: str, user_id: str, order_id: str
) -> BrandVoiceOrder:
    order = db.scalar(
        select(BrandVoiceOrder).where(
            BrandVoiceOrder.id == order_id,
            BrandVoiceOrder.tenant_id == tenant_id,
            BrandVoiceOrder.user_id == user_id,
        )
    )
    if order is None:
        raise AppError(
            "Brand voice order not found.", code="BRAND_VOICE_ORDER_NOT_FOUND", status_code=404
        )
    return order


def _validate_brand_voice_order_operation(
    *,
    order: BrandVoiceOrder,
    operation: BillingOperation,
) -> None:
    expected_terminal = {
        "awaiting_fulfillment": ("in_progress", None, 0, 0, False),
        "fulfilled": ("completed", "succeeded", ORDER_CREDITS, 0, True),
        "rejected": ("completed", "rejected", 0, ORDER_CREDITS, True),
    }.get(order.status)
    if expected_terminal is None:
        raise BillingInvariantError("manual order status is invalid")
    expected_status, expected_completion, expected_settled, expected_released, is_terminal = (
        expected_terminal
    )
    try:
        requested = Decimal(operation.requested_credits)
        settled = Decimal(operation.settled_credits)
        released = Decimal(operation.released_credits)
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise BillingInvariantError("manual order billing amount is invalid") from exc
    if (
        order.billing_operation_id != operation.id
        or order.tenant_id != operation.tenant_id
        or order.user_id != operation.user_id
        or operation.operation != f"doubao_brand_voice_order_{order.order_type}"
        or operation.result_type != "brand_voice_order"
        or operation.result_id != order.id
        or operation.status != expected_status
        or operation.completion_kind != expected_completion
        or (operation.completed_at is not None) != is_terminal
        or requested != ORDER_CREDITS
        or settled != expected_settled
        or released != expected_released
        or operation.error_code is not None
        or operation.error_http_status is not None
        or operation.error_payload is not None
    ):
        raise BillingInvariantError("manual order billing link is invalid")


def _manual_order_source_subscription_id(
    db: Session,
    *,
    operation: BillingOperation,
) -> str:
    source_subscription_ids = set(
        db.scalars(
            select(UsageRecord.subscription_id).where(
                UsageRecord.billing_operation_id == operation.id
            )
        )
    )
    if None in source_subscription_ids or len(source_subscription_ids) != 1:
        raise BillingInvariantError("manual order source subscription is invalid")
    source_subscription_id = next(iter(source_subscription_ids))
    source_subscription = db.get(Subscription, source_subscription_id)
    if source_subscription is None or source_subscription.tenant_id != operation.tenant_id:
        raise BillingInvariantError("manual order source subscription is invalid")
    return source_subscription_id


def _subscription_period_contains(subscription: Subscription, timestamp: datetime) -> bool:
    checked_at = _as_utc(timestamp)
    return _as_utc(subscription.period_start) <= checked_at <= _as_utc(subscription.period_end)


def _brand_voice_order_read_once(db: Session, *, order: BrandVoiceOrder) -> BrandVoiceOrderRead:
    operation = db.get(BillingOperation, order.billing_operation_id)
    if operation is None:
        raise AppError(
            "Stored manual order billing operation is missing.",
            code="BILLING_INVARIANT_VIOLATION",
            status_code=500,
        )
    _validate_brand_voice_order_operation(order=order, operation=operation)
    source_subscription_id = _manual_order_source_subscription_id(
        db,
        operation=operation,
    )
    provider_voice_id = None
    expires_at = None
    if order.fulfilled_provider_id is not None:
        registry = db.get(BrandVoiceProviderId, order.fulfilled_provider_id)
        if registry is None:
            raise AppError(
                "Stored provider voice registration is missing.",
                code="BILLING_INVARIANT_VIOLATION",
                status_code=500,
            )
        provider_voice_id = registry.normalized_provider_id
    if order.status == "fulfilled":
        if (
            order.fulfilled_brand_voice_id is None
            or order.fulfilled_provider_id is None
            or order.fulfilled_at is None
            or order.rejected_at is not None
            or order.rejection_reason is not None
        ):
            raise AppError(
                "Fulfilled manual order has inconsistent terminal fields.",
                code="BILLING_INVARIANT_VIOLATION",
                status_code=500,
            )
        delivered_voice = db.get(BrandVoice, order.fulfilled_brand_voice_id)
        fulfilled_at = _as_utc(order.fulfilled_at)
        activated_at = (
            _as_utc(delivered_voice.activated_at)
            if delivered_voice is not None and delivered_voice.activated_at is not None
            else None
        )
        delivered_expires_at = (
            _as_utc(delivered_voice.expires_at)
            if delivered_voice is not None and delivered_voice.expires_at is not None
            else None
        )
        completed_at = (
            _as_utc(operation.completed_at) if operation.completed_at is not None else None
        )
        if (
            delivered_voice is None
            or delivered_voice.tenant_id != order.tenant_id
            or delivered_voice.owner_user_id != order.user_id
            or completed_at != fulfilled_at
            or activated_at != fulfilled_at
            or delivered_expires_at != completed_at + timedelta(days=365)
        ):
            raise BillingInvariantError("fulfilled manual order terminal clock is invalid")
        expires_at = delivered_voice.expires_at
    grant = db.scalar(
        select(CreditRefundGrant).where(CreditRefundGrant.billing_operation_id == operation.id)
    )
    source_subscription = db.get(Subscription, source_subscription_id)
    if grant is not None:
        if (
            order.status != "rejected"
            or operation.status != "completed"
            or operation.completion_kind != "rejected"
            or operation.completed_at is None
            or order.rejected_at is None
            or _as_utc(order.rejected_at) != _as_utc(operation.completed_at)
        ):
            raise BillingInvariantError("manual order refund grant is invalid")
        target_subscription = (
            db.get(Subscription, grant.target_subscription_id)
            if grant.target_subscription_id is not None
            else None
        )
        grant_state_invalid = (
            grant.status == "pending"
            and (grant.target_subscription_id is not None or grant.applied_at is not None)
        ) or (
            grant.status == "applied"
            and (
                target_subscription is None
                or grant.applied_at is None
                or _as_utc(grant.applied_at) < _as_utc(operation.completed_at)
                or target_subscription.tenant_id != operation.tenant_id
                or target_subscription.id == source_subscription_id
                or not _subscription_period_contains(target_subscription, grant.applied_at)
            )
        )
        try:
            grant_amount = Decimal(grant.amount_credits)
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise BillingInvariantError("manual order refund amount is invalid") from exc
        if (
            not grant_amount.is_finite()
            or grant_amount != ORDER_CREDITS
            or grant_amount != Decimal(operation.requested_credits)
            or grant.tenant_id != operation.tenant_id
            or grant.tenant_id != order.tenant_id
            or grant.user_id != operation.user_id
            or grant.user_id != order.user_id
            or grant.source_subscription_id != source_subscription_id
            or source_subscription is None
            or source_subscription.tenant_id != operation.tenant_id
            or Decimal(operation.settled_credits) != 0
            or Decimal(operation.released_credits) != ORDER_CREDITS
            or grant.status not in {"pending", "applied"}
            or grant_state_invalid
        ):
            raise BillingInvariantError("manual order refund grant is invalid")
    elif order.status == "rejected":
        if (
            order.rejected_at is None
            or operation.completed_at is None
            or _as_utc(order.rejected_at) != _as_utc(operation.completed_at)
            or source_subscription is None
            or source_subscription.tenant_id != operation.tenant_id
            or not _subscription_period_contains(source_subscription, operation.completed_at)
        ):
            raise BillingInvariantError("manual order source refund release is invalid")
    if order.status != "rejected":
        refund_disposition = "not_applicable"
    elif grant is None:
        refund_disposition = "source_subscription_released"
    elif grant.status == "applied":
        refund_disposition = "current_subscription_credited"
    else:
        refund_disposition = "pending_next_subscription"
    return BrandVoiceOrderRead(
        id=order.id,
        tenant_id=order.tenant_id,
        ordered_by_user_id=order.user_id,
        order_type=order.order_type,
        requested_name=order.requested_name,
        source_audio_asset_id=order.source_audio_asset_id,
        existing_brand_voice_id=order.existing_brand_voice_id,
        status=order.status,
        fulfilled_brand_voice_id=order.fulfilled_brand_voice_id,
        fulfilled_provider_voice_id=provider_voice_id,
        rejection_reason=order.rejection_reason,
        fulfilled_at=order.fulfilled_at,
        expires_at=expires_at,
        rejected_at=order.rejected_at,
        created_at=order.created_at,
        updated_at=order.updated_at,
        billing=billing_summary(operation).model_dump(mode="python"),
        refund_disposition=refund_disposition,
        refund_grant_status=grant.status if grant is not None else None,
        refund_applied_at=grant.applied_at if grant is not None else None,
    )


def brand_voice_order_read(
    db: Session,
    *,
    order: BrandVoiceOrder,
    allow_stale_reread: bool = True,
) -> BrandVoiceOrderRead:
    order_id = order.id
    if (
        allow_stale_reread
        and object_session(order) is db
        and not db.new
        and not db.dirty
        and not db.deleted
        and manual_order_lifecycle_is_proven_stale(db, order_id=order_id)
    ):
        db.expire_all()
        refreshed_order = db.scalar(
            select(BrandVoiceOrder)
            .where(BrandVoiceOrder.id == order_id)
            .execution_options(populate_existing=True)
        )
        if refreshed_order is None:
            raise BillingInvariantError("manual order disappeared during consistent reread")
        order = refreshed_order
    return _brand_voice_order_read_once(db, order=order)


def user_brand_voice_order_read(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    order_id: str,
    allow_stale_reread: bool = True,
) -> BrandVoiceOrderRead:
    return brand_voice_order_read(
        db,
        order=get_user_brand_voice_order(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            order_id=order_id,
        ),
        allow_stale_reread=allow_stale_reread,
    )


@dataclass(frozen=True)
class _ResolveLocator:
    order_id: str
    tenant_id: str
    user_id: str
    operation_id: str
    source_asset_id: str
    existing_brand_voice_id: str | None
    source_subscription_id: str
    usage_ids: tuple[str, ...]
    previous_provider_voice_id: str | None


def _resolve_locator(db: Session, *, order_id: str) -> _ResolveLocator:
    row = db.execute(
        select(
            BrandVoiceOrder.id,
            BrandVoiceOrder.tenant_id,
            BrandVoiceOrder.user_id,
            BrandVoiceOrder.billing_operation_id,
            BrandVoiceOrder.source_audio_asset_id,
            BrandVoiceOrder.existing_brand_voice_id,
        ).where(BrandVoiceOrder.id == order_id)
    ).one_or_none()
    if row is None:
        raise AppError(
            "Brand voice order not found.",
            code="BRAND_VOICE_ORDER_NOT_FOUND",
            status_code=404,
        )
    usages = list(
        db.execute(
            select(UsageRecord.id, UsageRecord.subscription_id)
            .where(UsageRecord.billing_operation_id == row.billing_operation_id)
            .order_by(UsageRecord.id)
        )
    )
    source_ids = {item.subscription_id for item in usages if item.subscription_id is not None}
    if len(source_ids) != 1:
        raise BillingInvariantError("manual order source subscription discovery is invalid")
    previous_provider_voice_id = None
    if row.existing_brand_voice_id is not None:
        previous_provider_voice_id = db.scalar(
            select(BrandVoice.speaker_id).where(BrandVoice.id == row.existing_brand_voice_id)
        )
    return _ResolveLocator(
        order_id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        operation_id=row.billing_operation_id,
        source_asset_id=row.source_audio_asset_id,
        existing_brand_voice_id=row.existing_brand_voice_id,
        source_subscription_id=next(iter(source_ids)),
        usage_ids=tuple(item.id for item in usages),
        previous_provider_voice_id=previous_provider_voice_id,
    )


def _operation_for_update(db: Session, *, operation_id: str) -> BillingOperation:
    operation = db.scalar(
        select(BillingOperation)
        .where(BillingOperation.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if operation is None:
        raise BillingInvariantError("manual order operation is missing")
    return operation


def _subscriptions_for_update(db: Session, *, subscription_ids: set[str]) -> list[Subscription]:
    subscriptions = list(
        db.scalars(
            select(Subscription)
            .where(Subscription.id.in_(sorted(subscription_ids)))
            .order_by(Subscription.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if {item.id for item in subscriptions} != subscription_ids:
        raise BillingInvariantError("manual order subscription lock set is invalid")
    return subscriptions


def _usage_for_update(db: Session, *, usage_ids: tuple[str, ...]) -> list[UsageRecord]:
    usages = list(
        db.scalars(
            select(UsageRecord)
            .where(UsageRecord.id.in_(usage_ids))
            .order_by(UsageRecord.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if tuple(item.id for item in usages) != tuple(sorted(usage_ids)):
        raise BillingInvariantError("manual order usage lock set is invalid")
    return usages


def _expected_reserved_credits(db: Session, *, subscription_id: str) -> int:
    linked = list(
        db.execute(
            select(BillingOperation.id, BillingOperation.requested_credits)
            .join(UsageRecord, UsageRecord.billing_operation_id == BillingOperation.id)
            .where(
                UsageRecord.subscription_id == subscription_id,
                UsageRecord.status == "reserved",
                BillingOperation.status == "in_progress",
            )
            .distinct()
        )
    )
    linked_by_operation = {row.id: Decimal(row.requested_credits) for row in linked}
    legacy = list(
        db.scalars(
            select(UsageRecord.credits).where(
                UsageRecord.subscription_id == subscription_id,
                UsageRecord.status == "reserved",
                UsageRecord.billing_operation_id.is_(None),
            )
        )
    )
    total = sum(linked_by_operation.values(), Decimal("0"))
    total += sum(
        (Decimal(value).to_integral_value(rounding=ROUND_CEILING) for value in legacy),
        Decimal("0"),
    )
    if not total.is_finite() or total < 0 or total != total.to_integral_value():
        raise BillingInvariantError("expected reserved wallet amount is invalid")
    return int(total)


def _assert_wallet_reconciled(db: Session, subscriptions: list[Subscription]) -> None:
    for subscription in subscriptions:
        expected = _expected_reserved_credits(db, subscription_id=subscription.id)
        if subscription.quota_credits_reserved != expected:
            raise BillingInvariantError(f"subscription {subscription.id} reserved wallet mismatch")


def _validate_resolve_financial_state(
    *,
    locator: _ResolveLocator,
    operation: BillingOperation,
    usages: list[UsageRecord],
    source_subscription: Subscription,
) -> None:
    if (
        operation.id != locator.operation_id
        or operation.tenant_id != locator.tenant_id
        or operation.user_id != locator.user_id
        or operation.operation
        not in {
            "doubao_brand_voice_order_create",
            "doubao_brand_voice_order_renew",
        }
        or operation.status != "in_progress"
        or operation.completion_kind is not None
        or operation.completed_at is not None
        or operation.result_type != "brand_voice_order"
        or operation.result_id != locator.order_id
        or Decimal(operation.requested_credits) != Decimal(ORDER_CREDITS)
        or Decimal(operation.settled_credits) != 0
        or Decimal(operation.released_credits) != 0
        or len(usages) != 1
        or source_subscription.tenant_id != locator.tenant_id
    ):
        raise BillingInvariantError("manual order operation invariant is invalid")
    usage = usages[0]
    if (
        usage.billing_operation_id != operation.id
        or usage.subscription_id != source_subscription.id
        or usage.tenant_id != locator.tenant_id
        or usage.status != "reserved"
        or usage.settled_at is not None
        or usage.billing_item_index != 0
        or usage.billing_pricing_line_index != 0
        or usage.capability != "voice_clone"
        or usage.provider != DOUBAO_PROVIDER
        or usage.model != MANUAL_MODEL
        or usage.video_task_id is not None
        or usage.unit != "call"
        or Decimal(usage.quantity) != 1
        or Decimal(usage.credits) != ORDER_CREDITS
    ):
        raise BillingInvariantError("manual order usage invariant is invalid")
    try:
        snapshot = validate_pricing_snapshot(operation.pricing_snapshot)
    except Exception as exc:
        raise BillingInvariantError("manual order pricing snapshot is invalid") from exc
    if len(snapshot.pricing_lines) != 1:
        raise BillingInvariantError("manual order pricing line count is invalid")
    line = snapshot.pricing_lines[0]
    if (
        snapshot.operation != operation.operation
        or snapshot.pricing_shape != "simple"
        or snapshot.payable_credits != ORDER_CREDITS
        or snapshot.subtotal_credits != ORDER_CREDITS
        or line.operation != operation.operation
        or line.capability != "voice_clone"
        or line.unit != "call"
        or line.quantity != 1
        or line.unit_credits != ORDER_CREDITS
        or line.subtotal_credits != ORDER_CREDITS
        or line.rate_scope is not RateScope.PLATFORM_FIXED
        or line.rate.source not in {RateSource.PLATFORM_RATE, RateSource.CODE_DEFAULT}
        or (
            line.rate.source is RateSource.CODE_DEFAULT
            and line.rate.policy_key != operation.operation
        )
    ):
        raise BillingInvariantError("manual order fixed-price snapshot is invalid")
    if source_subscription.quota_credits_reserved < ORDER_CREDITS:
        raise BillingInvariantError("manual order source hold is missing")


def _terminal_replay(
    db: Session,
    *,
    locator: _ResolveLocator,
    operation: BillingOperation,
    action: Literal["fulfill", "reject"],
    provider_voice_id: str | None,
    rejection_reason: str | None,
) -> BrandVoiceOrder | None:
    order = db.get(BrandVoiceOrder, locator.order_id, populate_existing=True)
    if order is None or order.status == "awaiting_fulfillment":
        return None
    expected_completion = "succeeded" if order.status == "fulfilled" else "rejected"
    expected_settled = ORDER_CREDITS if order.status == "fulfilled" else 0
    if (
        order.tenant_id != locator.tenant_id
        or order.user_id != locator.user_id
        or order.billing_operation_id != operation.id
        or operation.tenant_id != locator.tenant_id
        or operation.user_id != locator.user_id
        or operation.status != "completed"
        or operation.completion_kind != expected_completion
        or operation.completed_at is None
        or operation.result_type != "brand_voice_order"
        or operation.result_id != order.id
        or Decimal(operation.requested_credits) != ORDER_CREDITS
        or Decimal(operation.settled_credits) != expected_settled
        or Decimal(operation.released_credits) != ORDER_CREDITS - expected_settled
    ):
        raise BillingInvariantError("terminal manual order replay invariant is invalid")
    same = False
    if action == "fulfill" and order.status == "fulfilled" and provider_voice_id is not None:
        registry = db.get(BrandVoiceProviderId, order.fulfilled_provider_id)
        same = bool(
            registry is not None
            and registry.normalized_provider_id
            == provider_voice_registry.normalize_provider_voice_id(provider_voice_id)
        )
    elif action == "reject" and order.status == "rejected":
        same = order.rejection_reason == rejection_reason
    if same:
        return order
    raise AppError(
        "Brand voice order has already been resolved.",
        code="BRAND_VOICE_ORDER_ALREADY_RESOLVED",
        status_code=409,
    )


def _lock_provider_stage(
    db: Session,
    *,
    provider_voice_id: str | None,
    previous_provider_voice_id: str | None,
) -> None:
    identifiers = []
    if provider_voice_id is not None:
        identifiers.append(provider_voice_id)
    if previous_provider_voice_id is not None:
        identifiers.append(previous_provider_voice_id)
    normalized = sorted(
        {provider_voice_registry.normalize_provider_voice_id(item) for item in identifiers}
    )
    provider_voice_registry.assert_doubao_registry_ready(
        db,
        provider_voice_ids=normalized,
    )


def _resolve_in_transaction(
    db: Session,
    *,
    locator: _ResolveLocator,
    actor_id: str,
    actor_tenant_id: str,
    action: Literal["fulfill", "reject"],
    provider_voice_id: str | None,
    rejection_reason: str | None,
    transaction_now: datetime,
) -> BrandVoiceOrder:
    for tenant_id in sorted({locator.tenant_id, actor_tenant_id}):
        lock_tenant_for_subscription_lifecycle(db, tenant_id=tenant_id)
    locked_actor = db.scalar(
        select(User)
        .where(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not is_authorized_platform_admin(
        db,
        user=locked_actor,
        expected_tenant_id=actor_tenant_id,
    ):
        raise AppError(
            "Platform administrator access is required.",
            code="PLATFORM_ADMIN_REQUIRED",
            status_code=403,
        )
    operation = _operation_for_update(db, operation_id=locator.operation_id)
    replay = _terminal_replay(
        db,
        locator=locator,
        operation=operation,
        action=action,
        provider_voice_id=provider_voice_id,
        rejection_reason=rejection_reason,
    )
    if replay is not None:
        return replay

    refund_context = None
    if action == "reject":
        refund_context = refund_subscriptions_for_update(
            db,
            tenant_id=locator.tenant_id,
            source_subscription_id=locator.source_subscription_id,
            now=transaction_now,
        )
        subscriptions = [refund_context.source_subscription]
        if (
            refund_context.current_subscription is not None
            and refund_context.current_subscription.id != refund_context.source_subscription.id
        ):
            subscriptions.append(refund_context.current_subscription)
        subscriptions.sort(key=lambda item: item.id)
    else:
        subscriptions = _subscriptions_for_update(
            db, subscription_ids={locator.source_subscription_id}
        )
    source_subscription = next(
        item for item in subscriptions if item.id == locator.source_subscription_id
    )
    usages = _usage_for_update(db, usage_ids=locator.usage_ids)
    _validate_resolve_financial_state(
        locator=locator,
        operation=operation,
        usages=usages,
        source_subscription=source_subscription,
    )
    _assert_wallet_reconciled(db, subscriptions)

    disposition = None
    if action == "reject":
        if refund_context is None:  # pragma: no cover - narrowed above
            raise AssertionError("refund context missing")
        disposition = decide_credit_refund(
            db,
            tenant_id=locator.tenant_id,
            user_id=locator.user_id,
            billing_operation=operation,
            subscriptions=refund_context,
            amount_credits=ORDER_CREDITS,
            decided_at=transaction_now,
        )

    voice_id = locator.existing_brand_voice_id or str(uuid4())
    voice: BrandVoice | None = None
    registry: BrandVoiceProviderId | None = None
    _lock_provider_stage(
        db,
        provider_voice_id=provider_voice_id,
        previous_provider_voice_id=(
            locator.previous_provider_voice_id if action == "fulfill" else None
        ),
    )
    if action == "fulfill":
        if provider_voice_id is None:
            raise AppError(
                "provider_voice_id is required for fulfillment.",
                code="BRAND_VOICE_ORDER_RESOLUTION_INVALID",
                status_code=422,
            )
        registry = provider_voice_registry.claim_customer_provider_voice_id(
            db,
            provider_voice_id=provider_voice_id,
            brand_voice_id=voice_id,
            order_id=locator.order_id,
            previous_provider_voice_id=locator.previous_provider_voice_id,
            flush=False,
        )

    order = db.scalar(
        select(BrandVoiceOrder)
        .where(BrandVoiceOrder.id == locator.order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise BillingInvariantError("manual order business row is missing")
    if locator.existing_brand_voice_id is not None:
        voice = db.scalar(
            select(BrandVoice)
            .where(BrandVoice.id == locator.existing_brand_voice_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    elif action == "fulfill":
        voice = BrandVoice(
            id=voice_id,
            tenant_id=locator.tenant_id,
            owner_user_id=locator.user_id,
            name="pending",
            provider=DOUBAO_PROVIDER,
            status="ready",
        )
        db.add(voice)
        db.flush([voice])
    asset = db.scalar(
        select(Asset)
        .where(Asset.id == locator.source_asset_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if asset is not None:
        _validate_source_asset(asset, tenant_id=locator.tenant_id)
    if locator.existing_brand_voice_id is not None:
        _validate_renewal_voice(
            voice,
            user_id=locator.user_id,
            now=transaction_now,
        )
    if (
        order.status != "awaiting_fulfillment"
        or order.tenant_id != locator.tenant_id
        or order.user_id != locator.user_id
        or order.billing_operation_id != locator.operation_id
        or order.source_audio_asset_id != locator.source_asset_id
        or order.existing_brand_voice_id != locator.existing_brand_voice_id
        or operation.operation != f"doubao_brand_voice_order_{order.order_type}"
        or asset is None
        or asset.tenant_id != locator.tenant_id
    ):
        raise BillingInvariantError("manual order business invariant is invalid")

    if registry is not None:
        db.flush([registry])

    usage = usages[0]
    quota.settle_locked_subscription_credits(
        source_subscription,
        requested_credits=ORDER_CREDITS,
        settled_credits=ORDER_CREDITS if action == "fulfill" else 0,
    )
    usage.status = "settled" if action == "fulfill" else "released"
    usage.settled_at = transaction_now
    operation.status = "completed"
    operation.completion_kind = "succeeded" if action == "fulfill" else "rejected"
    operation.completed_at = transaction_now
    operation.settled_credits = Decimal(ORDER_CREDITS if action == "fulfill" else 0)
    operation.released_credits = Decimal(0 if action == "fulfill" else ORDER_CREDITS)
    operation.updated_at = transaction_now
    order.resolver_user_id = actor_id
    order.updated_at = transaction_now

    if action == "fulfill":
        if voice is None or registry is None:
            raise BillingInvariantError("manual order fulfillment target is missing")
        voice.tenant_id = locator.tenant_id
        voice.owner_user_id = locator.user_id
        voice.name = order.requested_name
        voice.source_audio_asset_id = order.source_audio_asset_id
        voice.provider = DOUBAO_PROVIDER
        voice.speaker_id = registry.normalized_provider_id
        voice.status = "ready"
        voice.consent_confirmed = True
        voice.consent_confirmed_at = order.consent_confirmed_at
        voice.activated_at = transaction_now
        voice.expires_at = transaction_now + timedelta(days=365)
        voice.error_code = None
        voice.error_message = None
        voice.deleted_at = None
        voice.updated_at = transaction_now
        order.status = "fulfilled"
        order.fulfilled_brand_voice_id = voice.id
        order.fulfilled_provider_id = registry.id
        order.fulfilled_at = transaction_now
        audit_action = "brand_voice_order_fulfill"
        audit_reason = None
    else:
        if disposition is None:  # pragma: no cover - narrowed above
            raise AssertionError("refund disposition missing")
        order.status = "rejected"
        order.rejected_at = transaction_now
        order.rejection_reason = rejection_reason
        audit_action = "brand_voice_order_reject"
        audit_reason = rejection_reason

    db.add(
        AdminAuditLog(
            actor_user_id=actor_id,
            actor_tenant_id=actor_tenant_id,
            action=audit_action,
            target_tenant_id=locator.tenant_id,
            target_id=order.id,
            before={"status": "awaiting_fulfillment"},
            after={"status": order.status},
            reason=audit_reason,
            created_at=transaction_now,
        )
    )
    db.flush()
    operation.result_payload = brand_voice_order_read(db, order=order).model_dump(mode="json")
    return order


def resolve_brand_voice_order(
    db: Session,
    *,
    actor: User,
    order_id: str,
    action: Literal["fulfill", "reject"],
    provider_voice_id: str | None = None,
    rejection_reason: str | None = None,
    now: datetime | None = None,
) -> BrandVoiceOrder:
    if action == "fulfill":
        if (
            provider_voice_id is None
            or not provider_voice_id.strip()
            or rejection_reason is not None
        ):
            raise AppError(
                "Invalid fulfillment request.",
                code="BRAND_VOICE_ORDER_RESOLUTION_INVALID",
                status_code=422,
            )
        provider_voice_id = provider_voice_id.strip()
    elif action == "reject":
        if (
            rejection_reason is None
            or not rejection_reason.strip()
            or provider_voice_id is not None
        ):
            raise AppError(
                "Invalid rejection request.",
                code="BRAND_VOICE_ORDER_RESOLUTION_INVALID",
                status_code=422,
            )
        rejection_reason = rejection_reason.strip()
    else:  # pragma: no cover - Literal boundary
        raise AppError(
            "Invalid resolve action.",
            code="BRAND_VOICE_ORDER_RESOLUTION_INVALID",
            status_code=422,
        )
    locator = _resolve_locator(db, order_id=order_id)
    actor_id = actor.id
    actor_tenant_id = actor.tenant_id
    bind = db.get_bind()
    db.rollback()
    factory = sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return run_db_transaction_with_retry(
        factory,
        lambda transaction_db: _resolve_in_transaction(
            transaction_db,
            locator=locator,
            actor_id=actor_id,
            actor_tenant_id=actor_tenant_id,
            action=action,
            provider_voice_id=provider_voice_id,
            rejection_reason=rejection_reason,
            transaction_now=now or datetime.now(UTC),
        ),
    )
