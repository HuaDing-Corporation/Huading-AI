from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency, get_object_storage
from app.db.models import User
from app.schemas.history import (
    ImageHistoryCategory,
    ImageHistoryClearResponse,
    ImageHistoryDeletedResponse,
    ImageHistoryDetailResponse,
    ImageHistoryListResponse,
)
from app.schemas.response import ApiResponse, ok
from app.services.image_history import (
    clear_image_history,
    delete_image_history,
    get_image_history,
    list_image_history,
)
from app.services.storage.base import ObjectStorage

router = APIRouter()
ObjectStorageDependency = Depends(get_object_storage)


@router.delete("/images", response_model=ApiResponse[ImageHistoryClearResponse])
def clear_images(
    request: Request,
    category: Annotated[ImageHistoryCategory, Query()],
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ImageHistoryClearResponse]:
    return ok(
        request,
        clear_image_history(
            db,
            tenant_id=user.tenant_id,
            category=category,
        ),
    )


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
    response_model=ApiResponse[ImageHistoryDeletedResponse],
)
def delete_image(
    request: Request,
    category: ImageHistoryCategory,
    history_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ImageHistoryDeletedResponse]:
    return ok(
        request,
        delete_image_history(
            db,
            tenant_id=user.tenant_id,
            category=category,
            history_id=history_id,
        ),
    )
