import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.health import router as legacy_health_router
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.middleware.body_size_limit import BodySizeLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.tenant_context import TenantContextMiddleware
from app.services.bgm_library import seed_bgm_library
from app.services.plan_access import (
    configured_platform_tenant_slugs,
    platform_tenant_configuration_issues,
)
from app.services.progress import build_progress_store
from app.services.storage.factory import create_object_storage
from app.services.task_recovery import recover_orphaned_image_queue_tasks

logger = get_logger(__name__)


def _warn_platform_tenant_configuration() -> None:
    if not configured_platform_tenant_slugs():
        return
    try:
        with SessionLocal() as db:
            issues = platform_tenant_configuration_issues(db)
    except Exception as exc:
        logger.warning(
            "platform_tenant.configuration_check_failed",
            error_type=type(exc).__name__,
        )
        return
    for slug, reason in issues:
        logger.warning(
            "platform_tenant.configuration_warning",
            slug=slug,
            reason=reason,
        )


async def _run_orphan_recovery_loop() -> None:
    try:
        progress_store = build_progress_store(settings.redis_url)
    except Exception as exc:  # pragma: no cover - invalid Redis config is uncommon
        progress_store = None
        logger.warning(
            "orphan_task.progress_store_unavailable",
            error_type=type(exc).__name__,
        )
    while True:
        await asyncio.sleep(settings.engine_orphan_recovery_interval_seconds)
        try:
            result = await asyncio.to_thread(
                recover_orphaned_image_queue_tasks,
                session_factory=SessionLocal,
                progress_store=progress_store,
            )
            if result is not None and any(
                (
                    result.photo_tasks,
                    result.video_gen_tasks,
                    result.reverse_prompt_jobs,
                    result.ecom_replicate_jobs,
                    result.aibrain_reservations,
                    result.copy_reservations,
                    result.billing_operations_released,
                    result.billing_operations_settled,
                )
            ):
                logger.warning(
                    "orphan_task.recovered",
                    photo_tasks=result.photo_tasks,
                    video_gen_tasks=result.video_gen_tasks,
                    reverse_prompt_jobs=result.reverse_prompt_jobs,
                    ecom_replicate_jobs=result.ecom_replicate_jobs,
                    aibrain_reservations=result.aibrain_reservations,
                    copy_reservations=result.copy_reservations,
                    billing_operations_released=result.billing_operations_released,
                    billing_operations_settled=result.billing_operations_settled,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - scheduler must survive DB outages
            logger.warning(
                "orphan_task.recovery_failed",
                error_type=type(exc).__name__,
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    logger.info("app.starting", app_name=settings.app_name, version=settings.app_version)
    _warn_platform_tenant_configuration()
    if settings.engine_bgm_seed_on_startup:
        seed_bgm_library(
            SessionLocal,
            storage=create_object_storage(settings),
        )
        logger.info("bgm.seeded")
    recovery_task = asyncio.create_task(_run_orphan_recovery_loop())
    try:
        yield
    finally:
        recovery_task.cancel()
        with suppress(asyncio.CancelledError):
            await recovery_task
        logger.info("app.stopping")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Huading backend API skeleton.",
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    # Keep the receive guard next to the parser so multipart errors close spooled files.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_size=settings.upload_max_bytes,
        paths=(f"{settings.api_v1_prefix}/uploads",),
        path_limits={
            # Leave room for multipart headers; the handler still caps file bytes exactly.
            f"{settings.api_v1_prefix}/uploads{suffix}": file_limit + 1024 * 1024
            for suffix, file_limit in {
                "": settings.upload_image_max_bytes,
                "/images": settings.upload_image_max_bytes,
                "/audio": settings.upload_max_bytes,
                "/videos": settings.upload_video_max_bytes,
            }.items()
        },
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(TenantContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.effective_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    app.include_router(legacy_health_router, prefix="/api")
    return app


app = create_app()
