from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency, get_object_storage
from app.db.models import User
from app.schemas.history import (
    ImageHistoryCategory,
    ImageHistoryDetailResponse,
    ImageHistoryListResponse,
)
from app.schemas.response import ApiResponse, ok
from app.services.image_history import (
    delete_image_history,
    get_image_history,
    list_image_history,
)
from app.services.storage.base import ObjectStorage

router = APIRouter()
ObjectStorageDependency = Depends(get_object_storage)


@router.get("/images", response_model=ApiResponse[ImageHistoryListResponse])
def list_images(
    request: Request,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
    category: Annotated[ImageHistoryCategory | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[ImageHistoryListResponse]:
    return ok(
        request,
        list_image_history(
            db,
            tenant_id=user.tenant_id,
            storage=storage,
            category=category,
            page=page,
            page_size=page_size,
        ),
    )


@router.get(
    "/images/{category}/{history_id}",
    response_model=ApiResponse[ImageHistoryDetailResponse],
    response_model_exclude_none=True,
)
def get_image(
    request: Request,
    category: ImageHistoryCategory,
    history_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[ImageHistoryDetailResponse]:
    return ok(
        request,
        get_image_history(
            db,
            tenant_id=user.tenant_id,
            storage=storage,
            category=category,
            history_id=history_id,
        ),
    )


@router.delete(
    "/images/{category}/{history_id}",
    response_model=ApiResponse[dict[str, object]],
)
def delete_image(
    request: Request,
    category: ImageHistoryCategory,
    history_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[dict[str, object]]:
    deleted_id = delete_image_history(
        db,
        tenant_id=user.tenant_id,
        storage=storage,
        category=category,
        history_id=history_id,
    )
    return ok(request, {"id": deleted_id, "deleted": True})
