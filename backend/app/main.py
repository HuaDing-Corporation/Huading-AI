from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
from app.services.storage.factory import create_object_storage

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    logger.info("app.starting", app_name=settings.app_name, version=settings.app_version)
    if settings.engine_bgm_seed_on_startup:
        seed_bgm_library(
            SessionLocal,
            storage=create_object_storage(settings),
        )
        logger.info("bgm.seeded")
    yield
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
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(TenantContextMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_size=settings.upload_max_bytes,
        paths=(f"{settings.api_v1_prefix}/uploads",),
    )
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
