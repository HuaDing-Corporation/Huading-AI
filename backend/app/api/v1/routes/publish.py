from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency, get_object_storage
from app.db.models import User
from app.schemas.publish import (
    PublishDraftCreateRequest,
    PublishDraftCreateResponse,
    PublishDraftItem,
    PublishPlatformListResponse,
    PublishPlatformRead,
    PublishRecordDeletedResponse,
    PublishRecordListResponse,
    PublishRecordPatchRequest,
    PublishRecordPlatform,
    PublishRecordRead,
)
from app.schemas.response import ApiResponse, ok
from app.services.publish import (
    create_publish_draft,
    delete_publish_record,
    list_publish_records,
    mark_platform_published,
    publish_platforms,
    validate_platform_config_for_public_catalog,
)
from app.services.storage.base import ObjectStorage

router = APIRouter()
ObjectStorageDependency = Depends(get_object_storage)


def _record_read(record) -> PublishRecordRead:
    return PublishRecordRead(
        id=record.id,
        source_kind=record.source_kind,
        source_task_id=record.source_task_id,
        created_at=record.created_at,
        platforms=[PublishRecordPlatform(**item) for item in record.platforms],
    )


@router.get("/platforms", response_model=ApiResponse[PublishPlatformListResponse])
def platforms(
    request: Request,
    _user: User = CurrentUserDependency,
) -> ApiResponse[PublishPlatformListResponse]:
    validate_platform_config_for_public_catalog()
    items = [
        PublishPlatformRead(
            id=platform["id"],
            name=str(platform["name"]),
            title_max=int(platform["title_max"]),
            body_max=int(platform["body_max"]),
            hashtag_max=int(platform["hashtag_max"]),
            publish_url=str(platform["publish_url"]),
            cover_ratio=str(platform["cover_ratio"]),
            notes=str(platform["notes"]),
        )
        for platform in publish_platforms()
    ]
    return ok(request, PublishPlatformListResponse(items=items))


@router.post(
    "/drafts",
    response_model=ApiResponse[PublishDraftCreateResponse],
    status_code=status.HTTP_201_CREATED,
)
def drafts(
    request: Request,
    payload: PublishDraftCreateRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[PublishDraftCreateResponse]:
    record = create_publish_draft(
        db,
        tenant_id=user.tenant_id,
        source_kind=payload.source_kind,
        source_task_id=payload.source_task_id,
        platform_ids=list(payload.platforms),
        storage=storage,
    )
    return ok(
        request,
        PublishDraftCreateResponse(
            id=record.id,
            items=[PublishDraftItem(**item) for item in record.items],
        ),
    )


@router.get("/records", response_model=ApiResponse[PublishRecordListResponse])
def records(
    request: Request,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[PublishRecordListResponse]:
    items, total = list_publish_records(
        db,
        tenant_id=user.tenant_id,
        limit=limit,
        offset=offset,
    )
    return ok(
        request,
        PublishRecordListResponse(items=[_record_read(item) for item in items], total=total),
    )


@router.patch("/records/{record_id}", response_model=ApiResponse[PublishRecordRead])
def record_patch(
    request: Request,
    record_id: str,
    payload: PublishRecordPatchRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[PublishRecordRead]:
    record = mark_platform_published(
        db,
        tenant_id=user.tenant_id,
        record_id=record_id,
        platform_id=payload.platform_id,
    )
    return ok(request, _record_read(record))


@router.delete(
    "/records/{record_id}",
    response_model=ApiResponse[PublishRecordDeletedResponse],
)
def record_delete(
    request: Request,
    record_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[PublishRecordDeletedResponse]:
    record = delete_publish_record(db, tenant_id=user.tenant_id, record_id=record_id)
    return ok(
        request,
        PublishRecordDeletedResponse(id=record.id, deleted_at=record.deleted_at),
    )
