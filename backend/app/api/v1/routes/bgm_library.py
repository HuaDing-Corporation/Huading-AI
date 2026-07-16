from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency, get_object_storage
from app.core.config import settings
from app.db.models import User
from app.schemas.bgm import BgmLibraryResponse, BgmTrackRead
from app.schemas.response import ApiResponse, ok
from app.services.bgm_library import list_bgm_tracks
from app.services.storage.base import ObjectStorage
from app.services.storage.keys import presign_catalog_storage_key

router = APIRouter()
ObjectStorageDependency = Depends(get_object_storage)


@router.get("", response_model=ApiResponse[BgmLibraryResponse])
def bgm_library(
    request: Request,
    _user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[BgmLibraryResponse]:
    items = []
    for track in list_bgm_tracks(db):
        preview_key = track.preview_storage_key or track.storage_key
        items.append(
            BgmTrackRead(
                track_id=track.track_id,
                name=track.name,
                duration_sec=track.duration_sec,
                preview_url=presign_catalog_storage_key(
                    storage,
                    storage_key=preview_key,
                    expires_in=settings.engine_s3_presign_ttl,
                ),
                license=track.license,
            )
        )
    return ok(request, BgmLibraryResponse(items=items))
