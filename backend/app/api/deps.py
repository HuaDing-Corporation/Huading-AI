from collections.abc import Generator

import redis
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.services.progress import ProgressStore, build_progress_store
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage


def get_settings_dependency() -> Settings:
    return get_settings()


def get_db_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_redis_client() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def get_object_storage() -> ObjectStorage:
    settings = get_settings()
    return create_object_storage(settings)


def get_progress_store() -> ProgressStore:
    settings = get_settings()
    return build_progress_store(settings.redis_url)
