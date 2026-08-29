from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    BillingSubmissionHeaders,
    CurrentUserDependency,
    DbSessionDependency,
    get_object_storage,
    optional_billing_submission_headers,
    require_permission,
)
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.core.utils import base_mime
from app.db.models import Asset, BrandVoice, BrandVoiceOrder, UsageRecord, User
from app.providers.base import ProviderResolutionError, resolve_named_provider
from app.schemas.billing import BillingQuote
from app.schemas.brand_voices import (
    BrandVoiceCreateRequest,
    BrandVoiceCreateResponse,
    BrandVoiceListResponse,
    BrandVoiceRead,
    BrandVoiceUpdateRequest,
)
from app.schemas.response import ApiResponse, ok
from app.services.asset_retention import assert_brand_voice_not_held_by_manual_order
from app.services.billing_operations import (
    UsageAllocation,
    billing_summary,
    complete_failed,
    complete_succeeded,
    create_reserved_operation,
    find_replay,
    register_billing_result_schema,
)
from app.services.billing_quotes import issue_quote, request_sha256, verify_quote
from app.services.pricing import (
    PRICING_POLICIES,
    PricingDisclosure,
    build_simple_pricing,
    resolve_rate,
)
from app.services.storage.base import ObjectStorage
from app.services.storage.keys import (
    is_tenant_storage_key,
    presign_tenant_storage_key,
)
from app.services.voices import is_brand_voice_visible_to_user

router = APIRouter()
logger = get_logger(__name__)
CreateBrandVoicePermissionDependency = Depends(require_permission("video:create"))
ObjectStorageDependency = Depends(get_object_storage)
OptionalBillingHeadersDependency = Depends(optional_billing_submission_headers)

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
_MIN_SOURCE_AUDIO_MS = 5_000
_VOICE_CLONE_PROVIDER = "doubao-voice-clone"
_COSYVOICE_CLONE_PROVIDER = "cosyvoice-voice-clone"
_CLONE_ERROR_TEXT_LIMIT = 1000
_VOICE_CLONE_PROVIDER_ALIASES = {
    "doubao": _VOICE_CLONE_PROVIDER,
    _VOICE_CLONE_PROVIDER: _VOICE_CLONE_PROVIDER,
    "cosyvoice": _COSYVOICE_CLONE_PROVIDER,
    _COSYVOICE_CLONE_PROVIDER: _COSYVOICE_CLONE_PROVIDER,
}

register_billing_result_schema("brand_voice", BrandVoiceRead)


def _manual_order_required() -> AppError:
    return AppError(
        "豆包音色必须通过人工交付订单购买。",
        code="DOUBAO_MANUAL_ORDER_REQUIRED",
        status_code=422,
    )


def _require_explicit_cosyvoice(payload: BrandVoiceCreateRequest) -> None:
    if (
        payload.provider is None
        or _voice_clone_provider_name(payload.provider) != _COSYVOICE_CLONE_PROVIDER
    ):
        raise _manual_order_required()


def _cosyvoice_request_hash(payload: BrandVoiceCreateRequest) -> str:
    return request_sha256(
        {
            "operation": "cosyvoice_brand_voice_create",
            "payload": payload.model_dump(mode="json"),
        }
    )


def _cosyvoice_pricing_draft(db: Session, *, tenant_id: str):
    create_policy = PRICING_POLICIES["cosyvoice_brand_voice_create"]
    tts_policy = PRICING_POLICIES["cosyvoice_brand_tts"]
    reference_rate = resolve_rate(db, tenant_id=tenant_id, policy=tts_policy)
    rendered_rate = format(reference_rate.unit_credits, "f")
    disclosure = PricingDisclosure(
        key="cosyvoice_tts_reference_rate",
        rendered_text=(
            f"创建免费；使用该音色时当前参考费率为 {rendered_rate} 积分/字，实际使用时重新报价。"
        ),
        copy_version=1,
        unit=tts_policy.unit,
        rate_scope=tts_policy.scope,
        rate=reference_rate,
    )
    return build_simple_pricing(
        policy=create_policy,
        rate=resolve_rate(db, tenant_id=tenant_id, policy=create_policy),
        quantity=Decimal("1"),
        disclosures=(disclosure,),
    )


@router.post("/estimate", response_model=ApiResponse[BillingQuote])
def estimate_brand_voice(
    request: Request,
    payload: BrandVoiceCreateRequest,
    user: User = CreateBrandVoicePermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[BillingQuote]:
    _require_explicit_cosyvoice(payload)
    if payload.consent_confirmed is not True:
        raise AppError(
            "Voice clone consent must be confirmed.",
            code="BRAND_VOICE_CONSENT_REQUIRED",
            status_code=422,
        )
    _source_audio_or_404(
        db,
        tenant_id=user.tenant_id,
        asset_id=payload.source_audio_asset_id,
    )
    return ok(
        request,
        issue_quote(
            tenant_id=user.tenant_id,
            user_id=user.id,
            request_hash=_cosyvoice_request_hash(payload),
            draft=_cosyvoice_pricing_draft(db, tenant_id=user.tenant_id),
        ),
    )


@router.post(
    "",
    response_model=ApiResponse[BrandVoiceCreateResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_brand_voice(
    request: Request,
    payload: BrandVoiceCreateRequest,
    user: User = CreateBrandVoicePermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
    headers: BillingSubmissionHeaders | None = OptionalBillingHeadersDependency,
) -> ApiResponse[BrandVoiceCreateResponse]:
    _require_explicit_cosyvoice(payload)
    if headers is None:
        raise AppError(
            "Idempotency-Key and X-Huading-Quote are required.",
            code="BILLING_HEADERS_REQUIRED",
            status_code=422,
        )
    request_hash = _cosyvoice_request_hash(payload)
    replay = find_replay(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation="cosyvoice_brand_voice_create",
        idempotency_key=headers.idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return ok(request, _cosyvoice_replay_response(replay.operation))
    if payload.consent_confirmed is not True:
        raise AppError(
            "Voice clone consent must be confirmed.",
            code="BRAND_VOICE_CONSENT_REQUIRED",
            status_code=422,
        )
    source_audio = _source_audio_or_404(
        db,
        tenant_id=user.tenant_id,
        asset_id=payload.source_audio_asset_id,
    )

    now = datetime.now(UTC)
    brand_voice = BrandVoice(
        tenant_id=user.tenant_id,
        name=payload.name,
        source_audio_asset_id=source_audio.id,
        provider=_COSYVOICE_CLONE_PROVIDER,
        status="processing",
        consent_confirmed=True,
        consent_confirmed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(brand_voice)
    db.flush()
    verified_quote = verify_quote(
        token=headers.quote_token,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation="cosyvoice_brand_voice_create",
        request_hash=request_hash,
        current_draft=_cosyvoice_pricing_draft(db, tenant_id=user.tenant_id),
    )
    operation = create_reserved_operation(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation="cosyvoice_brand_voice_create",
        idempotency_key=headers.idempotency_key,
        request_hash=request_hash,
        verified_quote=verified_quote,
        usage_allocations=(
            UsageAllocation(
                item_index=0,
                pricing_line_index=0,
                quantity=Decimal("1"),
                credits=Decimal("0"),
                provider=_COSYVOICE_CLONE_PROVIDER,
                model=settings.engine_cosyvoice_voice_clone_target_model,
                video_task_id=None,
            ),
        ),
        result_id=brand_voice.id,
    )
    if operation.result_id != brand_voice.id:
        db.rollback()
        replay = find_replay(
            db,
            tenant_id=user.tenant_id,
            user_id=user.id,
            operation="cosyvoice_brand_voice_create",
            idempotency_key=headers.idempotency_key,
            request_hash=request_hash,
        )
        if replay is None:
            raise AppError(
                "Brand voice billing replay was lost.",
                code="BILLING_REPLAY_INVALID",
                status_code=500,
            )
        return ok(request, _cosyvoice_replay_response(replay.operation))
    clone_payload = _clone_payload(
        tenant_id=user.tenant_id,
        brand_voice_id=brand_voice.id,
        name=payload.name,
        provider=_COSYVOICE_CLONE_PROVIDER,
        source_audio=source_audio,
        storage=storage,
    )
    operation_id = operation.id
    brand_voice_id = brand_voice.id
    db.commit()

    try:
        provider = resolve_named_provider(
            db,
            tenant_id=user.tenant_id,
            capability="voice_clone",
            provider=_COSYVOICE_CLONE_PROVIDER,
        )
        result = asyncio.run(provider.clone_voice(clone_payload))
    except ProviderResolutionError as exc:
        _complete_cosyvoice_failure(
            db,
            brand_voice_id=brand_voice_id,
            operation_id=operation_id,
            code="VOICE_CLONE_PROVIDER_NOT_CONFIGURED",
            status_code=503,
            error=str(exc),
        )
        raise AssertionError("unreachable") from exc
    except Exception as exc:
        logger.warning(
            "brand_voice.clone_failed",
            tenant_id=user.tenant_id,
            brand_voice_id=brand_voice_id,
            source_audio_asset_id=payload.source_audio_asset_id,
            provider=_COSYVOICE_CLONE_PROVIDER,
            error=str(exc),
            error_type=exc.__class__.__name__,
            **_http_error_details(exc),
        )
        _complete_cosyvoice_failure(
            db,
            brand_voice_id=brand_voice_id,
            operation_id=operation_id,
            code="VOICE_CLONE_FAILED",
            status_code=502,
            error=str(exc),
        )
        raise AssertionError("unreachable") from exc

    if not isinstance(result, dict):
        result = {}
    speaker_id = str(result.get("speaker_id") or "").strip()
    provider_name = str(result.get("provider") or _COSYVOICE_CLONE_PROVIDER).strip()
    provider_status = str(result.get("status") or "").strip().lower()
    if not speaker_id or provider_name != _COSYVOICE_CLONE_PROVIDER or provider_status != "ready":
        _complete_cosyvoice_failure(
            db,
            brand_voice_id=brand_voice_id,
            operation_id=operation_id,
            code="VOICE_CLONE_FAILED",
            status_code=502,
            error="invalid provider result",
        )
    brand_voice = db.get(BrandVoice, brand_voice_id)
    if brand_voice is None:
        raise AppError(
            "Stored brand voice is missing.",
            code="BILLING_INVARIANT_VIOLATION",
            status_code=500,
        )
    brand_voice.speaker_id = speaker_id
    brand_voice.provider = provider_name
    brand_voice.status = "ready"
    brand_voice.updated_at = datetime.now(UTC)
    usage = db.scalar(select(UsageRecord).where(UsageRecord.billing_operation_id == operation_id))
    if usage is None:
        raise AppError(
            "Stored brand voice usage is missing.",
            code="BILLING_INVARIANT_VIOLATION",
            status_code=500,
        )
    _attach_cosyvoice_provider_cost(usage, result=result)
    db.flush()
    stored_voice = _brand_voice_read(db, brand_voice)
    operation = complete_succeeded(
        db,
        operation_id=operation_id,
        actual_quantities={0: Decimal("1")},
        result_type="brand_voice",
        result_id=brand_voice.id,
        result_payload=stored_voice,
    )
    db.commit()
    db.refresh(brand_voice)
    return ok(
        request,
        BrandVoiceCreateResponse(
            **_brand_voice_read(db, brand_voice).model_dump(mode="python"),
            billing=billing_summary(operation).model_dump(mode="python"),
        ),
    )


def _cosyvoice_replay_response(operation) -> BrandVoiceCreateResponse:
    if operation.status == "in_progress":
        raise AppError(
            "Brand voice creation is still in progress.",
            code="BILLING_OPERATION_IN_PROGRESS",
            status_code=409,
            detail={"billing": billing_summary(operation).model_dump(mode="json")},
        )
    if operation.completion_kind == "succeeded":
        stored = BrandVoiceRead.model_validate(operation.result_payload)
        return BrandVoiceCreateResponse(
            **stored.model_dump(mode="python"),
            billing=billing_summary(operation).model_dump(mode="python"),
        )
    raise AppError(
        "Voice clone failed.",
        code=operation.error_code or "VOICE_CLONE_FAILED",
        status_code=operation.error_http_status or 502,
        detail={"billing": billing_summary(operation).model_dump(mode="json")},
    )


def _complete_cosyvoice_failure(
    db: Session,
    *,
    brand_voice_id: str,
    operation_id: str,
    code: str,
    status_code: int,
    error: str,
) -> None:
    brand_voice = db.get(BrandVoice, brand_voice_id)
    if brand_voice is not None:
        brand_voice.status = "failed"
        brand_voice.error_code = code
        brand_voice.error_message = _truncate_text(error, _CLONE_ERROR_TEXT_LIMIT)
        brand_voice.updated_at = datetime.now(UTC)
    operation = complete_failed(
        db,
        operation_id=operation_id,
        code=code,
        http_status=status_code,
        sanitized_detail=None,
    )
    db.commit()
    raise AppError(
        "Voice clone failed.",
        code=code,
        status_code=status_code,
        detail={"billing": billing_summary(operation).model_dump(mode="json")},
    )


def _attach_cosyvoice_provider_cost(usage: UsageRecord, *, result: dict[str, Any]) -> None:
    raw_cost = result.get("cost_cents", 0)
    cost_cents = raw_cost if isinstance(raw_cost, int) and not isinstance(raw_cost, bool) else 0
    if cost_cents < 0:
        cost_cents = 0
    usage.provider = _COSYVOICE_CLONE_PROVIDER
    usage.model = str(result.get("model") or settings.engine_cosyvoice_voice_clone_target_model)
    usage.cost_cents = cost_cents
    usage.provider_usage = {"cost_cents": cost_cents}
    raw_usd = result.get("provider_cost_usd")
    if raw_usd not in (None, ""):
        try:
            parsed_usd = Decimal(str(raw_usd))
        except (InvalidOperation, TypeError, ValueError):
            parsed_usd = None
        if parsed_usd is not None and parsed_usd.is_finite() and parsed_usd >= 0:
            usage.provider_cost_usd = parsed_usd
            usage.provider_usage["provider_cost_usd"] = format(parsed_usd, "f")


@router.get("", response_model=ApiResponse[BrandVoiceListResponse])
def list_brand_voices(
    request: Request,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[BrandVoiceListResponse]:
    items = list(
        db.scalars(
            select(BrandVoice)
            .where(
                BrandVoice.tenant_id == user.tenant_id,
                BrandVoice.deleted_at.is_(None),
            )
            .order_by(BrandVoice.created_at.desc())
        )
    )
    items = [
        item for item in items if is_brand_voice_visible_to_user(db, user=user, brand_voice=item)
    ]
    return ok(
        request,
        BrandVoiceListResponse(
            items=[_brand_voice_read(db, item) for item in items],
            total=len(items),
        ),
    )


@router.get("/{brand_voice_id}", response_model=ApiResponse[BrandVoiceRead])
def get_brand_voice(
    request: Request,
    brand_voice_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[BrandVoiceRead]:
    brand_voice = _brand_voice_or_404(db, user=user, brand_voice_id=brand_voice_id)
    return ok(request, _brand_voice_read(db, brand_voice))


@router.patch("/{brand_voice_id}", response_model=ApiResponse[BrandVoiceRead])
def update_brand_voice(
    request: Request,
    brand_voice_id: str,
    payload: BrandVoiceUpdateRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[BrandVoiceRead]:
    brand_voice = _brand_voice_or_404(db, user=user, brand_voice_id=brand_voice_id)
    brand_voice.name = payload.name
    brand_voice.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(brand_voice)
    return ok(request, _brand_voice_read(db, brand_voice))


@router.delete("/{brand_voice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_brand_voice(
    brand_voice_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> Response:
    brand_voice = db.scalar(
        select(BrandVoice)
        .where(BrandVoice.id == brand_voice_id, BrandVoice.tenant_id == user.tenant_id)
        .with_for_update()
    )
    if brand_voice is None or not is_brand_voice_visible_to_user(
        db,
        user=user,
        brand_voice=brand_voice,
    ):
        raise AppError("Brand voice not found.", code="BRAND_VOICE_NOT_FOUND", status_code=404)
    assert_brand_voice_not_held_by_manual_order(db, brand_voice_id=brand_voice.id)
    provider_name = _voice_clone_provider_name(brand_voice.provider)
    if brand_voice.speaker_id and not _uses_doubao_clone_slot(provider_name):
        _release_remote_speaker(db, user=user, brand_voice=brand_voice)
    brand_voice.deleted_at = datetime.now(UTC)
    brand_voice.updated_at = brand_voice.deleted_at
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _brand_voice_or_404(db: Session, *, user: User, brand_voice_id: str) -> BrandVoice:
    brand_voice = db.get(BrandVoice, brand_voice_id)
    if brand_voice is None or not is_brand_voice_visible_to_user(
        db,
        user=user,
        brand_voice=brand_voice,
    ):
        raise AppError("Brand voice not found.", code="BRAND_VOICE_NOT_FOUND", status_code=404)
    return brand_voice


def _release_remote_speaker(db: Session, *, user: User, brand_voice: BrandVoice) -> None:
    provider_name = _voice_clone_provider_name(brand_voice.provider)
    try:
        provider = resolve_named_provider(
            db,
            tenant_id=user.tenant_id,
            capability="voice_clone",
            provider=provider_name,
        )
        asyncio.run(
            provider.delete_voice(
                {
                    "tenant_id": user.tenant_id,
                    "brand_voice_id": brand_voice.id,
                    "speaker_id": brand_voice.speaker_id,
                    "voice_clone_provider": provider_name,
                }
            )
        )
    except Exception as exc:  # best-effort remote release
        logger.warning(
            "brand_voice.release_failed",
            tenant_id=user.tenant_id,
            brand_voice_id=brand_voice.id,
            provider=provider_name,
            error=str(exc),
        )


def _source_audio_or_404(db: Session, *, tenant_id: str, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if (
        asset is None
        or asset.tenant_id != tenant_id
        or asset.type != "audio"
        or asset.status != "ready"
        or asset.deleted_at is not None
    ):
        raise AppError(
            "Source audio asset not found.",
            code="SOURCE_AUDIO_ASSET_NOT_FOUND",
            status_code=404,
        )
    _validate_audio_asset(asset, tenant_id=tenant_id)
    return asset


def _validate_audio_asset(asset: Asset, *, tenant_id: str) -> None:
    mime_type = base_mime(asset.mime_type)
    if mime_type not in _ALLOWED_AUDIO_TYPES:
        raise AppError(
            "Unsupported source audio type.",
            code="UNSUPPORTED_SOURCE_AUDIO_TYPE",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    if not is_tenant_storage_key(tenant_id, asset.storage_key):
        raise AppError(
            "Invalid source audio storage key.",
            code="INVALID_SOURCE_AUDIO_KEY",
            status_code=422,
        )
    if asset.duration_ms is not None and asset.duration_ms < _MIN_SOURCE_AUDIO_MS:
        raise AppError(
            "Source audio is too short.",
            code="SOURCE_AUDIO_TOO_SHORT",
            status_code=422,
        )


def _http_error_details(exc: Exception) -> dict[str, Any]:
    response = getattr(exc, "response", None)
    if response is None:
        return {}
    details: dict[str, Any] = {}
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        details["http_status_code"] = status_code
    text = _response_text(response)
    if text:
        details["http_response_text"] = _truncate_text(text, _CLONE_ERROR_TEXT_LIMIT)
    return details


def _response_text(response: Any) -> str:
    try:
        text = getattr(response, "text", "")
    except Exception:
        return ""
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return str(text or "")


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def _voice_clone_provider_name(value: str | None) -> str:
    normalized = str(value or "").strip()
    return _VOICE_CLONE_PROVIDER_ALIASES.get(normalized, normalized or _VOICE_CLONE_PROVIDER)


def _uses_doubao_clone_slot(provider: str) -> bool:
    return _voice_clone_provider_name(provider) == _VOICE_CLONE_PROVIDER


def _clone_payload(
    *,
    tenant_id: str,
    brand_voice_id: str,
    name: str,
    provider: str,
    source_audio: Asset,
    storage: ObjectStorage,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "brand_voice_id": brand_voice_id,
        "name": name,
        "source_audio_asset_id": source_audio.id,
        "source_audio_storage_key": source_audio.storage_key,
        "source_audio_mime_type": source_audio.mime_type,
        "voice_clone_provider": provider,
    }
    payload["source_audio_url"] = presign_tenant_storage_key(
        storage,
        tenant_id=tenant_id,
        storage_key=source_audio.storage_key,
        expires_in=settings.engine_s3_presign_ttl,
    )
    return payload


def _brand_voice_read(
    db: Session,
    brand_voice: BrandVoice,
) -> BrandVoiceRead:
    order_status = db.scalar(
        select(BrandVoiceOrder.status)
        .where(BrandVoiceOrder.fulfilled_brand_voice_id == brand_voice.id)
        .order_by(BrandVoiceOrder.created_at.desc())
        .limit(1)
    )
    now = datetime.now(UTC)
    expires_at = brand_voice.expires_at
    if order_status == "rejected" or brand_voice.status == "failed":
        delivery_status = "rejected"
    elif order_status == "awaiting_fulfillment" or brand_voice.status == "processing":
        delivery_status = "awaiting_fulfillment"
    elif (
        _uses_doubao_clone_slot(brand_voice.provider)
        and brand_voice.owner_user_id is not None
        and expires_at is not None
        and (
            expires_at.astimezone(UTC)
            if expires_at.tzinfo is not None
            else expires_at.replace(tzinfo=UTC)
        )
        <= now
    ):
        delivery_status = "expired"
    else:
        delivery_status = "active"
    return BrandVoiceRead(
        id=brand_voice.id,
        name=brand_voice.name,
        provider=_voice_clone_provider_name(brand_voice.provider),
        status=brand_voice.status,
        order_status=order_status,
        delivery_status=delivery_status,
        created_at=brand_voice.created_at,
    )
