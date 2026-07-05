import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import (
    DbSessionDependency,
    get_object_storage,
    require_permission,
    tenant_storage_key,
)
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.utils import base_mime
from app.db.models import Asset, User
from app.schemas.response import ApiResponse, ok
from app.schemas.uploads import UploadImageResponse, UploadResponse
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
_ALLOWED_AUDIO_TYPES: dict[str, str] = {
    "audio/aac": ".aac",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
}
_MAX_BYTES = settings.upload_max_bytes
_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


async def _read_limited_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_BYTES:
            raise AppError(
                f"File too large ({total} bytes); limit is {_MAX_BYTES} bytes.",
                code="UPLOAD_TOO_LARGE",
                status_code=413,
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=ApiResponse[UploadResponse], status_code=status.HTTP_201_CREATED)
async def upload_image(
    request: Request,
    file: UploadFile,
    user: User = UploadPermissionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[UploadResponse]:
    """Store an image under the caller's tenant namespace and return the key
    that video generation accepts as ``image_key`` (i2v input)."""
    content_type = base_mime(file.content_type)
    extension = _ALLOWED_TYPES.get(content_type)
    if extension is None:
        raise AppError(
            f"Unsupported image type {content_type or 'unknown'!r}; "
            f"allowed: {', '.join(sorted(_ALLOWED_TYPES))}.",
            code="UNSUPPORTED_MEDIA_TYPE",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    content = await _read_limited_upload(file)
    if not content:
        raise AppError("Uploaded file is empty.", code="EMPTY_UPLOAD", status_code=400)

    # Server-generated key: no client-controlled path components (#002-RV P2).
    key = f"uploads/{uuid.uuid4().hex}{extension}"
    uri = storage.put_bytes(
        tenant_storage_key(user.tenant_id, key), content, content_type=content_type
    )
    return ok(
        request,
        UploadResponse(key=key, uri=uri, content_type=content_type, size=len(content)),
    )


@router.post(
    "/audio",
    response_model=ApiResponse[UploadImageResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_audio(
    request: Request,
    file: UploadFile,
    user: User = UploadPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[UploadImageResponse]:
    content_type = base_mime(file.content_type)
    extension = _ALLOWED_AUDIO_TYPES.get(content_type)
    if extension is None:
        raise AppError(
            f"Unsupported audio type {content_type or 'unknown'!r}; "
            f"allowed: {', '.join(sorted(_ALLOWED_AUDIO_TYPES))}.",
            code="UNSUPPORTED_MEDIA_TYPE",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    content = await _read_limited_upload(file)
    if not content:
        raise AppError("Uploaded file is empty.", code="EMPTY_UPLOAD", status_code=400)

    storage_key = tenant_storage_key(user.tenant_id, f"uploads/{uuid.uuid4().hex}{extension}")
    storage.put_bytes(storage_key, content, content_type=content_type)
    asset = Asset(
        tenant_id=user.tenant_id,
        type="audio",
        source="upload",
        storage_key=storage_key,
        mime_type=content_type,
        size_bytes=len(content),
        duration_ms=_probe_audio_duration_ms(content, extension=extension),
        status="ready",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return ok(request, UploadImageResponse(asset_id=asset.id, type=asset.type, status=asset.status))


@router.post(
    "/images",
    response_model=ApiResponse[UploadImageResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_avatar_image(
    request: Request,
    file: UploadFile,
    user: User = UploadPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[UploadImageResponse]:
    content_type = base_mime(file.content_type)
    extension = _ALLOWED_TYPES.get(content_type)
    if extension is None:
        raise AppError(
            f"Unsupported image type {content_type or 'unknown'!r}; "
            f"allowed: {', '.join(sorted(_ALLOWED_TYPES))}.",
            code="UNSUPPORTED_MEDIA_TYPE",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    content = await _read_limited_upload(file)
    if not content:
        raise AppError("Uploaded file is empty.", code="EMPTY_UPLOAD", status_code=400)

    storage_key = tenant_storage_key(user.tenant_id, f"uploads/{uuid.uuid4().hex}{extension}")
    storage.put_bytes(storage_key, content, content_type=content_type)
    asset = Asset(
        tenant_id=user.tenant_id,
        type="avatar_image",
        source="upload",
        storage_key=storage_key,
        mime_type=content_type,
        size_bytes=len(content),
        status="ready",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return ok(
        request,
        UploadImageResponse(asset_id=asset.id, type=asset.type, status=asset.status),
    )


def _probe_audio_duration_ms(content: bytes, *, extension: str) -> int | None:
    if not content:
        return None
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)
        from app.workers.avatar_talk import _audio_duration_sec

        duration_sec = _audio_duration_sec(temp_path)
    except Exception:
        return None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    if duration_sec <= 0:
        return None
    return int(round(duration_sec * 1000))
