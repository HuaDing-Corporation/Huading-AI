from fastapi import APIRouter

from app.api.v1.routes import health, storage, tasks, videos

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(storage.router, prefix="/storage", tags=["storage"])
api_router.include_router(videos.router, prefix="/videos", tags=["videos"])
