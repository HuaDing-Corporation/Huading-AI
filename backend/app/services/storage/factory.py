from app.core.config import Settings
from app.services.storage.base import ObjectStorage
from app.services.storage.local import LocalObjectStorage
from app.services.storage.s3 import S3ObjectStorage


def create_object_storage(settings: Settings) -> ObjectStorage:
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalObjectStorage(settings.storage_local_root)
    if backend in {"s3", "oss"}:
        return S3ObjectStorage(
            bucket=settings.storage_bucket,
            endpoint_url=settings.storage_endpoint_url,
            region_name=settings.storage_region,
            access_key_id=settings.storage_access_key_id,
            secret_access_key=settings.storage_secret_access_key,
        )
    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")
