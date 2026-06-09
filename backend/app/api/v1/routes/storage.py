from fastapi import APIRouter, Depends, Request

from app.api.deps import get_object_storage, require_permission, tenant_storage_key
from app.core.exceptions import AppError
from app.db.models import User
from app.schemas.response import ApiResponse, ok
from app.schemas.storage import StoragePutRequest, StoragePutResponse
from app.services.storage.base import ObjectStorage, StorageKeyError

router = APIRouter()
ObjectStorageDependency = Depends(get_object_storage)
StoragePermissionDependency = Depends(require_permission("content:operate"))


@router.post("/objects", response_model=ApiResponse[StoragePutResponse])
def put_object(
    request: Request,
    payload: StoragePutRequest,
    user: User = StoragePermissionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[StoragePutResponse]:
    if (
        payload.key == "tenants"
        or payload.key.startswith("tenants/")
        or ".." in payload.key
        or payload.key.startswith("/")
        or "\\" in payload.key
    ):
        raise AppError(
            "Storage key must not include a tenant namespace.",
            code="INVALID_STORAGE_KEY",
            status_code=400,
        )
    try:
        uri = storage.put_text(
            tenant_storage_key(user.tenant_id, payload.key),
            payload.content,
            content_type=payload.content_type,
        )
    except StorageKeyError as exc:
        # Bad client input (key escapes root), not a server fault (#003-FIX P3).
        raise AppError(str(exc), code="INVALID_STORAGE_KEY", status_code=400) from exc
    return ok(request, StoragePutResponse(key=payload.key, uri=uri))
