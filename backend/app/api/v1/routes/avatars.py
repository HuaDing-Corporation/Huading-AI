from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency, get_object_storage
from app.core.config import settings
from app.db.models import Asset, User
from app.schemas.catalog import AvatarPresetListResponse, AvatarPresetRead
from app.schemas.response import ApiResponse, ok
from app.services.storage.base import ObjectStorage
from app.services.storage.keys import presign_catalog_storage_key

router = APIRouter()
ObjectStorageDependency = Depends(get_object_storage)


@router.get("/presets", response_model=ApiResponse[AvatarPresetListResponse])
def list_avatar_presets(
    request: Request,
    _user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[AvatarPresetListResponse]:
    assets = list(
        db.scalars(
            select(Asset)
            .where(
                Asset.tenant_id.is_(None),
                Asset.type == "avatar_image",
                Asset.source == "preset",
                Asset.status == "ready",
                Asset.deleted_at.is_(None),
            )
            .order_by(Asset.created_at.desc())
        )
    )
    items = []
    for asset in assets:
        metadata = asset.metadata_ or {}
        items.append(
            AvatarPresetRead(
                asset_id=asset.id,
                display_name=str(metadata.get("display_name") or "Preset Avatar"),
                thumbnail_url=presign_catalog_storage_key(
                    storage,
                    storage_key=asset.storage_key,
                    expires_in=settings.engine_s3_presign_ttl,
                ),
            )
        )
    return ok(request, AvatarPresetListResponse(items=items, total=len(items)))
