from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency
from app.db.models import User, Voice
from app.schemas.catalog import VoiceListResponse, VoiceRead
from app.schemas.response import ApiResponse, ok

router = APIRouter()


@router.get("", response_model=ApiResponse[VoiceListResponse])
def list_voices(
    request: Request,
    _user: User = CurrentUserDependency,
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
        )
        for voice in voices
    ]
    return ok(request, VoiceListResponse(items=items, total=len(items)))
