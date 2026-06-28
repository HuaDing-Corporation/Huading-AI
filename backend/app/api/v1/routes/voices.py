from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency
from app.db.models import BrandVoice, User, Voice
from app.schemas.catalog import VoiceListResponse, VoiceRead
from app.schemas.response import ApiResponse, ok

router = APIRouter()


@router.get("", response_model=ApiResponse[VoiceListResponse])
def list_voices(
    request: Request,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[VoiceListResponse]:
    voices = list(
        db.scalars(
            select(Voice)
            .where(Voice.is_active.is_(True))
            .order_by(Voice.provider.asc(), Voice.display_name.asc())
        )
    )
    items = [
        VoiceRead(
            id=voice.id,
            provider=voice.provider,
            voice_code=voice.voice_code,
            display_name=voice.display_name,
            gender=voice.gender,
            language=voice.language,
            sample_url=voice.sample_url,
            source="preset",
        )
        for voice in voices
    ]
    brand_voices = list(
        db.scalars(
            select(BrandVoice)
            .where(
                BrandVoice.tenant_id == user.tenant_id,
                BrandVoice.status == "ready",
                BrandVoice.speaker_id.is_not(None),
                BrandVoice.deleted_at.is_(None),
            )
            .order_by(BrandVoice.created_at.desc())
        )
    )
    items.extend(
        VoiceRead(
            id=brand_voice.id,
            provider=brand_voice.provider,
            voice_code=brand_voice.id,
            display_name=brand_voice.name,
            gender="neutral",
            language="zh-CN",
            sample_url=None,
            source="brand_voice",
        )
        for brand_voice in brand_voices
    )
    return ok(request, VoiceListResponse(items=items, total=len(items)))
