from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import Organization, Subscription, Tenant, User
from app.main import app


class _CaptureLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def info(self, _event: str, **_details: Any) -> None:
        return None

    def warning(self, event: str, **details: Any) -> None:
        self.warnings.append((event, details))


def _registration_payload(slug: str) -> dict[str, object]:
    return {
        "tenant_slug": slug,
        "tenant_name": f"{slug.title()} Studio",
        "email": f"owner@{slug}.test",
        "password": "secret-pass",
    }


def test_reserved_platform_slug_registration_is_indistinguishable_and_has_no_side_effects(
    auth_db,
    monkeypatch,
) -> None:
    from app.services import plan_access

    monkeypatch.setattr(
        plan_access.settings,
        "engine_platform_tenant_slugs",
        {"huading"},
    )

    response = TestClient(app).post(
        "/api/v1/auth/register-tenant",
        json=_registration_payload("huading"),
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "tenant_slug_taken"
    assert error["message"] == "Tenant slug is already taken."
    assert error["detail"] is None
    assert error["details"] is None
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(Tenant)) == 0
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(Organization)) == 0
        assert db.scalar(select(func.count()).select_from(Subscription)) == 0


def test_empty_platform_slug_list_keeps_registration_behavior(
    auth_db,
    seed_plan,
    monkeypatch,
) -> None:
    from app.services import plan_access

    monkeypatch.setattr(
        plan_access.settings,
        "engine_platform_tenant_slugs",
        set(),
    )

    response = TestClient(app).post(
        "/api/v1/auth/register-tenant",
        json=_registration_payload("ordinary"),
    )

    assert response.status_code == 201
    tenant_id = response.json()["data"]["tenant"]["id"]
    with auth_db() as db:
        assert db.get(Tenant, tenant_id) is not None
        assert db.scalar(select(User).where(User.tenant_id == tenant_id)) is not None
        assert (
            db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
            is not None
        )


def test_registration_and_access_share_platform_slug_normalization(monkeypatch) -> None:
    from app.services import plan_access

    monkeypatch.setattr(
        plan_access.settings,
        "engine_platform_tenant_slugs",
        {"huading"},
    )

    assert plan_access.is_platform_tenant_slug("  HuaDing  ") is True
    assert plan_access.is_platform_tenant_slug("ordinary") is False


def test_lifespan_warns_for_missing_or_non_huading_platform_tenants(
    auth_db,
    monkeypatch,
) -> None:
    from app import main as app_main
    from app.services import plan_access

    with auth_db() as db:
        db.add(Tenant(slug="ordinary", name="Ordinary Tenant"))
        db.commit()

    logger = _CaptureLogger()
    monkeypatch.setattr(
        plan_access.settings,
        "engine_platform_tenant_slugs",
        {"missing-platform", "ordinary"},
    )
    monkeypatch.setattr(app_main, "SessionLocal", auth_db)
    monkeypatch.setattr(app_main, "logger", logger)

    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    warnings = {
        (details["slug"], details["reason"])
        for event, details in logger.warnings
        if event == "platform_tenant.configuration_warning"
    }
    assert warnings == {
        ("missing-platform", "tenant_missing"),
        ("ordinary", "huading_plan_missing"),
    }


def test_lifespan_platform_check_failure_does_not_block_startup(monkeypatch) -> None:
    from app import main as app_main
    from app.services import plan_access

    logger = _CaptureLogger()
    monkeypatch.setattr(
        plan_access.settings,
        "engine_platform_tenant_slugs",
        {"huading"},
    )
    monkeypatch.setattr(
        app_main,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(RuntimeError("sensitive connection detail")),
    )
    monkeypatch.setattr(app_main, "logger", logger)

    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert logger.warnings == [
        (
            "platform_tenant.configuration_check_failed",
            {"error_type": "RuntimeError"},
        )
    ]
