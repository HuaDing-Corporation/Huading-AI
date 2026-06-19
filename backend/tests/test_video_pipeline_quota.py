from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Asset, CreditRate, Plan, Subscription, UsageRecord, VideoTask, Voice
from app.main import app
from app.services import quota


def _seed_subscription(db, tenant_id: str, *, total: int, used: int = 0, reserved: int = 0):
    now = datetime.now(UTC)
    plan = Plan(
        code=f"plan-{tenant_id}-{total}-{used}-{reserved}",
        name="Plan",
        price_cents=0,
        period="monthly",
        quota_credits=total,
        max_concurrent=1,
        seat_limit=3,
    )
    db.add(plan)
    db.flush()
    sub = Subscription(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status="active",
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=30),
        quota_credits_total=total,
        quota_credits_used=used,
        quota_credits_reserved=reserved,
    )
    db.add(sub)
    db.add_all(
        [
            CreditRate(capability="avatar", unit="second", credits_per_unit=Decimal("1.0000")),
            CreditRate(capability="tts", unit="second", credits_per_unit=Decimal("0.2000")),
        ]
    )
    db.commit()
    return sub


def _seed_voice_avatar(db, tenant_id: str):
    voice = Voice(
        provider="edge-tts",
        voice_code="zh-CN-XiaoxiaoNeural",
        display_name="Xiaoxiao",
        gender="female",
    )
    avatar = Asset(
        tenant_id=tenant_id,
        type="avatar_image",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/avatar.png",
        status="ready",
    )
    db.add_all([voice, avatar])
    db.commit()
    return voice, avatar


def test_avatar_talk_order_with_insufficient_quota_rejects_without_task_or_usage(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        _seed_subscription(db, auth_context["tenant_id"], total=1)
        voice, avatar = _seed_voice_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id
        avatar_id = avatar.id

    from app.api.v1.routes import videos as videos_route

    class _UnexpectedTask:
        def apply_async(self, **kwargs):  # pragma: no cover
            raise AssertionError("insufficient quota must not enqueue")

    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _UnexpectedTask())
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={
            "topic": "x" * 100,
            "script": "x" * 100,
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "TENANT_QUOTA_EXCEEDED"
    with auth_db() as db:
        assert db.scalars(select(UsageRecord)).all() == []


def test_release_reserved_quota_marks_usage_released(auth_context, auth_db) -> None:
    with auth_db() as db:
        sub = _seed_subscription(db, auth_context["tenant_id"], total=100)
        db.add(VideoTask(id="task-release", tenant_id=auth_context["tenant_id"], status="queued"))
        db.flush()
        record = UsageRecord(
            tenant_id=auth_context["tenant_id"],
            subscription_id=sub.id,
            video_task_id="task-release",
            capability="avatar",
            provider="omnihuman",
            model="m",
            unit="second",
            quantity=Decimal("10"),
            credits=Decimal("12.00"),
            cost_cents=0,
            status="reserved",
        )
        sub.quota_credits_reserved = 12
        db.add(record)
        db.commit()

        quota.release_reserved_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            video_task_id="task-release",
        )
        db.commit()

        assert sub.quota_credits_reserved == 0
        assert sub.quota_credits_used == 0
        assert record.status == "released"


def test_settle_reserved_quota_moves_reserved_to_used(auth_context, auth_db) -> None:
    with auth_db() as db:
        sub = _seed_subscription(db, auth_context["tenant_id"], total=100)
        db.add(VideoTask(id="task-settle", tenant_id=auth_context["tenant_id"], status="queued"))
        db.flush()
        record = UsageRecord(
            tenant_id=auth_context["tenant_id"],
            subscription_id=sub.id,
            video_task_id="task-settle",
            capability="avatar",
            provider="omnihuman",
            model="m",
            unit="second",
            quantity=Decimal("10"),
            credits=Decimal("12.00"),
            cost_cents=0,
            status="reserved",
        )
        sub.quota_credits_reserved = 12
        db.add(record)
        db.commit()

        quota.settle_reserved_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            video_task_id="task-settle",
            actual_seconds=8,
            cost_cents=800,
        )
        db.commit()

        assert sub.quota_credits_reserved == 0
        assert sub.quota_credits_used == 10
        assert record.status == "settled"
        assert record.quantity == Decimal("8.000")
        assert record.cost_cents == 800
        assert record.settled_at is not None
