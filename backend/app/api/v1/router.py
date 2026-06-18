from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    avatars,
    health,
    quota,
    scripts,
    storage,
    tasks,
    uploads,
    videos,
    voices,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(health.alias_router, tags=["health"])
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(quota.router, prefix="/quota", tags=["quota"])
api_router.include_router(voices.router, prefix="/voices", tags=["voices"])
api_router.include_router(avatars.router, prefix="/avatars", tags=["avatars"])
api_router.include_router(scripts.router, prefix="/scripts", tags=["scripts"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(storage.router, prefix="/storage", tags=["storage"])
api_router.include_router(uploads.router, prefix="/uploads", tags=["uploads"])
api_router.include_router(videos.router, prefix="/videos", tags=["videos"])
