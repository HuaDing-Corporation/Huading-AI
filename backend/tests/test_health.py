from dataclasses import replace

from fastapi.testclient import TestClient

from app.api.deps import get_db_session, get_redis_client
from app.core.config import settings
from app.main import app


def test_health_check() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_live_health_uses_response_envelope() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "ok"
    assert body["error"] is None
    assert body["request_id"]
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_production_ready_uses_shared_readiness_gate_without_repairing(
    auth_db,
    auth_context,
    monkeypatch,
) -> None:
    from app.services import pricing

    class _Redis:
        def ping(self) -> bool:
            return True

    def database_session():
        db = auth_db()
        try:
            yield db
        finally:
            db.close()

    original_default = pricing.PRICING_POLICIES["script_generate"].default_unit_credits
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setitem(
        pricing.PRICING_POLICIES,
        "script_generate",
        replace(
            pricing.PRICING_POLICIES["script_generate"],
            default_unit_credits=original_default + 1,
        ),
    )
    app.dependency_overrides[get_db_session] = database_session
    app.dependency_overrides[get_redis_client] = lambda: _Redis()
    try:
        response = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_redis_client, None)

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["status"] == "degraded"
    assert {component["name"] for component in payload["components"]} >= {
        "postgres",
        "redis",
        "pricing_closure",
    }
