from fastapi import APIRouter

from app.api.v1.routes import (
    admin_console,
    aibrain,
    analytics,
    auth,
    avatars,
    batches,
    bgm_library,
    billing,
    brand_voices,
    copy,
    covers,
    ecom_images,
    health,
    history,
    oral,
    publish,
    quota,
    reverse_prompt,
    scripts,
    storage,
    tasks,
    tenant,
    uploads,
    videos,
    voices,
)

api_router = APIRouter()
api_router.include_router(aibrain.router, prefix="/aibrain", tags=["aibrain"])
api_router.include_router(
    admin_console.router,
    prefix="/admin/console",
    tags=["admin-console"],
)
api_router.include_router(analytics.router, prefix="/admin/analytics", tags=["admin-analytics"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(health.alias_router, tags=["health"])
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(history.router, prefix="/history", tags=["history"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
api_router.include_router(quota.router, prefix="/quota", tags=["quota"])
api_router.include_router(voices.router, prefix="/voices", tags=["voices"])
api_router.include_router(brand_voices.router, prefix="/brand-voices", tags=["brand-voices"])
api_router.include_router(avatars.router, prefix="/avatars", tags=["avatars"])
api_router.include_router(batches.router, prefix="/batches", tags=["batches"])
api_router.include_router(bgm_library.router, prefix="/bgm-library", tags=["bgm-library"])
api_router.include_router(copy.router, prefix="/copy", tags=["copy"])
api_router.include_router(oral.router, prefix="/oral", tags=["oral"])
api_router.include_router(publish.router, prefix="/publish", tags=["publish"])
api_router.include_router(covers.router, prefix="/covers", tags=["covers"])
api_router.include_router(ecom_images.router, prefix="/ecom-images", tags=["ecom-images"])
api_router.include_router(reverse_prompt.router, prefix="/reverse-prompt", tags=["reverse-prompt"])
api_router.include_router(scripts.router, prefix="/scripts", tags=["scripts"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(tenant.router, prefix="/tenant", tags=["tenant"])
api_router.include_router(storage.router, prefix="/storage", tags=["storage"])
api_router.include_router(uploads.router, prefix="/uploads", tags=["uploads"])
api_router.include_router(videos.router, prefix="/videos", tags=["videos"])
