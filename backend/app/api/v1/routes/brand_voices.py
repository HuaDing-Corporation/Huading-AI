from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUserDependency,
    DbSessionDependency,
    get_object_storage,
    require_permission,
)
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.core.utils import base_mime
from app.db.models import Asset, BrandVoice, User
from app.providers.base import ProviderResolutionError, resolve
from app.schemas.brand_voices import (
    BrandVoiceCreateRequest,
    BrandVoiceCreateResponse,
    BrandVoiceDeletedResponse,
    BrandVoiceListResponse,
    BrandVoiceRead,
    BrandVoiceUpdateRequest,
)
from app.schemas.response import ApiResponse, ok
from app.services.quota import charge_voice_clone_quota
from app.services.storage.base import ObjectStorage

router = APIRouter()
logger = get_logger(__name__)
CreateBrandVoicePermissionDependency = Depends(require_permission("video:create"))
ObjectStorageDependency = Depends(get_object_storage)

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
_VOICE_CLONE_MODEL = "seed-icl-2.0"
_CLONE_ERROR_TEXT_LIMIT = 1000


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
) -> ApiResponse[BrandVoiceCreateResponse]:
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
        provider=_VOICE_CLONE_PROVIDER,
        status="processing",
        consent_confirmed=True,
        consent_confirmed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(brand_voice)
    db.flush()

    usage = charge_voice_clone_quota(
        db,
        tenant_id=user.tenant_id,
        provider=_VOICE_CLONE_PROVIDER,
        model=_VOICE_CLONE_MODEL,
    )
    try:
        provider = resolve(db, tenant_id=user.tenant_id, capability="voice_clone")
        result = asyncio.run(
            provider.clone_voice(
                {
                    "tenant_id": user.tenant_id,
                    "brand_voice_id": brand_voice.id,
                    "name": payload.name,
                    "source_audio_asset_id": source_audio.id,
                    "source_audio_storage_key": source_audio.storage_key,
                    "source_audio_url": storage.presign_get_url(
                        source_audio.storage_key,
                        expires_in=settings.engine_s3_presign_ttl,
                    ),
                    "source_audio_mime_type": source_audio.mime_type,
                }
            )
        )
    except ProviderResolutionError as exc:
        db.rollback()
        raise AppError(
            "Voice clone provider is not configured.",
            code="VOICE_CLONE_PROVIDER_NOT_CONFIGURED",
            status_code=503,
        ) from exc
    except Exception as exc:
        brand_voice_id = brand_voice.id
        source_audio_id = source_audio.id
        db.rollback()
        logger.warning(
            "brand_voice.clone_failed",
            tenant_id=user.tenant_id,
            brand_voice_id=brand_voice_id,
            source_audio_asset_id=source_audio_id,
            error=str(exc),
            error_type=exc.__class__.__name__,
            **_http_error_details(exc),
        )
        raise AppError(
            "Voice clone failed.",
            code="VOICE_CLONE_FAILED",
            status_code=502,
        ) from exc

    speaker_id = str(result.get("speaker_id") or "").strip()
    if not speaker_id:
        db.rollback()
        raise AppError("Voice clone failed.", code="VOICE_CLONE_FAILED", status_code=502)
    provider_name = str(result.get("provider") or _VOICE_CLONE_PROVIDER)
    brand_voice.speaker_id = speaker_id
    brand_voice.provider = provider_name
    brand_voice.status = _provider_status(result)
    brand_voice.updated_at = datetime.now(UTC)
    usage.provider = provider_name
    db.commit()
    db.refresh(brand_voice)
    return ok(request, _brand_voice_read(brand_voice, response_type=BrandVoiceCreateResponse))


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
    return ok(
        request,
        BrandVoiceListResponse(
            items=[_brand_voice_read(item) for item in items],
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
    brand_voice = _brand_voice_or_404(db, tenant_id=user.tenant_id, brand_voice_id=brand_voice_id)
    return ok(request, _brand_voice_read(brand_voice))


@router.patch("/{brand_voice_id}", response_model=ApiResponse[BrandVoiceRead])
def update_brand_voice(
    request: Request,
    brand_voice_id: str,
    payload: BrandVoiceUpdateRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[BrandVoiceRead]:
    brand_voice = _brand_voice_or_404(db, tenant_id=user.tenant_id, brand_voice_id=brand_voice_id)
    brand_voice.name = payload.name
    brand_voice.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(brand_voice)
    return ok(request, _brand_voice_read(brand_voice))


@router.delete("/{brand_voice_id}", response_model=ApiResponse[BrandVoiceDeletedResponse])
def delete_brand_voice(
    request: Request,
    brand_voice_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[BrandVoiceDeletedResponse]:
    brand_voice = _brand_voice_or_404(db, tenant_id=user.tenant_id, brand_voice_id=brand_voice_id)
    if brand_voice.speaker_id:
        _release_remote_speaker(db, user=user, brand_voice=brand_voice)
    brand_voice.deleted_at = datetime.now(UTC)
    brand_voice.updated_at = brand_voice.deleted_at
    db.commit()
    return ok(request, BrandVoiceDeletedResponse(deleted=True))


def _brand_voice_or_404(db: Session, *, tenant_id: str, brand_voice_id: str) -> BrandVoice:
    brand_voice = db.get(BrandVoice, brand_voice_id)
    if (
        brand_voice is None
        or brand_voice.tenant_id != tenant_id
        or brand_voice.deleted_at is not None
    ):
        raise AppError("Brand voice not found.", code="BRAND_VOICE_NOT_FOUND", status_code=404)
    return brand_voice


def _release_remote_speaker(db: Session, *, user: User, brand_voice: BrandVoice) -> None:
    try:
        provider = resolve(db, tenant_id=user.tenant_id, capability="voice_clone")
        asyncio.run(
            provider.delete_voice(
                {
                    "tenant_id": user.tenant_id,
                    "brand_voice_id": brand_voice.id,
                    "speaker_id": brand_voice.speaker_id,
                }
            )
        )
    except Exception as exc:  # best-effort remote release
        logger.warning(
            "brand_voice.release_failed",
            tenant_id=user.tenant_id,
            brand_voice_id=brand_voice.id,
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
    expected_prefix = f"tenants/{tenant_id}/"
    if (
        not asset.storage_key.startswith(expected_prefix)
        or ".." in asset.storage_key
        or "\\" in asset.storage_key
    ):
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


def _provider_status(result: dict[str, Any]) -> str:
    provider_status = str(result.get("status") or "ready").lower()
    if provider_status not in {"processing", "ready", "failed"}:
        return "ready"
    return provider_status


def _brand_voice_read(
    brand_voice: BrandVoice,
    *,
    response_type: type[BrandVoiceRead] = BrandVoiceRead,
) -> BrandVoiceRead:
    return response_type(
        id=brand_voice.id,
        name=brand_voice.name,
        status=brand_voice.status,
        created_at=brand_voice.created_at,
    )
