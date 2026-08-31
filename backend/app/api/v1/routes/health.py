from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, get_redis_client
from app.core.config import settings
from app.schemas.common import HealthResponse as LivenessResponse
from app.schemas.health import ComponentHealth, HealthResponse
from app.schemas.response import ApiResponse, ok

router = APIRouter()
DbSessionDependency = Depends(get_db_session)
RedisDependency = Depends(get_redis_client)


@router.get("/live", response_model=ApiResponse[HealthResponse])
def live(request: Request) -> ApiResponse[HealthResponse]:
    return ok(request, HealthResponse(status="ok"))


alias_router = APIRouter()


@alias_router.get("/healthz", response_model=LivenessResponse)
def healthz() -> LivenessResponse:
    return LivenessResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
    )


@router.get("/ready", response_model=ApiResponse[HealthResponse])
def ready(
    request: Request,
    db: Session = DbSessionDependency,
    redis_client=RedisDependency,
) -> ApiResponse[HealthResponse]:
    components: list[ComponentHealth] = []
    status = "ok"

    try:
        db.execute(text("SELECT 1"))
        components.append(ComponentHealth(name="postgres", status="ok"))
    except Exception as exc:
        status = "degraded"
        components.append(ComponentHealth(name="postgres", status="error", detail=str(exc)))

    try:
        redis_client.ping()
        components.append(ComponentHealth(name="redis", status="ok"))
    except Exception as exc:
        status = "degraded"
        components.append(ComponentHealth(name="redis", status="error", detail=str(exc)))

    if str(settings.environment).strip().lower() in {"prod", "production"}:
        try:
            from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

            report = pricing_closure_readiness(db, production_mode=True)
            if report.ready:
                components.append(ComponentHealth(name="pricing_closure", status="ok"))
            else:
                status = "degraded"
                components.append(
                    ComponentHealth(
                        name="pricing_closure",
                        status="error",
                        detail="pricing closure readiness gate is not ready",
                    )
                )
        except Exception:
            status = "degraded"
            components.append(
                ComponentHealth(
                    name="pricing_closure",
                    status="error",
                    detail="pricing closure readiness check failed",
                )
            )

    return ok(request, HealthResponse(status=status, components=components))


@alias_router.get("/readyz")
def readyz(db: Session = DbSessionDependency) -> dict[str, bool | str]:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return {"status": "degraded", "db": False}
    return {"status": "ok", "db": True}
