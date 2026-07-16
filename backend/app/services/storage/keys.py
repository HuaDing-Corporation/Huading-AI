import posixpath
from collections.abc import Iterable

from app.core.exceptions import AppError
from app.services.storage.base import ObjectStorage, StorageKeyError

_CATALOG_STORAGE_PREFIXES = ("platform/", "library/bgm/")


def _canonical_storage_key(storage_key: str | None) -> str:
    value = str(storage_key or "")
    if not value or "\\" in value or "%" in value or "\x00" in value:
        raise StorageKeyError("Object key is not canonical.")
    parts = value.split("/")
    if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
        raise StorageKeyError("Object key is not canonical.")
    normalized = posixpath.normpath(value)
    if normalized != value:
        raise StorageKeyError("Object key is not canonical.")
    return normalized


def validate_tenant_storage_key(tenant_id: str, storage_key: str | None) -> str:
    value = _canonical_storage_key(storage_key)
    parts = value.split("/")
    if len(parts) < 3 or parts[0] != "tenants" or parts[1] != tenant_id:
        raise StorageKeyError("Object key is outside the tenant storage prefix.")
    return value


def validate_tenant_storage_keys(
    tenant_id: str,
    storage_keys: Iterable[str | None],
) -> list[str]:
    return [validate_tenant_storage_key(tenant_id, key) for key in storage_keys]


def is_tenant_storage_key(tenant_id: str, storage_key: str | None) -> bool:
    try:
        validate_tenant_storage_key(tenant_id, storage_key)
    except StorageKeyError:
        return False
    return True


def validate_catalog_storage_key(storage_key: str | None) -> str:
    value = _canonical_storage_key(storage_key)
    if not value.startswith(_CATALOG_STORAGE_PREFIXES):
        raise StorageKeyError("Object key is outside the catalog storage prefixes.")
    return value


def _storage_object_not_found() -> AppError:
    return AppError(
        "Storage object not found.",
        code="STORAGE_OBJECT_NOT_FOUND",
        status_code=404,
    )


def presign_tenant_storage_key(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    storage_key: str | None,
    expires_in: int,
    download_filename: str | None = None,
) -> str:
    try:
        safe_key = validate_tenant_storage_key(tenant_id, storage_key)
    except StorageKeyError as exc:
        raise _storage_object_not_found() from exc
    return storage.presign_get_url(
        safe_key,
        expires_in=expires_in,
        download_filename=download_filename,
    )


def presign_catalog_storage_key(
    storage: ObjectStorage,
    *,
    storage_key: str | None,
    expires_in: int,
    download_filename: str | None = None,
) -> str:
    try:
        safe_key = validate_catalog_storage_key(storage_key)
    except StorageKeyError as exc:
        raise _storage_object_not_found() from exc
    return storage.presign_get_url(
        safe_key,
        expires_in=expires_in,
        download_filename=download_filename,
    )


def delete_tenant_storage_key(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    storage_key: str | None,
) -> None:
    storage.delete_object(validate_tenant_storage_key(tenant_id, storage_key))


def get_tenant_storage_bytes(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    storage_key: str | None,
) -> bytes:
    return storage.get_bytes(validate_tenant_storage_key(tenant_id, storage_key))


def get_catalog_storage_bytes(
    storage: ObjectStorage,
    *,
    storage_key: str | None,
) -> bytes:
    return storage.get_bytes(validate_catalog_storage_key(storage_key))


def put_tenant_storage_bytes(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    storage_key: str | None,
    content: bytes,
    content_type: str,
) -> str:
    return storage.put_bytes(
        validate_tenant_storage_key(tenant_id, storage_key),
        content,
        content_type=content_type,
    )


def put_tenant_storage_text(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    storage_key: str | None,
    content: str,
    content_type: str,
) -> str:
    return storage.put_text(
        validate_tenant_storage_key(tenant_id, storage_key),
        content,
        content_type=content_type,
    )


def put_catalog_storage_bytes(
    storage: ObjectStorage,
    *,
    storage_key: str | None,
    content: bytes,
    content_type: str,
) -> str:
    return storage.put_bytes(
        validate_catalog_storage_key(storage_key),
        content,
        content_type=content_type,
    )


def catalog_storage_key_exists(
    storage: ObjectStorage,
    *,
    storage_key: str | None,
) -> bool:
    safe_key = validate_catalog_storage_key(storage_key)
    object_exists = getattr(storage, "object_exists", None)
    if callable(object_exists):
        return bool(object_exists(safe_key))
    try:
        storage.get_bytes(safe_key)
    except Exception:
        return False
    return True
