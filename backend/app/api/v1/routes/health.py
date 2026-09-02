from threading import Condition
from time import monotonic

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
_PRICING_READINESS_CACHE_TTL_SECONDS = 5.0
_PRICING_READINESS_WAIT_SECONDS = 10.0
_pricing_readiness_condition = Condition()
_pricing_readiness_in_flight = False
_pricing_readiness_cached_at: float | None = None
_pricing_readiness_cached_value: bool | None = None


def _reset_pricing_readiness_cache() -> None:
    """Clear the process-local readiness cache (primarily for controlled tests)."""
    global _pricing_readiness_cached_at, _pricing_readiness_cached_value
    global _pricing_readiness_in_flight
    with _pricing_readiness_condition:
        _pricing_readiness_cached_at = None
        _pricing_readiness_cached_value = None
        _pricing_readiness_in_flight = False
        _pricing_readiness_condition.notify_all()


def _cached_pricing_closure_ready(db: Session) -> bool:
    """Run the full validator once per short cache window for public probes."""
    global _pricing_readiness_cached_at, _pricing_readiness_cached_value
    global _pricing_readiness_in_flight
    with _pricing_readiness_condition:
        now = monotonic()
        if (
            _pricing_readiness_cached_at is not None
            and _pricing_readiness_cached_value is not None
            and now - _pricing_readiness_cached_at < _PRICING_READINESS_CACHE_TTL_SECONDS
        ):
            return _pricing_readiness_cached_value
        if _pricing_readiness_in_flight:
            _pricing_readiness_condition.wait(timeout=_PRICING_READINESS_WAIT_SECONDS)
            now = monotonic()
            if (
                _pricing_readiness_cached_at is not None
                and _pricing_readiness_cached_value is not None
                and now - _pricing_readiness_cached_at < _PRICING_READINESS_CACHE_TTL_SECONDS
            ):
                return _pricing_readiness_cached_value
            # A failed or stuck peer must never turn into a false-green cache hit.
            return False
        _pricing_readiness_in_flight = True
    try:
        from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

        value = bool(pricing_closure_readiness(db, production_mode=True).ready)
    except Exception:
        value = False
    with _pricing_readiness_condition:
        _pricing_readiness_cached_at = monotonic()
        _pricing_readiness_cached_value = value
        _pricing_readiness_in_flight = False
        _pricing_readiness_condition.notify_all()
    return value


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
    except Exception:
        status = "degraded"
        components.append(
            ComponentHealth(
                name="postgres",
                status="error",
                detail="database readiness check failed",
            )
        )

    try:
        redis_client.ping()
        components.append(ComponentHealth(name="redis", status="ok"))
    except Exception:
        status = "degraded"
        components.append(
            ComponentHealth(
                name="redis",
                status="error",
                detail="cache readiness check failed",
            )
        )

    if str(settings.environment).strip().lower() in {"prod", "production"}:
        try:
            if _cached_pricing_closure_ready(db):
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
