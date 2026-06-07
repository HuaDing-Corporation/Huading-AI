from fastapi import APIRouter, Depends, Request

from app.api.deps import get_object_storage
from app.schemas.response import ApiResponse, ok
from app.schemas.storage import StoragePutRequest, StoragePutResponse
from app.services.storage.base import ObjectStorage

router = APIRouter()
ObjectStorageDependency = Depends(get_object_storage)


@router.post("/objects", response_model=ApiResponse[StoragePutResponse])
def put_object(
    request: Request,
    payload: StoragePutRequest,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[StoragePutResponse]:
    uri = storage.put_text(payload.key, payload.content, content_type=payload.content_type)
    return ok(request, StoragePutResponse(key=payload.key, uri=uri))
