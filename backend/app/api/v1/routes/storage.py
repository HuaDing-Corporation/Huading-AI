from fastapi import APIRouter, Depends, Request

from app.api.deps import get_object_storage
from app.core.exceptions import AppError
from app.schemas.response import ApiResponse, ok
from app.schemas.storage import StoragePutRequest, StoragePutResponse
from app.services.storage.base import ObjectStorage, StorageKeyError

router = APIRouter()
ObjectStorageDependency = Depends(get_object_storage)


@router.post("/objects", response_model=ApiResponse[StoragePutResponse])
def put_object(
    request: Request,
    payload: StoragePutRequest,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[StoragePutResponse]:
    try:
        uri = storage.put_text(payload.key, payload.content, content_type=payload.content_type)
    except StorageKeyError as exc:
        # Bad client input (key escapes root), not a server fault (#003-FIX P3).
        raise AppError(str(exc), code="INVALID_STORAGE_KEY", status_code=400) from exc
    return ok(request, StoragePutResponse(key=payload.key, uri=uri))
