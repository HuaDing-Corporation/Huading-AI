from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Plan, Subscription, User
from app.main import app


def _active_subscription(db, tenant_id: str) -> Subscription:
    subscription = db.scalar(
        select(Subscription).where(
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
        )
    )
    assert subscription is not None
    return subscription


def _set_plan(db, *, tenant_id: str, plan_code: str) -> None:
    plan = db.scalar(select(Plan).where(Plan.code == plan_code))
    if plan is None:
        plan = Plan(
            code=plan_code,
            name=f"{plan_code.title()} Plan",
            price_cents=0,
            period="monthly",
            quota_credits=0,
            is_active=True,
        )
        db.add(plan)
        db.flush()
    _active_subscription(db, tenant_id).plan_id = plan.id
    db.commit()


def _assert_me_entitlements_and_manual_doubao_order(
    client: TestClient,
    *,
    headers: dict[str, str],
    expected_entitlement: bool,
    expected_role_permission: str,
    expected_analytics_platform: bool = False,
) -> None:
    me_response = client.get("/api/v1/auth/me", headers=headers)
    assert me_response.status_code == 200
    permissions = set(me_response.json()["data"]["permissions"])
    assert expected_role_permission in permissions
    assert ("voice_clone_vip" in permissions) is expected_entitlement
    assert ("analytics_view" in permissions) is expected_entitlement
    assert ("analytics_platform" in permissions) is expected_analytics_platform

    clone_response = client.post(
        "/api/v1/brand-voices",
        json={
            "name": "Entitlement Probe",
            "source_audio_asset_id": "missing-audio-asset",
            "consent_confirmed": True,
            "provider": "doubao",
        },
        headers=headers,
    )
    assert clone_response.status_code == 422
    assert clone_response.json()["error"]["code"] == "DOUBAO_MANUAL_ORDER_REQUIRED"


def test_creator_me_entitlement_tracks_live_plan_while_doubao_stays_manual(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)
    with auth_db() as db:
        user = db.get(User, auth_context["user_id"])
        assert user is not None
        user.role = "creator"
        db.commit()

    _assert_me_entitlements_and_manual_doubao_order(
        client,
        headers=auth_context["headers"],
        expected_entitlement=False,
        expected_role_permission="video:create",
    )

    with auth_db() as db:
        _set_plan(db, tenant_id=auth_context["tenant_id"], plan_code="huading")
    _assert_me_entitlements_and_manual_doubao_order(
        client,
        headers=auth_context["headers"],
        expected_entitlement=True,
        expected_role_permission="video:create",
    )

    with auth_db() as db:
        _set_plan(db, tenant_id=auth_context["tenant_id"], plan_code="free")
    _assert_me_entitlements_and_manual_doubao_order(
        client,
        headers=auth_context["headers"],
        expected_entitlement=False,
        expected_role_permission="video:create",
    )


def test_platform_tenant_me_has_entitlements_without_subscription(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    from app.services import plan_access

    monkeypatch.setattr(
        plan_access,
        "settings",
        SimpleNamespace(engine_platform_tenant_slugs={"acme"}),
        raising=False,
    )
    with auth_db() as db:
        user = db.get(User, auth_context["user_id"])
        assert user is not None
        user.role = "admin"
        db.delete(_active_subscription(db, auth_context["tenant_id"]))
        db.commit()

    _assert_me_entitlements_and_manual_doubao_order(
        TestClient(app),
        headers=auth_context["headers"],
        expected_entitlement=True,
        expected_role_permission="tenant:admin",
        expected_analytics_platform=True,
    )

    admin_check = TestClient(app).get(
        "/api/v1/auth/admin-check",
        headers=auth_context["headers"],
    )
    assert admin_check.status_code == 200
    assert "voice_clone_vip" in admin_check.json()["data"]["permissions"]
