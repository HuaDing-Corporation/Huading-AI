from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_object_storage
from app.db.models import (
    CreditRate,
    PlatformAccount,
    PublishJob,
    Subscription,
    UsageRecord,
    VideoTask,
)
from app.main import app


class _Storage:
    bucket = "publish-test-bucket"

    def __init__(self) -> None:
        self.saved: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.saved[key] = (content, content_type)
        return f"memory://{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.saved[key][0]

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        suffix = "&download=1" if download_filename else ""
        return f"https://storage.test/{key}?ttl={expires_in}{suffix}"

    def delete_object(self, key: str) -> None:
        self.saved.pop(key, None)


def _register_tenant(client: TestClient, slug: str) -> dict:
    resp = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": slug,
            "tenant_name": slug.title(),
            "email": f"owner-{slug}@example.com",
            "password": "unit-pass-123",
        },
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    return {
        "headers": {"Authorization": f"Bearer {data['token']['access_token']}"},
        "tenant_id": data["tenant"]["id"],
    }


def _active_subscription(db, tenant_id: str) -> Subscription:
    subscription = db.scalar(
        select(Subscription)
        .where(Subscription.tenant_id == tenant_id, Subscription.status == "active")
        .order_by(Subscription.period_end.desc())
    )
    assert subscription is not None
    return subscription


def _seed_copy_billing(db, tenant_id: str) -> str:
    subscription = _active_subscription(db, tenant_id)
    subscription.quota_credits_total = 500
    subscription.quota_credits_used = 0
    subscription.quota_credits_reserved = 0
    db.add(
        CreditRate(
            tenant_id=None,
            capability="llm",
            unit="call",
            credits_per_unit=Decimal("2.0000"),
        )
    )
    db.commit()
    return subscription.id


def _seed_source(
    db,
    *,
    tenant_id: str,
    user_id: str | None,
    source_id: str,
    source_kind: str = "video",
    status: str = "done",
) -> None:
    is_image = source_kind == "image"
    media_key = (
        f"tenants/{tenant_id}/photos/{source_id}/output.png"
        if is_image
        else f"tenants/{tenant_id}/videos/{source_id}/final.mp4"
    )
    thumbnail_key = media_key if is_image else f"tenants/{tenant_id}/videos/{source_id}/cover.png"
    db.add(
        VideoTask(
            id=source_id,
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            status=status,
            mode="photo" if is_image else "avatar_talk",
            video_mode="photo" if is_image else "avatar_talk",
            progress=100 if status == "done" else 20,
            topic="Launch topic",
            script="Original selling points for launch.",
            storage_bucket="publish-test-bucket",
            storage_key=media_key,
            thumbnail_key=thumbnail_key,
            content_type="image/png" if is_image else "video/mp4",
            finished_at=datetime.now(UTC) if status == "done" else None,
        )
    )
    db.commit()


def _patch_publish_llm(monkeypatch, payloads: list[dict]) -> None:
    from app.services import copy as copy_service

    class _FakeDeepSeek:
        async def generate_text(self, payload: dict):
            payloads.append(payload)
            platform = payload["platform_id"]
            title = f"{platform} title " + ("x" * 100)
            hashtags = [f"#{platform}{index}" for index in range(1, 8)]
            return {
                "text": json.dumps(
                    {
                        "title": title,
                        "body": f"{platform} body for Launch topic",
                        "hashtags": hashtags,
                    },
                    ensure_ascii=False,
                ),
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "usage": {
                    "prompt_tokens": 100_000,
                    "completion_tokens": 50_000,
                    "total_tokens": 150_000,
                },
            }

    monkeypatch.setattr(copy_service.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(copy_service.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(copy_service.settings, "engine_llm_model", "m")
    monkeypatch.setattr(
        copy_service,
        "resolve",
        lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        raising=False,
    )


def test_publish_platforms_returns_configured_public_catalog(auth_context) -> None:
    resp = TestClient(app).get("/api/v1/publish/platforms", headers=auth_context["headers"])

    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert [item["id"] for item in items] == [
        "douyin",
        "kuaishou",
        "wxchannels",
        "xiaohongshu",
        "bilibili",
    ]
    assert all(item["publish_url"].startswith("https://") for item in items)
    assert all("credential" not in item for item in items)
    xhs = next(item for item in items if item["id"] == "xiaohongshu")
    assert xhs["title_max"] == 20
    assert xhs["hashtag_max"] >= 5


def test_publish_drafts_generates_per_platform_copy_and_charges_per_platform(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    payloads: list[dict] = []
    _patch_publish_llm(monkeypatch, payloads)
    with auth_db() as db:
        subscription_id = _seed_copy_billing(db, auth_context["tenant_id"])
        _seed_source(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            source_id="publish-video-unit",
        )

    storage = _Storage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/publish/drafts",
            json={
                "source_kind": "video",
                "source_task_id": "publish-video-unit",
                "platforms": ["douyin", "xiaohongshu"],
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["id"]
    assert [item["platform_id"] for item in data["items"]] == ["douyin", "xiaohongshu"]
    douyin, xhs = data["items"]
    assert len(douyin["title"]) <= 55
    assert 3 <= len(douyin["hashtags"]) <= 5
    assert len(xhs["title"]) <= 20
    assert len(xhs["hashtags"]) <= 10
    assert douyin["media_url"].startswith(
        "https://storage.test/tenants/"
    )
    assert douyin["cover_url"].startswith("https://storage.test/tenants/")
    assert douyin["publish_url"] == "https://creator.douyin.com/"

    assert len(payloads) == 2
    assert payloads[0]["platform_id"] == "douyin"
    assert payloads[1]["platform_id"] == "xiaohongshu"
    assert "title <= 55" in payloads[0]["user_prompt"]
    assert "title <= 20" in payloads[1]["user_prompt"]

    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 4
        usage = db.scalars(
            select(UsageRecord)
            .where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "llm",
                UsageRecord.unit == "token",
            )
            .order_by(UsageRecord.created_at)
        ).all()
        assert len(usage) == 2
        assert all(record.status == "settled" for record in usage)
        assert all(record.credits == Decimal("2.00") for record in usage)
        assert all(record.quantity == Decimal("150000.000") for record in usage)
        assert all(record.cost_cents == 20 for record in usage)
        assert db.scalar(select(func.count()).select_from(PlatformAccount)) == 0
        assert db.scalar(select(func.count()).select_from(PublishJob)) == 0


def test_publish_drafts_hide_cross_tenant_or_unfinished_sources(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    payloads: list[dict] = []
    _patch_publish_llm(monkeypatch, payloads)
    client = TestClient(app)
    other = _register_tenant(client, "publish-other")
    with auth_db() as db:
        _seed_copy_billing(db, auth_context["tenant_id"])
        _seed_source(
            db,
            tenant_id=other["tenant_id"],
            user_id=None,
            source_id="other-video-unit",
        )
        _seed_source(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            source_id="queued-video-unit",
            status="running",
        )

    cross = client.post(
        "/api/v1/publish/drafts",
        json={
            "source_kind": "video",
            "source_task_id": "other-video-unit",
            "platforms": ["douyin"],
        },
        headers=auth_context["headers"],
    )
    unfinished = client.post(
        "/api/v1/publish/drafts",
        json={
            "source_kind": "video",
            "source_task_id": "queued-video-unit",
            "platforms": ["douyin"],
        },
        headers=auth_context["headers"],
    )

    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "PUBLISH_SOURCE_NOT_FOUND"
    assert unfinished.status_code == 404
    assert unfinished.json()["error"]["code"] == "PUBLISH_SOURCE_NOT_FOUND"
    assert payloads == []


def test_publish_records_are_tenant_scoped_patchable_and_soft_deleted(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    payloads: list[dict] = []
    _patch_publish_llm(monkeypatch, payloads)
    client = TestClient(app)
    other = _register_tenant(client, "publish-record-other")
    with auth_db() as db:
        _seed_copy_billing(db, auth_context["tenant_id"])
        _seed_source(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            source_id="record-video-unit",
        )

    storage = _Storage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        created = client.post(
            "/api/v1/publish/drafts",
            json={
                "source_kind": "video",
                "source_task_id": "record-video-unit",
                "platforms": ["douyin", "bilibili"],
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)
    assert created.status_code == 201
    record_id = created.json()["data"]["id"]

    listing = client.get("/api/v1/publish/records", headers=auth_context["headers"])
    assert listing.status_code == 200
    assert listing.json()["data"]["items"][0]["id"] == record_id
    assert listing.json()["data"]["items"][0]["platforms"] == [
        {"platform_id": "douyin", "status": "draft"},
        {"platform_id": "bilibili", "status": "draft"},
    ]

    cross_patch = client.patch(
        f"/api/v1/publish/records/{record_id}",
        json={"platform_id": "douyin", "status": "published"},
        headers=other["headers"],
    )
    assert cross_patch.status_code == 404
    assert cross_patch.json()["error"]["code"] == "PUBLISH_RECORD_NOT_FOUND"

    patched = client.patch(
        f"/api/v1/publish/records/{record_id}",
        json={"platform_id": "douyin", "status": "published"},
        headers=auth_context["headers"],
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["platforms"][0] == {
        "platform_id": "douyin",
        "status": "published",
    }

    cross_delete = client.delete(
        f"/api/v1/publish/records/{record_id}",
        headers=other["headers"],
    )
    assert cross_delete.status_code == 404
    assert cross_delete.json()["error"]["code"] == "PUBLISH_RECORD_NOT_FOUND"

    deleted = client.delete(
        f"/api/v1/publish/records/{record_id}",
        headers=auth_context["headers"],
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["id"] == record_id
    assert deleted.json()["data"]["deleted_at"]

    after_delete = client.get("/api/v1/publish/records", headers=auth_context["headers"])
    assert after_delete.status_code == 200
    assert after_delete.json()["data"] == {"items": [], "total": 0}
