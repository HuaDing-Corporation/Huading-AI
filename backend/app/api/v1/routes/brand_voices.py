from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select, text
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
from app.db.models import Asset, BrandVoice, ProviderConfig, User
from app.providers.base import ProviderResolutionError, resolve_named_provider
from app.schemas.brand_voices import (
    BrandVoiceCreateRequest,
    BrandVoiceCreateResponse,
    BrandVoiceDeletedResponse,
    BrandVoiceListResponse,
    BrandVoiceRead,
    BrandVoiceUpdateRequest,
)
from app.schemas.response import ApiResponse, ok
from app.services.plan_access import require_doubao_voice_clone_access
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
_VOICE_CLONE_MODEL = "volc.megatts.voiceclone"
_PLATFORM_SLOT_POOL_LOCK_ID = 7_619_542_610_467_367
_COSYVOICE_CLONE_PROVIDER = "cosyvoice-voice-clone"
_CLONE_ERROR_TEXT_LIMIT = 1000
_VOICE_CLONE_PROVIDER_ALIASES = {
    "doubao": _VOICE_CLONE_PROVIDER,
    _VOICE_CLONE_PROVIDER: _VOICE_CLONE_PROVIDER,
    "cosyvoice": _COSYVOICE_CLONE_PROVIDER,
    _COSYVOICE_CLONE_PROVIDER: _COSYVOICE_CLONE_PROVIDER,
}


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
    requested_provider = _voice_clone_provider_name(payload.provider)
    if _uses_doubao_clone_slot(requested_provider):
        require_doubao_voice_clone_access(
            db,
            tenant_id=user.tenant_id,
            role=user.role,
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
        provider=requested_provider,
        status="processing",
        consent_confirmed=True,
        consent_confirmed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(brand_voice)
    db.flush()

    speaker_id = ""
    if _uses_doubao_clone_slot(requested_provider):
        try:
            speaker_id = _allocate_voice_clone_speaker_id(
                db,
                tenant_id=user.tenant_id,
                brand_voice_id=brand_voice.id,
            )
        except AppError:
            db.rollback()
            raise

    usage = charge_voice_clone_quota(
        db,
        tenant_id=user.tenant_id,
        provider=requested_provider,
        model=_voice_clone_model(requested_provider),
    )
    try:
        provider = resolve_named_provider(
            db,
            tenant_id=user.tenant_id,
            capability="voice_clone",
            provider=requested_provider,
        )
        result = asyncio.run(
            provider.clone_voice(
                _clone_payload(
                    tenant_id=user.tenant_id,
                    brand_voice_id=brand_voice.id,
                    name=payload.name,
                    provider=requested_provider,
                    source_audio=source_audio,
                    storage=storage,
                    speaker_id=speaker_id,
                )
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
            provider=requested_provider,
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
    provider_name = str(result.get("provider") or requested_provider)
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
    provider_name = _voice_clone_provider_name(brand_voice.provider)
    if brand_voice.speaker_id:
        _release_remote_speaker(db, user=user, brand_voice=brand_voice)
        if _uses_doubao_clone_slot(provider_name):
            _release_voice_clone_speaker_id(
                db,
                tenant_id=user.tenant_id,
                speaker_id=brand_voice.speaker_id,
                brand_voice_id=brand_voice.id,
            )
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


def _voice_clone_provider_config(
    db: Session,
    *,
    tenant_id: str,
    provider: str = _VOICE_CLONE_PROVIDER,
    for_update: bool = False,
) -> ProviderConfig | None:
    tenant_statement = select(ProviderConfig).where(
        ProviderConfig.tenant_id == tenant_id,
        ProviderConfig.capability == "voice_clone",
        ProviderConfig.provider == provider,
        ProviderConfig.is_active.is_(True),
    )
    if for_update:
        tenant_statement = tenant_statement.with_for_update()
    tenant_config = db.scalar(tenant_statement)
    if tenant_config is not None:
        return tenant_config
    platform_statement = select(ProviderConfig).where(
        ProviderConfig.tenant_id.is_(None),
        ProviderConfig.capability == "voice_clone",
        ProviderConfig.provider == provider,
        ProviderConfig.is_active.is_(True),
    )
    if for_update:
        platform_statement = platform_statement.with_for_update()
    return db.scalar(platform_statement)


def _allocate_voice_clone_speaker_id(
    db: Session,
    *,
    tenant_id: str,
    brand_voice_id: str,
) -> str:
    config = _voice_clone_provider_config(db, tenant_id=tenant_id, for_update=True)
    if config is None:
        _lock_platform_voice_clone_slot_pool(db)
        config = _voice_clone_provider_config(db, tenant_id=tenant_id, for_update=True)
        if config is None:
            config = ProviderConfig(
                tenant_id=None,
                capability="voice_clone",
                provider=_VOICE_CLONE_PROVIDER,
                config={},
                is_active=True,
            )
            db.add(config)
            db.flush()
    values = dict(config.config or {}) if config is not None else {}
    has_db_speaker_ids = "speaker_ids" in values
    speaker_ids = _configured_speaker_ids(values)
    used = {
        str(key): str(value)
        for key, value in dict(values.get("used_speaker_ids") or {}).items()
        if str(key)
    }
    speaker_id = next((item for item in speaker_ids if item not in used), "")
    if not speaker_id:
        raise AppError(
            "暂无可用音色槽位，请先购买。",
            code="VOICE_CLONE_SLOT_UNAVAILABLE",
            status_code=409,
        )
    used[speaker_id] = brand_voice_id
    if has_db_speaker_ids:
        values["speaker_ids"] = speaker_ids
    values["used_speaker_ids"] = used
    config.config = values
    return speaker_id


def _lock_platform_voice_clone_slot_pool(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _PLATFORM_SLOT_POOL_LOCK_ID},
        )


def _configured_speaker_ids(values: dict[str, Any]) -> list[str]:
    raw_ids = (
        values.get("speaker_ids")
        if "speaker_ids" in values
        else settings.engine_doubao_voice_clone_speaker_ids
    )
    return _normalize_speaker_ids(raw_ids)


def _normalize_speaker_ids(raw_ids: Any) -> list[str]:
    if isinstance(raw_ids, str):
        candidates = raw_ids.split(",")
    else:
        candidates = list(raw_ids or [])
    speaker_ids: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        speaker_id = str(item).strip()
        if not speaker_id or speaker_id in seen:
            continue
        speaker_ids.append(speaker_id)
        seen.add(speaker_id)
    return speaker_ids


def _release_voice_clone_speaker_id(
    db: Session,
    *,
    tenant_id: str,
    speaker_id: str,
    brand_voice_id: str,
) -> None:
    config = _voice_clone_provider_config(db, tenant_id=tenant_id, for_update=True)
    if config is None:
        return
    values = dict(config.config or {})
    used = dict(values.get("used_speaker_ids") or {})
    if str(used.get(speaker_id) or "") == brand_voice_id:
        used.pop(speaker_id, None)
        values["used_speaker_ids"] = used
        config.config = values


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


def _voice_clone_provider_name(value: str | None) -> str:
    return _VOICE_CLONE_PROVIDER_ALIASES.get(str(value or "").strip(), _VOICE_CLONE_PROVIDER)


def _uses_doubao_clone_slot(provider: str) -> bool:
    return _voice_clone_provider_name(provider) == _VOICE_CLONE_PROVIDER


def _voice_clone_model(provider: str) -> str:
    if provider == _COSYVOICE_CLONE_PROVIDER:
        return settings.engine_cosyvoice_voice_clone_target_model
    return _VOICE_CLONE_MODEL


def _clone_payload(
    *,
    tenant_id: str,
    brand_voice_id: str,
    name: str,
    provider: str,
    source_audio: Asset,
    storage: ObjectStorage,
    speaker_id: str,
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
    if _uses_doubao_clone_slot(provider):
        payload["speaker_id"] = speaker_id
        payload["source_audio_bytes"] = storage.get_bytes(source_audio.storage_key)
    else:
        payload["source_audio_url"] = storage.presign_get_url(
            source_audio.storage_key,
            expires_in=settings.engine_s3_presign_ttl,
        )
    return payload


def _brand_voice_read(
    brand_voice: BrandVoice,
    *,
    response_type: type[BrandVoiceRead] = BrandVoiceRead,
) -> BrandVoiceRead:
    return response_type(
        id=brand_voice.id,
        name=brand_voice.name,
        provider=_voice_clone_provider_name(brand_voice.provider),
        status=brand_voice.status,
        created_at=brand_voice.created_at,
    )
