from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, get_redis_client
from app.schemas.health import ComponentHealth, HealthResponse
from app.schemas.response import ApiResponse, ok

router = APIRouter()
DbSessionDependency = Depends(get_db_session)
RedisDependency = Depends(get_redis_client)


@router.get("/live", response_model=ApiResponse[HealthResponse])
def live(request: Request) -> ApiResponse[HealthResponse]:
    return ok(request, HealthResponse(status="ok"))


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

    return ok(request, HealthResponse(status=status, components=components))
