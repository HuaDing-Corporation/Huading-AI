from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models import BrandVoice, Voice


def resolve_narration_voice(
    db: Session,
    *,
    tenant_id: str,
    voice_id: str | None,
) -> tuple[Voice | None, BrandVoice | None]:
    if not voice_id:
        raise AppError("voice_id is required.", code="VALIDATION_ERROR", status_code=422)
    voice = db.get(Voice, voice_id)
    if voice is not None and voice.is_active:
        return voice, None
    brand_voice = db.get(BrandVoice, voice_id)
    if (
        brand_voice is not None
        and brand_voice.tenant_id == tenant_id
        and brand_voice.deleted_at is None
        and brand_voice.status == "ready"
        and brand_voice.speaker_id
    ):
        return None, brand_voice
    raise AppError("Voice not found.", code="VOICE_NOT_FOUND", status_code=404)
