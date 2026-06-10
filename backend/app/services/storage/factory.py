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
            bucket=settings.engine_s3_bucket or settings.storage_bucket,
            endpoint_url=settings.engine_s3_endpoint or settings.storage_endpoint_url,
            public_endpoint_url=settings.engine_s3_public_endpoint,
            region_name=settings.engine_s3_region or settings.storage_region,
            access_key_id=settings.engine_s3_access_key or settings.storage_access_key_id,
            secret_access_key=settings.engine_s3_secret_key or settings.storage_secret_access_key,
        )
    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")
