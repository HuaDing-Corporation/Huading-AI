import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from fastapi import APIRouter, Depends, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import (
    DbSessionDependency,
    get_object_storage,
    require_permission,
    tenant_storage_key,
)
from app.api.v1.routes.videos import (
    _AvatarVideoProbe,
    _probe_avatar_video_bytes,
    _validate_avatar_video_probe,
)
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.utils import base_mime
from app.db.models import Asset, User
from app.schemas.response import ApiResponse, ok
from app.schemas.uploads import UploadImageResponse, UploadResponse
from app.services.storage.base import ObjectStorage
from app.services.storage.keys import put_tenant_storage_bytes
from app.services.video_reference import (
    VIDEO_REFERENCE_MAX_BYTES,
    normalize_video_reference,
)

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
_ALLOWED_AVATAR_VIDEO_TYPES: dict[str, str] = {
    "video/mp4": ".mp4",
}
_ALLOWED_VIDEO_REFERENCE_TYPES: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/mov": ".mov",
    "video/quicktime": ".mov",
    "video/x-quicktime": ".mov",
    "video/webm": ".webm",
}
_MAX_BYTES = settings.upload_max_bytes
_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
_REVERSE_PROMPT_VIDEO_MIN_DURATION_MS = 1_000
_REVERSE_PROMPT_VIDEO_MAX_DURATION_MS = 60_000
_REVERSE_PROMPT_VIDEO_MIN_DIMENSION = 240
_REVERSE_PROMPT_VIDEO_MAX_DIMENSION = 2160


async def _read_limited_upload(file: UploadFile, *, max_bytes: int = _MAX_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise AppError(
                f"File too large ({total} bytes); limit is {max_bytes} bytes.",
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
    uri = put_tenant_storage_bytes(
        storage,
        tenant_id=user.tenant_id,
        storage_key=tenant_storage_key(user.tenant_id, key),
        content=content,
        content_type=content_type,
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
    put_tenant_storage_bytes(
        storage,
        tenant_id=user.tenant_id,
        storage_key=storage_key,
        content=content,
        content_type=content_type,
    )
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
    put_tenant_storage_bytes(
        storage,
        tenant_id=user.tenant_id,
        storage_key=storage_key,
        content=content,
        content_type=content_type,
    )
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


@router.post(
    "/videos",
    response_model=ApiResponse[UploadImageResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_avatar_video(
    request: Request,
    file: UploadFile,
    purpose: Literal[
        "avatar_source",
        "reverse_prompt",
        "video_gen_reference",
    ] = "avatar_source",
    user: User = UploadPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[UploadImageResponse]:
    content_type = base_mime(file.content_type)
    allowed_types = (
        _ALLOWED_VIDEO_REFERENCE_TYPES
        if purpose == "video_gen_reference"
        else _ALLOWED_AVATAR_VIDEO_TYPES
    )
    extension = allowed_types.get(content_type)
    if extension is None:
        raise AppError(
            f"Unsupported video type {content_type or 'unknown'!r}; "
            f"allowed: {', '.join(sorted(allowed_types))}.",
            code="UNSUPPORTED_MEDIA_TYPE",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    max_bytes = (
        VIDEO_REFERENCE_MAX_BYTES
        if purpose == "video_gen_reference"
        else settings.upload_video_max_bytes
    )
    content = await _read_limited_upload(file, max_bytes=max_bytes)
    if not content:
        raise AppError("Uploaded file is empty.", code="EMPTY_UPLOAD", status_code=400)

    if purpose == "video_gen_reference":
        normalized = normalize_video_reference(content, suffix=extension)
        storage_key = tenant_storage_key(
            user.tenant_id,
            f"uploads/{uuid.uuid4().hex}.mp4",
        )
        put_tenant_storage_bytes(
            storage,
            tenant_id=user.tenant_id,
            storage_key=storage_key,
            content=normalized.content,
            content_type="video/mp4",
        )
        asset = Asset(
            tenant_id=user.tenant_id,
            type="video",
            source="upload",
            storage_key=storage_key,
            mime_type="video/mp4",
            size_bytes=len(normalized.content),
            duration_ms=normalized.duration_ms,
            width=normalized.width,
            height=normalized.height,
            status="ready",
            metadata_={
                "purpose": purpose,
                "container": normalized.container,
                "video_codec": normalized.video_codec,
                "audio_codec": normalized.audio_codec,
                "original_mime_type": content_type,
                "original_extension": extension,
                "transcoded": normalized.transcoded,
            },
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        return ok(
            request,
            UploadImageResponse(asset_id=asset.id, type=asset.type, status=asset.status),
        )

    probe = _probe_avatar_video_bytes(content, suffix=extension)
    metadata = {
        "purpose": purpose,
        "container": probe.container,
        "video_codec": probe.video_codec,
        "audio_codec": probe.audio_codec,
    }
    candidate = Asset(
        tenant_id=user.tenant_id,
        type="video",
        source="upload",
        storage_key="",
        mime_type=content_type,
        size_bytes=len(content),
        duration_ms=probe.duration_ms,
        width=probe.width,
        height=probe.height,
        status="ready",
        metadata_=metadata,
    )
    if purpose == "reverse_prompt":
        _validate_reverse_prompt_video_probe(candidate, probe)
    else:
        _validate_avatar_video_probe(candidate, probe)

    storage_key = tenant_storage_key(user.tenant_id, f"uploads/{uuid.uuid4().hex}{extension}")
    put_tenant_storage_bytes(
        storage,
        tenant_id=user.tenant_id,
        storage_key=storage_key,
        content=content,
        content_type=content_type,
    )
    asset = Asset(
        tenant_id=user.tenant_id,
        type="video",
        source="upload",
        storage_key=storage_key,
        mime_type=content_type,
        size_bytes=len(content),
        duration_ms=probe.duration_ms,
        width=probe.width,
        height=probe.height,
        status="ready",
        metadata_=metadata,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return ok(request, UploadImageResponse(asset_id=asset.id, type=asset.type, status=asset.status))


def _validate_reverse_prompt_video_probe(asset: Asset, probe: _AvatarVideoProbe) -> None:
    if base_mime(asset.mime_type) != "video/mp4":
        raise AppError(
            "Reverse prompt video must be MP4.",
            code="REVERSE_PROMPT_VIDEO_UNSUPPORTED_FORMAT",
            status_code=422,
        )
    if asset.size_bytes is not None and asset.size_bytes > settings.upload_video_max_bytes:
        raise AppError(
            f"Reverse prompt video is too large; limit is {settings.upload_video_max_bytes} bytes.",
            code="REVERSE_PROMPT_VIDEO_TOO_LARGE",
            status_code=413,
        )
    if (
        probe.duration_ms is None
        or probe.duration_ms < _REVERSE_PROMPT_VIDEO_MIN_DURATION_MS
        or probe.duration_ms > _REVERSE_PROMPT_VIDEO_MAX_DURATION_MS
    ):
        raise AppError(
            "Reverse prompt video must be between 1 and 60 seconds.",
            code="REVERSE_PROMPT_VIDEO_DURATION_INVALID",
            status_code=422,
        )
    width = int(probe.width or 0)
    height = int(probe.height or 0)
    if (
        min(width, height) < _REVERSE_PROMPT_VIDEO_MIN_DIMENSION
        or max(width, height) > _REVERSE_PROMPT_VIDEO_MAX_DIMENSION
    ):
        raise AppError(
            "Reverse prompt video dimensions must be between 240 and 2160 pixels.",
            code="REVERSE_PROMPT_VIDEO_RESOLUTION_INVALID",
            status_code=422,
        )
    container = (probe.container or "").lower()
    if "mp4" not in container:
        raise AppError(
            "Reverse prompt video must be MP4.",
            code="REVERSE_PROMPT_VIDEO_UNSUPPORTED_FORMAT",
            status_code=422,
        )
    if (probe.video_codec or "").lower() != "h264":
        raise AppError(
            "Reverse prompt video must use H.264 video.",
            code="REVERSE_PROMPT_VIDEO_CODEC_INVALID",
            status_code=422,
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
