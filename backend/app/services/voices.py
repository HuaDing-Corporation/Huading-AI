from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import BrandVoice, BrandVoiceProviderId, User, Voice
from app.services.plan_access import is_platform_tenant, uses_doubao_voice_clone

_SUPPORTED_BRAND_VOICE_PROVIDERS = {
    "doubao-voice-clone",
    "cosyvoice-voice-clone",
}


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _configured_official_doubao_ids() -> set[str]:
    return {
        normalized
        for item in settings.engine_doubao_official_voice_ids
        if (normalized := str(item).strip())
    }


def is_official_doubao_voice_for_user(
    db: Session,
    *,
    user: User,
    brand_voice: BrandVoice,
) -> bool:
    """Prove a rights-less row is an official voice for the platform tenant."""
    speaker_id = str(brand_voice.speaker_id or "").strip()
    if (
        not uses_doubao_voice_clone(brand_voice.provider)
        or brand_voice.owner_user_id is not None
        or brand_voice.activated_at is not None
        or brand_voice.expires_at is not None
        or not speaker_id
        or speaker_id not in _configured_official_doubao_ids()
        or brand_voice.tenant_id != user.tenant_id
        or not is_platform_tenant(db, tenant_id=user.tenant_id)
    ):
        return False
    registered_id = db.scalar(
        select(BrandVoiceProviderId.id).where(
            BrandVoiceProviderId.provider == "doubao-voice-clone",
            BrandVoiceProviderId.normalized_provider_id == speaker_id,
            BrandVoiceProviderId.kind == "official",
            BrandVoiceProviderId.status == "active",
            BrandVoiceProviderId.brand_voice_id.is_(None),
            BrandVoiceProviderId.first_order_id.is_(None),
        )
    )
    return registered_id is not None


def is_brand_voice_visible_to_user(
    db: Session,
    *,
    user: User,
    brand_voice: BrandVoice,
) -> bool:
    if brand_voice.tenant_id != user.tenant_id or brand_voice.deleted_at is not None:
        return False
    if not uses_doubao_voice_clone(brand_voice.provider):
        return True
    if brand_voice.owner_user_id is not None:
        return brand_voice.owner_user_id == user.id
    return is_official_doubao_voice_for_user(db, user=user, brand_voice=brand_voice)


def assert_brand_voice_usable(
    db: Session,
    brand_voice: BrandVoice,
    *,
    user: User,
    requested_at: datetime,
) -> None:
    if (
        not is_brand_voice_visible_to_user(db, user=user, brand_voice=brand_voice)
        or brand_voice.status != "ready"
        or not str(brand_voice.speaker_id or "").strip()
        or brand_voice.provider not in _SUPPORTED_BRAND_VOICE_PROVIDERS
    ):
        raise AppError("Voice not found.", code="VOICE_NOT_FOUND", status_code=404)
    if not uses_doubao_voice_clone(brand_voice.provider):
        return
    if brand_voice.owner_user_id is None:
        return
    if brand_voice.activated_at is None or brand_voice.expires_at is None:
        raise AppError("Voice not found.", code="VOICE_NOT_FOUND", status_code=404)
    effective_at = _as_utc(requested_at)
    if not (_as_utc(brand_voice.activated_at) <= effective_at < _as_utc(brand_voice.expires_at)):
        raise AppError("Voice not found.", code="VOICE_NOT_FOUND", status_code=404)


def resolve_narration_voice(
    db: Session,
    *,
    user: User,
    voice_id: str | None,
    requested_at: datetime | None = None,
) -> tuple[Voice | None, BrandVoice | None]:
    if not voice_id:
        raise AppError("voice_id is required.", code="VALIDATION_ERROR", status_code=422)
    voice = db.get(Voice, voice_id)
    if voice is not None and voice.is_active:
        return voice, None
    brand_voice = db.get(BrandVoice, voice_id)
    if brand_voice is None:
        raise AppError("Voice not found.", code="VOICE_NOT_FOUND", status_code=404)
    assert_brand_voice_usable(
        db,
        brand_voice,
        user=user,
        requested_at=requested_at or datetime.now(UTC),
    )
    return None, brand_voice
