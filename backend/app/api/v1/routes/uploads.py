import uuid

from fastapi import APIRouter, Depends, Request, UploadFile, status

from app.api.deps import get_object_storage, require_permission, tenant_storage_key
from app.core.exceptions import AppError
from app.db.models import User
from app.schemas.response import ApiResponse, ok
from app.schemas.uploads import UploadResponse
from app.services.storage.base import ObjectStorage

router = APIRouter()
ObjectStorageDependency = Depends(get_object_storage)
UploadPermissionDependency = Depends(require_permission("video:create"))

# Product shots for image-to-video: common raster image types only.
_ALLOWED_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("", response_model=ApiResponse[UploadResponse], status_code=status.HTTP_201_CREATED)
async def upload_image(
    request: Request,
    file: UploadFile,
    user: User = UploadPermissionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[UploadResponse]:
    """Store an image under the caller's tenant namespace and return the key
    that video generation accepts as ``image_key`` (i2v input)."""
    content_type = (file.content_type or "").lower()
    extension = _ALLOWED_TYPES.get(content_type)
    if extension is None:
        raise AppError(
            f"Unsupported image type {content_type or 'unknown'!r}; "
            f"allowed: {', '.join(sorted(_ALLOWED_TYPES))}.",
            code="UNSUPPORTED_MEDIA_TYPE",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    content = await file.read()
    if not content:
        raise AppError("Uploaded file is empty.", code="EMPTY_UPLOAD", status_code=400)
    if len(content) > _MAX_BYTES:
        raise AppError(
            f"File too large ({len(content)} bytes); limit is {_MAX_BYTES} bytes.",
            code="UPLOAD_TOO_LARGE",
            status_code=413,
        )

    # Server-generated key: no client-controlled path components (#002-RV P2).
    key = f"uploads/{uuid.uuid4().hex}{extension}"
    uri = storage.put_bytes(
        tenant_storage_key(user.tenant_id, key), content, content_type=content_type
    )
    return ok(
        request,
        UploadResponse(key=key, uri=uri, content_type=content_type, size=len(content)),
    )
