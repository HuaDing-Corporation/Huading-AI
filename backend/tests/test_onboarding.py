"""Onboarding bugs caught by the real OmniHuman e2e (ONBOARDING-FIX-0001).

P0-B: register-tenant must create a default active subscription.
P0-A: 下单 must persist video_task before its task_asset (FK ordering).
P1:   duplicate slug must return 409, not 500.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Asset, Subscription, TaskAsset, UsageRecord, VideoTask, Voice
from app.main import app


class _FakeAvatarTask:
    """Stand-in for the Celery task so 下单 doesn't enqueue to a real broker."""

    def __init__(self) -> None:
        self.enqueued: dict[str, object] = {}

    def apply_async(self, *, args, task_id, queue=None):
        self.enqueued.update({"args": args, "task_id": task_id, "queue": queue})
        return type("Result", (), {"status": "PENDING"})()


def _seed_voice_and_avatar(db, tenant_id: str) -> tuple[str, str]:
    voice = Voice(
        provider="edge-tts",
        voice_code="zh-CN-XiaoxiaoNeural",
        display_name="Xiaoxiao",
        gender="female",
        language="zh-CN",
        is_active=True,
    )
    avatar = Asset(
        tenant_id=tenant_id,
        type="avatar_image",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/avatar.png",
        mime_type="image/png",
        status="ready",
    )
    db.add_all([voice, avatar])
    db.commit()
    return voice.id, avatar.id


# ── P0-B: register creates a default subscription ────────────────────────────


def test_register_creates_active_subscription(auth_context, auth_db) -> None:
    # auth_context registers a tenant; seed_plan provides the default 'basic' plan.
    with auth_db() as db:
        sub = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
    assert sub is not None, "register-tenant must create a subscription (P0-B)"
    assert sub.status == "active"
    assert sub.quota_credits_total == 1000
    assert sub.quota_credits_used == 0
    assert sub.quota_credits_reserved == 0


def test_registered_tenant_can_order_avatar_talk_without_manual_subscription(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    """P0-B + P0-A end-to-end: a freshly registered tenant (subscription created by
    register, NOT manually seeded) can 下单, and both video_task + task_asset persist
    with no FK violation."""
    with auth_db() as db:
        voice_id, avatar_id = _seed_voice_and_avatar(db, auth_context["tenant_id"])

    from app.api.v1.routes import videos as videos_route

    fake = _FakeAvatarTask()
    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", fake)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={
            "topic": "三分钟看懂咖啡",
            "script": "这是一段测试口播文案。",
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
            "speed": 1.0,
            "aspect_ratio": "9:16",
            "subtitle_enabled": True,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202, resp.text
    task_id = resp.json()["data"]["id"]
    assert task_id and fake.enqueued["task_id"] == task_id
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task is not None and task.mode == "avatar_talk"
        task_asset = db.scalar(select(TaskAsset).where(TaskAsset.video_task_id == task_id))
        assert task_asset is not None and task_asset.role == "input_avatar"
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        assert usage is not None and usage.status == "reserved"


# ── P1: duplicate slug → 409, not 500 ────────────────────────────────────────


def test_duplicate_slug_returns_409(auth_context) -> None:
    # auth_context already registered slug "acme"; a second register must 409.
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": "acme",
            "tenant_name": "Acme Two",
            "email": "other@example.com",
            "password": "secret-pass",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "tenant_slug_taken"


# ── P0-B edge: no active plan → graceful (register still 201, no subscription) ─


def test_register_without_plan_is_graceful(auth_db) -> None:
    # auth_db alone seeds NO plan (only seed_plan/auth_context does). Register must
    # still succeed (201) and log a warning rather than 500 / hard-fail.
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": "noplan",
            "tenant_name": "No Plan Co",
            "email": "owner@noplan.test",
            "password": "secret-pass",
        },
    )
    assert resp.status_code == 201, resp.text
    tenant_id = resp.json()["data"]["tenant"]["id"]
    with auth_db() as db:
        sub = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    assert sub is None, "no plan → no subscription (graceful), but tenant still registered"
