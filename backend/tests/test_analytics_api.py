from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Plan, Subscription, Tenant, UsageRecord, User, VideoTask
from app.main import app

PERIOD_PARAMS = {"from": "2026-06-01", "to": "2026-06-30"}


def _dt(day: int) -> datetime:
    return datetime(2026, 6, day, 9, 0, tzinfo=UTC)


def _subscription(
    db,
    tenant_id: str,
    *,
    total: int,
    used: int,
    reserved: int,
) -> Subscription:
    subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    if subscription is None:
        plan = Plan(
            code=f"analytics-{tenant_id}",
            name="Analytics Plan",
            price_cents=0,
            period="monthly",
            quota_credits=total,
            is_active=True,
        )
        db.add(plan)
        db.flush()
        subscription = Subscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            status="active",
            period_start=datetime(2026, 1, 1, tzinfo=UTC),
            period_end=datetime(2026, 12, 31, tzinfo=UTC),
            quota_credits_total=total,
            quota_credits_used=used,
            quota_credits_reserved=reserved,
        )
        db.add(subscription)
    subscription.quota_credits_total = total
    subscription.quota_credits_used = used
    subscription.quota_credits_reserved = reserved
    return subscription


def _tenant(db, *, slug: str, name: str) -> Tenant:
    tenant = Tenant(slug=slug, name=name)
    db.add(tenant)
    db.flush()
    return tenant


def _usage(
    db,
    *,
    tenant_id: str,
    subscription_id: str,
    task_id: str,
    task_status: str,
    usage_status: str,
    created_at: datetime,
    provider: str,
    model: str,
    capability: str,
    credits: str,
    cost_cents: int,
) -> None:
    db.add(
        VideoTask(
            id=task_id,
            tenant_id=tenant_id,
            status=task_status,
            video_mode="photo",
            mode="photo",
            created_at=created_at,
            updated_at=created_at,
            finished_at=created_at if task_status in {"done", "failed", "cancelled"} else None,
        )
    )
    db.flush()
    db.add(
        UsageRecord(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            video_task_id=task_id,
            capability=capability,
            provider=provider,
            model=model,
            unit="image" if capability == "image" else "second",
            quantity=Decimal("1.000"),
            credits=Decimal(credits),
            cost_cents=cost_cents,
            status=usage_status,
            created_at=created_at,
            settled_at=created_at if usage_status == "settled" else None,
        )
    )


def _usage_without_task(
    db,
    *,
    tenant_id: str,
    subscription_id: str,
    created_at: datetime,
    provider: str,
    model: str,
    capability: str,
    credits: str,
    cost_cents: int,
) -> None:
    db.add(
        UsageRecord(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            video_task_id=None,
            capability=capability,
            provider=provider,
            model=model,
            unit="call",
            quantity=Decimal("1.000"),
            credits=Decimal(credits),
            cost_cents=cost_cents,
            status="settled",
            created_at=created_at,
            settled_at=created_at,
        )
    )


def _seed_analytics_fixture(auth_db, tenant_id: str) -> str:
    with auth_db() as db:
        acme_sub = _subscription(db, tenant_id, total=1000, used=120, reserved=30)
        beta = _tenant(db, slug="beta", name="Beta LLC")
        beta_sub = _subscription(db, beta.id, total=500, used=70, reserved=5)
        db.flush()

        _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=acme_sub.id,
            task_id="analytics-acme-image",
            task_status="done",
            usage_status="settled",
            created_at=_dt(5),
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            credits="10.50",
            cost_cents=120,
        )
        _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=acme_sub.id,
            task_id="analytics-acme-tts",
            task_status="done",
            usage_status="settled",
            created_at=_dt(6),
            provider="doubao",
            model="seed-tts",
            capability="tts",
            credits="4.50",
            cost_cents=60,
        )
        _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=acme_sub.id,
            task_id="analytics-acme-failed",
            task_status="failed",
            usage_status="released",
            created_at=_dt(7),
            provider="omnihuman",
            model="avatar",
            capability="avatar",
            credits="99.00",
            cost_cents=9999,
        )
        _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=acme_sub.id,
            task_id="analytics-acme-reserved",
            task_status="running",
            usage_status="reserved",
            created_at=_dt(8),
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            credits="88.00",
            cost_cents=8888,
        )
        _usage_without_task(
            db,
            tenant_id=tenant_id,
            subscription_id=acme_sub.id,
            created_at=_dt(12),
            provider="deepseek",
            model="deepseek-chat",
            capability="llm",
            credits="2.00",
            cost_cents=20,
        )
        _usage(
            db,
            tenant_id=tenant_id,
            subscription_id=acme_sub.id,
            task_id="analytics-acme-outside",
            task_status="done",
            usage_status="settled",
            created_at=datetime(2026, 5, 20, 9, 0, tzinfo=UTC),
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            credits="77.00",
            cost_cents=7777,
        )
        _usage(
            db,
            tenant_id=beta.id,
            subscription_id=beta_sub.id,
            task_id="analytics-beta-image",
            task_status="done",
            usage_status="settled",
            created_at=_dt(10),
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            credits="30.00",
            cost_cents=300,
        )
        _usage(
            db,
            tenant_id=beta.id,
            subscription_id=beta_sub.id,
            task_id="analytics-beta-failed",
            task_status="failed",
            usage_status="released",
            created_at=_dt(11),
            provider="apimart",
            model="gpt-image-2",
            capability="image",
            credits="66.00",
            cost_cents=6666,
        )
        db.commit()
        return beta.id


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/analytics/overview",
        "/api/v1/admin/analytics/by-tenant",
        "/api/v1/admin/analytics/by-provider",
        "/api/v1/admin/analytics/timeseries",
    ],
)
def test_analytics_endpoints_require_admin(path, auth_context, auth_db) -> None:
    with auth_db() as db:
        user = db.get(User, auth_context["user_id"])
        assert user is not None
        user.role = "creator"
        db.commit()

    response = TestClient(app).get(path, headers=auth_context["headers"])

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_overview_counts_only_settled_usage_and_failed_rate_denominator(
    auth_context,
    auth_db,
) -> None:
    _seed_analytics_fixture(auth_db, auth_context["tenant_id"])

    response = TestClient(app).get(
        "/api/v1/admin/analytics/overview",
        params=PERIOD_PARAMS,
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "total_credits_used": 47.0,
        "total_cost_cents": 500,
        "task_count": 5,
        "success_count": 3,
        "failed_count": 2,
        "tenant_count": 2,
        "period": {"from": "2026-06-01", "to": "2026-06-30"},
    }


def test_by_tenant_returns_balance_sorting_pagination_and_success_rate(
    auth_context,
    auth_db,
) -> None:
    _seed_analytics_fixture(auth_db, auth_context["tenant_id"])

    response = TestClient(app).get(
        "/api/v1/admin/analytics/by-tenant",
        params=PERIOD_PARAMS | {"sort": "credits_desc", "limit": 1, "offset": 0},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert data["items"] == [
        {
            "tenant_id": data["items"][0]["tenant_id"],
            "tenant_name": "Beta LLC",
            "credits_used": 30.0,
            "cost_cents": 300,
            "task_count": 2,
            "success_rate": 0.5,
            "balance": {"total": 500, "used": 70, "reserved": 5, "remaining": 425},
        }
    ]

    second_page = TestClient(app).get(
        "/api/v1/admin/analytics/by-tenant",
        params=PERIOD_PARAMS | {"sort": "credits_desc", "limit": 1, "offset": 1},
        headers=auth_context["headers"],
    )

    assert second_page.status_code == 200
    second = second_page.json()["data"]
    assert second["total"] == 2
    assert second["items"][0]["tenant_name"] == "Acme Studio"
    assert second["items"][0]["credits_used"] == 17.0
    assert second["items"][0]["task_count"] == 3
    assert second["items"][0]["success_rate"] == pytest.approx(0.6667)
    assert second["items"][0]["balance"] == {
        "total": 1000,
        "used": 120,
        "reserved": 30,
        "remaining": 850,
    }


def test_by_provider_groups_provider_model_and_share_pct(auth_context, auth_db) -> None:
    _seed_analytics_fixture(auth_db, auth_context["tenant_id"])

    response = TestClient(app).get(
        "/api/v1/admin/analytics/by-provider",
        params=PERIOD_PARAMS,
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assert response.json()["data"]["items"] == [
        {
            "provider": "apimart",
            "model": "gpt-image-2",
            "credits_used": 40.5,
            "cost_cents": 420,
            "task_count": 2,
            "share_pct": 86.17,
        },
        {
            "provider": "doubao",
            "model": "seed-tts",
            "credits_used": 4.5,
            "cost_cents": 60,
            "task_count": 1,
            "share_pct": 9.57,
        },
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "credits_used": 2.0,
            "cost_cents": 20,
            "task_count": 1,
            "share_pct": 4.26,
        },
    ]


def test_timeseries_returns_day_and_week_buckets(auth_context, auth_db) -> None:
    _seed_analytics_fixture(auth_db, auth_context["tenant_id"])

    day_response = TestClient(app).get(
        "/api/v1/admin/analytics/timeseries",
        params={"from": "2026-06-05", "to": "2026-06-07", "granularity": "day"},
        headers=auth_context["headers"],
    )

    assert day_response.status_code == 200
    assert day_response.json()["data"]["buckets"] == [
        {"date": "2026-06-05", "credits_used": 10.5, "cost_cents": 120, "task_count": 1},
        {"date": "2026-06-06", "credits_used": 4.5, "cost_cents": 60, "task_count": 1},
        {"date": "2026-06-07", "credits_used": 0.0, "cost_cents": 0, "task_count": 0},
    ]

    week_response = TestClient(app).get(
        "/api/v1/admin/analytics/timeseries",
        params=PERIOD_PARAMS | {"granularity": "week"},
        headers=auth_context["headers"],
    )

    assert week_response.status_code == 200
    buckets = week_response.json()["data"]["buckets"]
    assert buckets[0] == {
        "date": "2026-06-01",
        "credits_used": 15.0,
        "cost_cents": 180,
        "task_count": 2,
    }
    assert buckets[1] == {
        "date": "2026-06-08",
        "credits_used": 32.0,
        "cost_cents": 320,
        "task_count": 2,
    }


def test_analytics_empty_data_and_invalid_period(auth_context) -> None:
    client = TestClient(app)
    empty = client.get(
        "/api/v1/admin/analytics/overview",
        params=PERIOD_PARAMS,
        headers=auth_context["headers"],
    )

    assert empty.status_code == 200
    assert empty.json()["data"] == {
        "total_credits_used": 0.0,
        "total_cost_cents": 0,
        "task_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "tenant_count": 0,
        "period": {"from": "2026-06-01", "to": "2026-06-30"},
    }

    invalid = client.get(
        "/api/v1/admin/analytics/overview",
        params={"from": "2026-07-01", "to": "2026-06-01"},
        headers=auth_context["headers"],
    )

    assert invalid.status_code == 422
