from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.deps import get_object_storage, get_progress_store
from app.db.models import Asset, CreditRate, Plan, Subscription, UsageRecord, VideoTask, Voice
from app.main import app
from app.schemas.videos import VideoGenerateRequest


class _FakeStorage:
    def __init__(self) -> None:
        self.bucket = "test-bucket"
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


class _MemProgressStore:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    def update(self, task_id: str, **fields) -> None:
        snapshot = self.data.get(task_id, {})
        snapshot.update({k: v for k, v in fields.items() if v is not None})
        snapshot["task_id"] = task_id
        self.data[task_id] = snapshot

    def read(self, task_id: str) -> dict | None:
        return self.data.get(task_id)


def _seed_billing(db, tenant_id: str) -> Subscription:
    now = datetime.now(UTC)
    plan = Plan(
        code=f"plan-{tenant_id}",
        name="Test Plan",
        price_cents=0,
        period="monthly",
        quota_credits=100,
        max_concurrent=1,
        seat_limit=3,
    )
    db.add(plan)
    db.flush()
    subscription = Subscription(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status="active",
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=30),
        quota_credits_total=100,
        quota_credits_used=25,
        quota_credits_reserved=5,
    )
    db.add(subscription)
    db.add_all(
        [
            CreditRate(
                tenant_id=None,
                capability="avatar",
                unit="second",
                credits_per_unit=Decimal("1.0000"),
            ),
            CreditRate(
                tenant_id=None,
                capability="tts",
                unit="second",
                credits_per_unit=Decimal("0.2000"),
            ),
            CreditRate(
                tenant_id=None,
                capability="video",
                unit="second",
                credits_per_unit=Decimal("2.0000"),
            ),
        ]
    )
    db.commit()
    return subscription


def _seed_voice_and_avatar(db, tenant_id: str) -> tuple[Voice, Asset]:
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
    return voice, avatar


def test_quota_returns_current_subscription_totals(auth_context, auth_db) -> None:
    with auth_db() as db:
        _seed_billing(db, auth_context["tenant_id"])

    client = TestClient(app)
    resp = client.get("/api/v1/quota", headers=auth_context["headers"])

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "total": 100,
        "used": 25,
        "reserved": 5,
        "remaining": 70,
    }


def test_catalog_endpoints_return_voices_and_platform_avatar_presets(
    auth_context,
    auth_db,
) -> None:
    storage = _FakeStorage()
    with auth_db() as db:
        db.add(
            Voice(
                provider="edge-tts",
                voice_code="zh-CN-YunjianNeural",
                display_name="Yunjian",
                gender="male",
                language="zh-CN",
                sample_url="https://sample.test/yunjian.mp3",
                is_active=True,
            )
        )
        db.add(
            Asset(
                tenant_id=None,
                type="avatar_image",
                source="preset",
                storage_key="platform/avatars/default.png",
                mime_type="image/png",
                status="ready",
                metadata_={"display_name": "Default Presenter"},
            )
        )
        db.commit()

    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        voices = client.get("/api/v1/voices", headers=auth_context["headers"])
        avatars = client.get("/api/v1/avatars/presets", headers=auth_context["headers"])
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert voices.status_code == 200
    assert voices.json()["data"]["total"] == 1
    assert voices.json()["data"]["items"][0]["voice_code"] == "zh-CN-YunjianNeural"
    assert avatars.status_code == 200
    assert avatars.json()["data"]["total"] == 1
    assert avatars.json()["data"]["items"][0]["display_name"] == "Default Presenter"
    assert avatars.json()["data"]["items"][0]["thumbnail_url"].startswith(
        "https://storage.test/platform/avatars/default.png"
    )


def test_upload_images_creates_avatar_asset_under_tenant_scope(
    auth_context,
    auth_db,
) -> None:
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/uploads/images",
            files={"file": ("avatar.png", b"\x89PNG fake", "image/png")},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["asset_id"]
    assert data["type"] == "avatar_image"
    assert data["status"] == "ready"
    with auth_db() as db:
        asset = db.get(Asset, data["asset_id"])
        assert asset is not None
        assert asset.tenant_id == auth_context["tenant_id"]
        assert asset.storage_key.startswith(f"tenants/{auth_context['tenant_id']}/uploads/")
        assert asset.storage_key in storage.saved


def test_avatar_talk_order_reserves_quota_and_returns_queued_id(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        voice, avatar = _seed_voice_and_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id
        avatar_id = avatar.id
        subscription_id = subscription.id

    enqueued: dict[str, object] = {}

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.update({"args": args, "task_id": task_id, "queue": queue})
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _FakeTask())
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={
            "topic": "羊绒大衣怎么选",
            "script": "这是一段测试口播文案。",
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
            "speed": 1.0,
            "aspect_ratio": "9:16",
            "subtitle_enabled": True,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["id"]
    assert data["status"] == "queued"
    assert enqueued["task_id"] == data["id"]
    with auth_db() as db:
        task = db.get(VideoTask, data["id"])
        assert task is not None
        assert task.mode == "avatar_talk"
        assert task.tenant_id == auth_context["tenant_id"]
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved > 5
        reserved = db.query(UsageRecord).filter_by(video_task_id=data["id"]).one()
        assert reserved.status == "reserved"
        assert reserved.capability == "avatar"


def test_avatar_talk_order_without_script_leaves_worker_to_generate_it(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        _seed_billing(db, auth_context["tenant_id"])
        voice, avatar = _seed_voice_and_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id
        avatar_id = avatar.id

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _FakeTask())
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={
            "topic": "cashmere coat",
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
            "video_mode": "avatar_talk",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    with auth_db() as db:
        task = db.get(VideoTask, resp.json()["data"]["id"])
        assert task is not None
        assert task.topic == "cashmere coat"
        assert task.script is None


def test_seedance_i2v_order_routes_before_avatar_when_voice_is_present(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        voice, _avatar = _seed_voice_and_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id
        subscription_id = subscription.id

    enqueued: dict[str, object] = {}

    class _FakeI2VTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.update({"args": args, "task_id": task_id, "queue": queue})
            return type("Result", (), {"status": "PENDING"})()

    class _UnexpectedAvatarTask:
        def apply_async(self, **_kwargs):  # pragma: no cover
            raise AssertionError("seedance_i2v must route before avatar_talk")

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_seedance_i2v_task", _FakeI2VTask())
    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _UnexpectedAvatarTask())

    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={
            "topic": "premium scarf product benefits",
            "video_mode": "seedance_i2v",
            "image_key": "uploads/product.png",
            "voice_id": voice_id,
            "duration_sec": 30,
            "speed": 1.0,
            "aspect_ratio": "9:16",
            "subtitle_enabled": True,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert enqueued["task_id"] == data["id"]
    assert enqueued["queue"] == "avatar"
    assert enqueued["args"][0]["video_mode"] == "seedance_i2v"
    assert enqueued["args"][0]["image_key"] == "uploads/product.png"
    assert enqueued["args"][0]["duration_sec"] == 30
    with auth_db() as db:
        task = db.get(VideoTask, data["id"])
        assert task is not None
        assert task.mode == "seedance_i2v"
        assert task.video_mode == "seedance_i2v"
        assert task.voice_id == voice_id
        assert task.params["image_key"] == "uploads/product.png"
        assert task.params["duration_sec"] == 30
        assert task.duration_sec == 30
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == 65
        reserved = db.query(UsageRecord).filter_by(video_task_id=data["id"]).one()
        assert reserved.status == "reserved"
        assert reserved.capability == "video"
        assert reserved.provider == "seedance"
        assert reserved.quantity == Decimal("30")
        assert reserved.credits == Decimal("60.00")


def test_video_estimate_seedance_i2v_matches_reserved_quota_with_tenant_rate(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 500
        db.add(
            CreditRate(
                tenant_id=auth_context["tenant_id"],
                capability="video",
                unit="second",
                credits_per_unit=Decimal("3.0000"),
            )
        )
        voice, _avatar = _seed_voice_and_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved
        db.commit()

    class _FakeI2VTask:
        def apply_async(self, *, args, task_id, queue=None):
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_seedance_i2v_task", _FakeI2VTask())
    client = TestClient(app)
    payload = {
        "topic": "premium scarf product benefits",
        "video_mode": "seedance_i2v",
        "image_key": "uploads/product.png",
        "voice_id": voice_id,
        "duration_sec": 30,
    }

    estimate_resp = client.post(
        "/api/v1/videos/estimate",
        json=payload,
        headers=auth_context["headers"],
    )
    create_resp = client.post(
        "/api/v1/videos",
        json=payload,
        headers=auth_context["headers"],
    )

    assert estimate_resp.status_code == 200
    assert estimate_resp.json()["data"] == {
        "estimated_credits": 90,
        "unit": "credits",
        "note": "Estimated reservation; final settlement uses actual generated duration.",
    }
    assert create_resp.status_code == 202
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved - reserved_before == 90
        reserved = db.query(UsageRecord).filter_by(
            video_task_id=create_resp.json()["data"]["id"]
        ).one()
        assert reserved.quantity == Decimal("30")
        assert reserved.credits == Decimal("90.00")


def test_video_estimate_avatar_uses_existing_script_duration_and_tenant_rates(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        _seed_billing(db, auth_context["tenant_id"])
        db.add_all(
            [
                CreditRate(
                    tenant_id=auth_context["tenant_id"],
                    capability="avatar",
                    unit="second",
                    credits_per_unit=Decimal("1.5000"),
                ),
                CreditRate(
                    tenant_id=auth_context["tenant_id"],
                    capability="tts",
                    unit="second",
                    credits_per_unit=Decimal("0.5000"),
                ),
            ]
        )
        voice, avatar = _seed_voice_and_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id
        avatar_id = avatar.id
        db.commit()

    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos/estimate",
        json={
            "topic": "cashmere coat",
            "script": "x" * 20,
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
            "video_mode": "avatar_talk",
            "speed": 1.0,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["estimated_credits"] == 8
    assert data["unit"] == "credits"


def test_video_estimate_is_read_only_no_task_usage_or_reserved_change(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        voice, _avatar = _seed_voice_and_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id
        subscription_id = subscription.id
        before_reserved = subscription.quota_credits_reserved
        before_tasks = db.query(VideoTask).count()
        before_usage = db.query(UsageRecord).count()

    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos/estimate",
        json={
            "topic": "premium scarf product benefits",
            "video_mode": "seedance_i2v",
            "image_key": "uploads/product.png",
            "voice_id": voice_id,
            "duration_sec": 15,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == before_reserved
        assert db.query(VideoTask).count() == before_tasks
        assert db.query(UsageRecord).count() == before_usage


def test_video_estimate_uses_video_generate_validation(auth_context) -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos/estimate",
        json={
            "topic": "premium scarf product benefits",
            "video_mode": "seedance_i2v",
            "image_key": "uploads/product.png",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422


def test_video_estimate_implicit_avatar_requires_avatar_asset_id(auth_context) -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos/estimate",
        json={
            "topic": "cashmere coat",
            "voice_id": "voice-only",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_seedance_i2v_order_requires_voice(auth_context) -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={
            "topic": "premium scarf product benefits",
            "video_mode": "seedance_i2v",
            "image_key": "uploads/product.png",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422


def test_seedance_i2v_duration_is_clamped_and_forwarded(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 500
        db.commit()
        voice, _avatar = _seed_voice_and_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id

    enqueued: dict[str, object] = {}

    class _FakeI2VTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.update({"args": args, "task_id": task_id, "queue": queue})
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_seedance_i2v_task", _FakeI2VTask())
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={
            "topic": "premium scarf product benefits",
            "video_mode": "seedance_i2v",
            "image_key": "uploads/product.png",
            "voice_id": voice_id,
            "duration_sec": 999,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    assert enqueued["args"][0]["duration_sec"] == 120
    with auth_db() as db:
        task = db.get(VideoTask, resp.json()["data"]["id"])
        assert task.duration_sec == 120
        assert task.params["duration_sec"] == 120


def test_video_generate_request_clamps_seedance_duration() -> None:
    low = VideoGenerateRequest(
        topic="x",
        video_mode="seedance_i2v",
        image_key="uploads/product.png",
        voice_id="voice",
        duration_sec=3,
    )
    high = VideoGenerateRequest(
        topic="x",
        video_mode="seedance_i2v",
        image_key="uploads/product.png",
        voice_id="voice",
        duration_sec=999,
    )

    assert low.duration_sec == 5
    assert high.duration_sec == 120


def test_video_list_uses_limit_offset_and_returns_items_total(
    auth_context,
    auth_db,
) -> None:
    store = _MemProgressStore()
    storage = _FakeStorage()
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    with auth_db() as db:
        for index in range(3):
            db.add(
                VideoTask(
                    id=f"list-task-{index}",
                    tenant_id=auth_context["tenant_id"],
                    created_by_user_id=auth_context["user_id"],
                    mode="avatar_talk",
                    video_mode="avatar_talk",
                    status="done",
                    progress=100,
                    topic=f"topic-{index}",
                    script=f"script-{index}",
                    created_at=base_time + timedelta(minutes=index),
                )
            )
        db.commit()

    app.dependency_overrides[get_progress_store] = lambda: store
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/videos?limit=1&offset=1",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_progress_store, None)
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data) == {"items", "total"}
    assert data["total"] == 3
    assert [item["id"] for item in data["items"]] == ["list-task-1"]


def test_video_schema_rejects_invalid_aspect_ratio_before_db_check() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        VideoGenerateRequest.model_validate({"topic": "bad ratio", "aspect_ratio": "4:3"})


def test_video_progress_snapshot_accepts_percent_and_legacy_fraction() -> None:
    from app.api.v1.routes import videos as videos_route

    task = VideoTask(progress=5)

    assert videos_route._progress(task, {"progress": 10}) == 10
    assert videos_route._progress(task, {"progress": 85}) == 85
    assert videos_route._progress(task, {"progress": 0.1}) == 10
    assert videos_route._progress(task, {"progress": 200}) == 100
    assert videos_route._sse_progress({"progress": 10}) == 10
    assert videos_route._sse_progress({"progress": 0.1}) == 10


def test_video_sse_emits_new_enum_terminal_frame(auth_context, auth_db) -> None:
    store = _MemProgressStore()
    storage = _FakeStorage()
    with auth_db() as db:
        task = VideoTask(
            id="avatar-task-1",
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            mode="avatar_talk",
            video_mode="avatar_talk",
            status="done",
            progress=100,
            topic="topic",
            script="script",
            storage_key=f"tenants/{auth_context['tenant_id']}/videos/avatar-task-1/final.mp4",
        )
        db.add(task)
        db.commit()

    store.update(
        f"{auth_context['tenant_id']}:avatar-task-1",
        status="done",
        progress=100,
        step="upload",
        playback_url="https://storage.test/play.mp4",
        download_url="https://storage.test/download.mp4",
        thumbnail_url="https://storage.test/thumb.jpg",
    )
    app.dependency_overrides[get_progress_store] = lambda: store
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/videos/avatar-task-1/events",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_progress_store, None)
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    assert '"status": "done"' in resp.text
    assert '"progress": 100' in resp.text
    assert '"playback_url": "https://storage.test/play.mp4"' in resp.text
