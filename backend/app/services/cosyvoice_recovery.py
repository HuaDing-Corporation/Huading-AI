from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    BillingOperation,
    BrandVoice,
    CreditRefundGrant,
    Tenant,
    UsageRecord,
)
from app.schemas.brand_voices import BrandVoiceRead, CosyVoiceCloneResult
from app.services import quota
from app.services.billing_operations import (
    _subscription_ids_for_operation,
    _usage_for_update,
    complete_failed,
    complete_succeeded,
)

_RECOVERY_MARKER_PATTERN = re.compile(r"^[a-z0-9]{9}$")


class CosyVoiceRecoveryMarkerCollisionError(RuntimeError):
    pass


class CosyVoiceRecoveryInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class CosyVoiceReconciliationOutcome:
    operation_id: str
    state: Literal["held", "succeeded", "failed"]


def _cosyvoice_recovery_lock_key(marker: str) -> int:
    if not _RECOVERY_MARKER_PATTERN.fullmatch(marker):
        raise ValueError(
            "CosyVoice recovery marker must be nine lowercase alphanumeric characters."
        )
    digest = hashlib.sha256(f"huading:cosyvoice-recovery:{marker}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def lock_cosyvoice_recovery_marker(db: Session, *, marker: str) -> None:
    lock_id = _cosyvoice_recovery_lock_key(marker)
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": lock_id},
    )


def assert_cosyvoice_recovery_marker_owned(
    *,
    request_key: str,
    marker: str,
    candidate_request_keys: Iterable[str],
    marker_for_request: Callable[[str], str],
) -> None:
    for candidate_request_key in candidate_request_keys:
        if candidate_request_key == request_key:
            continue
        if marker_for_request(candidate_request_key) == marker:
            raise CosyVoiceRecoveryMarkerCollisionError(
                "CosyVoice recovery marker is reserved by a different request."
            )


@contextmanager
def claim_cosyvoice_recovery_marker_in_session(
    db: Session,
    *,
    operation_id: str,
    request_key: str,
    marker: str,
    marker_for_request: Callable[[str], str],
) -> Iterator[None]:
    lock_cosyvoice_recovery_marker(db, marker=marker)
    stored_request_key = db.scalar(
        select(BillingOperation.request_hash).where(
            BillingOperation.id == operation_id,
            BillingOperation.operation == "cosyvoice_brand_voice_create",
        )
    )
    if stored_request_key != request_key:
        raise CosyVoiceRecoveryInvariantError(
            "CosyVoice recovery operation does not match its request key."
        )
    candidate_request_keys = db.scalars(
        select(BillingOperation.request_hash).where(
            BillingOperation.operation == "cosyvoice_brand_voice_create"
        )
    )
    assert_cosyvoice_recovery_marker_owned(
        request_key=request_key,
        marker=marker,
        candidate_request_keys=candidate_request_keys,
        marker_for_request=marker_for_request,
    )
    yield


@contextmanager
def claim_cosyvoice_recovery_marker(
    *,
    session_factory: Callable[[], Session],
    operation_id: str,
    request_key: str,
    marker: str,
    marker_for_request: Callable[[str], str],
) -> Iterator[None]:
    with session_factory() as db:
        try:
            with claim_cosyvoice_recovery_marker_in_session(
                db,
                operation_id=operation_id,
                request_key=request_key,
                marker=marker,
                marker_for_request=marker_for_request,
            ):
                yield
        finally:
            db.rollback()


def reconcile_cosyvoice_operation(
    db: Session,
    *,
    operation_id: str,
    provider: Any | None,
    now: datetime,
    marker_for_request: Callable[[str], str],
) -> CosyVoiceReconciliationOutcome:
    """Query-only recovery and local finalization under one durable marker claim."""
    if now.tzinfo is None:
        raise ValueError("reconciliation timestamp must be timezone-aware")
    if db.in_transaction():
        db.rollback()
    with db.begin():
        identity = db.execute(
            select(
                BillingOperation.request_hash,
                BillingOperation.tenant_id,
                BillingOperation.result_id,
                BillingOperation.status,
                BillingOperation.completion_kind,
            ).where(
                BillingOperation.id == operation_id,
                BillingOperation.operation == "cosyvoice_brand_voice_create",
            )
        ).one_or_none()
        if identity is None:
            raise CosyVoiceRecoveryInvariantError("CosyVoice recovery operation is missing.")
        request_key, tenant_id, brand_voice_id, operation_status, completion_kind = identity
        if operation_status == "completed":
            state = "succeeded" if completion_kind == "succeeded" else "failed"
            return CosyVoiceReconciliationOutcome(operation_id=operation_id, state=state)
        if not brand_voice_id:
            raise CosyVoiceRecoveryInvariantError(
                "CosyVoice recovery operation has no brand voice identity."
            )
        marker = marker_for_request(request_key)
        with claim_cosyvoice_recovery_marker_in_session(
            db,
            operation_id=operation_id,
            request_key=request_key,
            marker=marker,
            marker_for_request=marker_for_request,
        ):
            db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
            operation = db.scalar(
                select(BillingOperation)
                .where(BillingOperation.id == operation_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if operation is None:
                raise CosyVoiceRecoveryInvariantError(
                    "CosyVoice recovery operation disappeared."
                )
            if operation.status == "completed":
                state = "succeeded" if operation.completion_kind == "succeeded" else "failed"
                return CosyVoiceReconciliationOutcome(operation_id=operation.id, state=state)

            remote_result: dict[str, Any] | None = None
            ambiguous = False
            try:
                from app.providers.voice_clone.cosyvoice import (
                    CosyVoiceRecoveryAmbiguousError,
                )

                if provider is None:
                    raise RuntimeError("CosyVoice provider is unavailable.")
                remote_result = asyncio.run(
                    provider.query_recovery_voice(
                        {
                            "tenant_id": tenant_id,
                            "brand_voice_id": brand_voice_id,
                            "billing_operation_id": operation_id,
                            "external_request_key": request_key,
                        }
                    )
                )
            except CosyVoiceRecoveryAmbiguousError:
                ambiguous = True
            except Exception:
                remote_result = None
            subscription_ids = _subscription_ids_for_operation(db, operation.id)
            quota.lock_subscriptions_for_billing(db, subscription_ids=subscription_ids)
            usages = _usage_for_update(db, operation.id)
            list(
                db.scalars(
                    select(CreditRefundGrant)
                    .where(CreditRefundGrant.billing_operation_id == operation.id)
                    .order_by(CreditRefundGrant.id)
                    .with_for_update()
                )
            )
            brand_voice = db.scalar(
                select(BrandVoice)
                .where(BrandVoice.id == brand_voice_id, BrandVoice.tenant_id == tenant_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if brand_voice is None:
                raise CosyVoiceRecoveryInvariantError(
                    "CosyVoice recovery brand voice is missing."
                )
            if ambiguous:
                return _fail_reconciliation(
                    db,
                    operation=operation,
                    brand_voice=brand_voice,
                    code="VOICE_CLONE_RECOVERY_AMBIGUOUS",
                    http_status=502,
                    message="CosyVoice recovery inventory is ambiguous.",
                    now=now,
                )
            if remote_result is None:
                changed_at = operation.updated_at or operation.created_at
                if changed_at.tzinfo is None:
                    changed_at = changed_at.replace(tzinfo=UTC)
                if changed_at > now - timedelta(
                    seconds=settings.engine_orphan_task_stale_seconds
                ):
                    return CosyVoiceReconciliationOutcome(
                        operation_id=operation.id,
                        state="held",
                    )
                return _fail_reconciliation(
                    db,
                    operation=operation,
                    brand_voice=brand_voice,
                    code="VOICE_CLONE_RECOVERY_EXPIRED",
                    http_status=504,
                    message="CosyVoice recovery evidence was not found before the deadline.",
                    now=now,
                )
            return _adopt_reconciled_voice(
                db,
                operation=operation,
                usages=usages,
                brand_voice=brand_voice,
                result=remote_result,
                now=now,
            )


def _fail_reconciliation(
    db: Session,
    *,
    operation: BillingOperation,
    brand_voice: BrandVoice,
    code: str,
    http_status: int,
    message: str,
    now: datetime,
) -> CosyVoiceReconciliationOutcome:
    brand_voice.status = "failed"
    brand_voice.error_code = code
    brand_voice.error_message = message
    brand_voice.updated_at = now
    completed = complete_failed(
        db,
        operation_id=operation.id,
        code=code,
        http_status=http_status,
        sanitized_detail=None,
    )
    return CosyVoiceReconciliationOutcome(operation_id=completed.id, state="failed")


def _adopt_reconciled_voice(
    db: Session,
    *,
    operation: BillingOperation,
    usages: list[UsageRecord],
    brand_voice: BrandVoice,
    result: dict[str, Any],
    now: datetime,
) -> CosyVoiceReconciliationOutcome:
    try:
        parsed = CosyVoiceCloneResult.model_validate(result)
    except Exception as exc:
        raise CosyVoiceRecoveryInvariantError(
            "CosyVoice recovery inventory returned an invalid result."
        ) from exc
    usage = next((row for row in usages if row.billing_item_index == 0), None)
    if usage is None:
        raise CosyVoiceRecoveryInvariantError("CosyVoice recovery usage is missing.")
    _attach_provider_cost(usage, result=parsed.model_dump(mode="python", exclude_none=True))
    brand_voice.speaker_id = parsed.speaker_id
    brand_voice.provider = parsed.provider
    brand_voice.status = "ready"
    brand_voice.error_code = None
    brand_voice.error_message = None
    brand_voice.updated_at = now
    stored_voice = BrandVoiceRead(
        id=brand_voice.id,
        name=brand_voice.name,
        provider=brand_voice.provider,
        status=brand_voice.status,
        order_status=None,
        delivery_status="active",
        expires_at=brand_voice.expires_at,
        created_at=brand_voice.created_at,
    )
    completed = complete_succeeded(
        db,
        operation_id=operation.id,
        actual_quantities={0: Decimal("1")},
        result_type="brand_voice",
        result_id=brand_voice.id,
        result_payload=stored_voice,
    )
    return CosyVoiceReconciliationOutcome(operation_id=completed.id, state="succeeded")


def _attach_provider_cost(usage: UsageRecord, *, result: dict[str, Any]) -> None:
    raw_cost = result.get("cost_cents", 0)
    cost_cents = raw_cost if isinstance(raw_cost, int) and not isinstance(raw_cost, bool) else 0
    if cost_cents < 0:
        cost_cents = 0
    usage.provider = "cosyvoice-voice-clone"
    usage.model = str(result.get("model") or settings.engine_cosyvoice_voice_clone_target_model)
    usage.cost_cents = cost_cents
    usage.provider_usage = {"cost_cents": cost_cents}
    raw_usd = result.get("provider_cost_usd")
    if raw_usd in (None, ""):
        return
    try:
        parsed_usd = Decimal(str(raw_usd))
    except (InvalidOperation, TypeError, ValueError):
        return
    if parsed_usd.is_finite() and parsed_usd >= 0:
        usage.provider_cost_usd = parsed_usd
        usage.provider_usage["provider_cost_usd"] = format(parsed_usd, "f")
