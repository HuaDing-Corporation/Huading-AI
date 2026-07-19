from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.deps import get_object_storage, get_progress_store
from app.db.models import (
    Asset,
    CreditRate,
    Plan,
    Role,
    Subscription,
    TaskAsset,
    UsageRecord,
    User,
    VideoTask,
    Voice,
)
from app.main import app
from app.schemas.videos import ScenePromptRequest, VideoGenerateRequest


class _FakeStorage:
    def __init__(self, *, existing_keys: set[str] | None = None) -> None:
        self.bucket = "test-bucket"
        self.saved: dict[str, tuple[bytes, str]] = {}
        self.existing_keys = existing_keys or set()
        self.existence_checks: list[str] = []

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.saved[key] = (content, content_type)
        return f"memory://{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.saved[key][0]

    def object_exists(self, key: str) -> bool:
        self.existence_checks.append(key)
        return key in self.existing_keys or key in self.saved

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        suffix = "&download=1" if download_filename else ""
        return f"https://storage.test/{key}?ttl={expires_in}{suffix}"


def _tenant_storage(tenant_id: str, *relative_keys: str) -> _FakeStorage:
    return _FakeStorage(
        existing_keys={f"tenants/{tenant_id}/{relative_key}" for relative_key in relative_keys}
    )


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
        period_end=now + timedelta(days=60),
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
                credits_per_unit=Decimal("150.0000"),
            ),
            CreditRate(
                tenant_id=None,
                capability="tts",
                unit="character",
                credits_per_unit=Decimal("0.1000"),
            ),
            CreditRate(
                tenant_id=None,
                capability="video",
                unit="second",
                credits_per_unit=Decimal("80.0000"),
            ),
            CreditRate(
                tenant_id=None,
                capability="image",
                unit="image",
                credits_per_unit=Decimal("10.0000"),
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


def _seed_voice_and_avatar_video(db, tenant_id: str) -> tuple[Voice, Asset]:
    voice = Voice(
        provider="edge-tts",
        voice_code="zh-CN-XiaoxiaoNeural",
        display_name="Xiaoxiao",
        gender="female",
        language="zh-CN",
        is_active=True,
    )
    avatar_video = Asset(
        tenant_id=tenant_id,
        type="video",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/avatar-source.mp4",
        mime_type="video/mp4",
        size_bytes=8_000_000,
        duration_ms=9_500,
        width=960,
        height=960,
        status="ready",
        metadata_={
            "purpose": "avatar_source",
            "container": "mp4",
            "video_codec": "h264",
            "audio_codec": "aac",
        },
    )
    db.add_all([voice, avatar_video])
    db.commit()
    return voice, avatar_video


def test_avatar_talk_schema_accepts_exactly_one_avatar_source() -> None:
    import pytest
    from pydantic import ValidationError

    video_request = VideoGenerateRequest.model_validate(
        {
            "topic": "avatar video source",
            "voice_id": "voice-1",
            "avatar_video_asset_id": "video-1",
            "video_mode": "avatar_talk",
        }
    )

    assert video_request.avatar_asset_id is None
    assert video_request.avatar_video_asset_id == "video-1"

    invalid_payloads = [
        {
            "topic": "avatar video source",
            "voice_id": "voice-1",
            "video_mode": "avatar_talk",
        },
        {
            "topic": "avatar video source",
            "voice_id": "voice-1",
            "avatar_asset_id": "image-1",
            "avatar_video_asset_id": "video-1",
            "video_mode": "avatar_talk",
        },
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            VideoGenerateRequest.model_validate(payload)


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


def test_upload_videos_creates_avatar_source_asset_under_tenant_scope(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.api.v1.routes import uploads
    from app.api.v1.routes.videos import _AvatarVideoProbe

    storage = _FakeStorage()
    monkeypatch.setattr(
        uploads,
        "_probe_avatar_video_bytes",
        lambda _content, *, suffix: _AvatarVideoProbe(
            duration_ms=9_500,
            width=720,
            height=1280,
            container="mov,mp4,m4a,3gp,3g2,mj2",
            video_codec="h264",
            audio_codec="aac",
        ),
        raising=False,
    )
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/uploads/videos",
            files={
                "file": (
                    "avatar-source.mp4",
                    b"fake mp4 bytes",
                    "video/mp4;codecs=avc1.42E01E,mp4a.40.2",
                )
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["asset_id"]
    assert data["type"] == "video"
    assert data["status"] == "ready"
    with auth_db() as db:
        asset = db.get(Asset, data["asset_id"])
        assert asset is not None
        assert asset.tenant_id == auth_context["tenant_id"]
        assert asset.type == "video"
        assert asset.mime_type == "video/mp4"
        assert asset.source == "upload"
        assert asset.status == "ready"
        assert asset.size_bytes == len(b"fake mp4 bytes")
        assert asset.duration_ms == 9_500
        assert asset.width == 720
        assert asset.height == 1280
        assert asset.metadata_ == {
            "purpose": "avatar_source",
            "container": "mov,mp4,m4a,3gp,3g2,mj2",
            "video_codec": "h264",
            "audio_codec": "aac",
        }
        assert asset.storage_key.startswith(f"tenants/{auth_context['tenant_id']}/uploads/")
        assert asset.storage_key.endswith(".mp4")
        assert storage.saved[asset.storage_key] == (b"fake mp4 bytes", "video/mp4")


def test_upload_videos_reverse_prompt_allows_optional_or_non_aac_audio_only_for_purpose(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.api.v1.routes import uploads
    from app.api.v1.routes.videos import _AvatarVideoProbe

    def fake_probe(content: bytes, *, suffix: str) -> _AvatarVideoProbe:
        if content == b"silent reverse video":
            duration_ms, audio_codec, video_codec = 45_000, "", "h264"
        elif content == b"opus reverse video":
            duration_ms, audio_codec, video_codec = 59_000, "opus", "h264"
        elif content == b"overlong reverse video":
            duration_ms, audio_codec, video_codec = 60_001, "aac", "h264"
        elif content == b"vp9 reverse video":
            duration_ms, audio_codec, video_codec = 15_000, "aac", "vp9"
        else:
            duration_ms, audio_codec, video_codec = 9_500, "", "h264"
        return _AvatarVideoProbe(
            duration_ms=duration_ms,
            width=1080,
            height=1920,
            container="mov,mp4,m4a,3gp,3g2,mj2",
            video_codec=video_codec,
            audio_codec=audio_codec,
        )

    storage = _FakeStorage()
    monkeypatch.setattr(uploads, "_probe_avatar_video_bytes", fake_probe)
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        silent = client.post(
            "/api/v1/uploads/videos?purpose=reverse_prompt",
            files={"file": ("silent.mp4", b"silent reverse video", "video/mp4")},
            headers=auth_context["headers"],
        )
        opus = client.post(
            "/api/v1/uploads/videos?purpose=reverse_prompt",
            files={"file": ("opus.mp4", b"opus reverse video", "video/mp4")},
            headers=auth_context["headers"],
        )
        avatar_silent = client.post(
            "/api/v1/uploads/videos",
            files={"file": ("avatar-silent.mp4", b"avatar silent video", "video/mp4")},
            headers=auth_context["headers"],
        )
        overlong = client.post(
            "/api/v1/uploads/videos?purpose=reverse_prompt",
            files={
                "file": ("overlong.mp4", b"overlong reverse video", "video/mp4")
            },
            headers=auth_context["headers"],
        )
        vp9 = client.post(
            "/api/v1/uploads/videos?purpose=reverse_prompt",
            files={"file": ("vp9.mp4", b"vp9 reverse video", "video/mp4")},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert silent.status_code == 201
    assert opus.status_code == 201
    assert avatar_silent.status_code == 422
    assert avatar_silent.json()["error"]["code"] == "AVATAR_VIDEO_AUDIO_CODEC_INVALID"
    assert overlong.status_code == 422
    assert overlong.json()["error"]["code"] == "REVERSE_PROMPT_VIDEO_DURATION_INVALID"
    assert vp9.status_code == 422
    assert vp9.json()["error"]["code"] == "REVERSE_PROMPT_VIDEO_CODEC_INVALID"

    with auth_db() as db:
        assets = list(
            db.scalars(
                select(Asset)
                .where(
                    Asset.tenant_id == auth_context["tenant_id"],
                    Asset.type == "video",
                )
                .order_by(Asset.duration_ms)
            )
        )
        assert [asset.duration_ms for asset in assets] == [45_000, 59_000]
        assert [asset.metadata_["purpose"] for asset in assets] == [
            "reverse_prompt",
            "reverse_prompt",
        ]
        assert [asset.metadata_["audio_codec"] for asset in assets] == ["", "opus"]


def test_upload_videos_rejects_invalid_source_without_asset(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.api.v1.routes import uploads
    from app.api.v1.routes.videos import _AvatarVideoProbe

    storage = _FakeStorage()
    monkeypatch.setattr(
        uploads,
        "_probe_avatar_video_bytes",
        lambda _content, *, suffix: _AvatarVideoProbe(
            duration_ms=10_001,
            width=720,
            height=1280,
            container="mov,mp4,m4a,3gp,3g2,mj2",
            video_codec="h264",
            audio_codec="aac",
        ),
        raising=False,
    )
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        bad_type = client.post(
            "/api/v1/uploads/videos",
            files={"file": ("avatar-source.mov", b"mov bytes", "video/quicktime")},
            headers=auth_context["headers"],
        )
        bad_duration = client.post(
            "/api/v1/uploads/videos",
            files={"file": ("avatar-source.mp4", b"too long", "video/mp4")},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert bad_type.status_code == 415
    assert bad_duration.status_code == 422
    assert storage.saved == {}
    with auth_db() as db:
        assert db.scalar(select(Asset).where(Asset.type == "video")) is None


def test_avatar_talk_order_reserves_quota_and_returns_queued_id(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 10000
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
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 10000
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


def test_avatar_talk_order_accepts_avatar_video_source_and_reserves_same_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 10000
        voice, avatar_video = _seed_voice_and_avatar_video(db, auth_context["tenant_id"])
        voice_id = voice.id
        avatar_video_id = avatar_video.id
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
            "topic": "cashmere coat",
            "script": "short avatar video source script",
            "voice_id": voice_id,
            "avatar_video_asset_id": avatar_video_id,
            "video_mode": "avatar_talk",
            "speed": 1.0,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert enqueued["task_id"] == data["id"]
    assert enqueued["queue"] == "avatar"
    with auth_db() as db:
        task = db.get(VideoTask, data["id"])
        assert task is not None
        assert task.mode == "avatar_talk"
        assert task.params["avatar_video_asset_id"] == avatar_video_id
        assert task.params["avatar_source_type"] == "video"
        input_asset = db.scalar(
            select(Asset)
            .join(TaskAsset, TaskAsset.asset_id == Asset.id)
            .where(TaskAsset.video_task_id == task.id, TaskAsset.role == "input_avatar")
        )
        assert input_asset is not None
        assert input_asset.id == avatar_video_id
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved > 5
        reserved = db.query(UsageRecord).filter_by(video_task_id=data["id"]).one()
        assert reserved.capability == "avatar"
        assert reserved.model == "jimeng_realman_avatar_picture_omni_v15"


def test_avatar_talk_video_source_accepts_change_lips_optional_params(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 10000
        voice, avatar_video = _seed_voice_and_avatar_video(db, auth_context["tenant_id"])
        voice_id = voice.id
        avatar_video_id = avatar_video.id

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
            "script": "short avatar video source script",
            "voice_id": voice_id,
            "avatar_video_asset_id": avatar_video_id,
            "video_mode": "avatar_talk",
            "align_audio_reverse": True,
            "templ_start_seconds": 1.5,
            "open_sr": True,
            "separate_vocal": False,
            "open_scenedet": True,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    with auth_db() as db:
        task = db.get(VideoTask, resp.json()["data"]["id"])
        assert task is not None
        assert task.params["align_audio_reverse"] is True
        assert task.params["templ_start_seconds"] == 1.5
        assert task.params["open_sr"] is True
        assert task.params["separate_vocal"] is False
        assert task.params["open_scenedet"] is True


def test_avatar_talk_video_source_rejects_invalid_asset_matrix(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 10000
        voice, valid_video = _seed_voice_and_avatar_video(db, auth_context["tenant_id"])
        from app.db.models import Tenant

        db.add(Tenant(id="other-tenant", slug="other-tenant", name="Other Tenant"))
        db.flush()
        too_long = Asset(
            tenant_id=auth_context["tenant_id"],
            type="video",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/too-long.mp4",
            mime_type="video/mp4",
            size_bytes=8_000_000,
            duration_ms=10_001,
            width=960,
            height=960,
            status="ready",
            metadata_={"purpose": "avatar_source", "video_codec": "h264", "audio_codec": "aac"},
        )
        wrong_type = Asset(
            tenant_id=auth_context["tenant_id"],
            type="avatar_image",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/avatar.png",
            mime_type="image/png",
            status="ready",
        )
        other_tenant_video = Asset(
            tenant_id="other-tenant",
            type="video",
            source="upload",
            storage_key="tenants/other-tenant/uploads/avatar-source.mp4",
            mime_type="video/mp4",
            size_bytes=8_000_000,
            duration_ms=9_500,
            width=960,
            height=960,
            status="ready",
            metadata_={"purpose": "avatar_source", "video_codec": "h264", "audio_codec": "aac"},
        )
        bad_codec = Asset(
            tenant_id=auth_context["tenant_id"],
            type="video",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/bad-codec.mp4",
            mime_type="video/mp4",
            size_bytes=8_000_000,
            duration_ms=9_500,
            width=960,
            height=960,
            status="ready",
            metadata_={"purpose": "avatar_source", "video_codec": "hevc", "audio_codec": "aac"},
        )
        bad_resolution = Asset(
            tenant_id=auth_context["tenant_id"],
            type="video",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/bad-resolution.mp4",
            mime_type="video/mp4",
            size_bytes=8_000_000,
            duration_ms=9_500,
            width=320,
            height=320,
            status="ready",
            metadata_={"purpose": "avatar_source", "video_codec": "h264", "audio_codec": "aac"},
        )
        too_large = Asset(
            tenant_id=auth_context["tenant_id"],
            type="video",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/too-large.mp4",
            mime_type="video/mp4",
            size_bytes=201 * 1024 * 1024,
            duration_ms=9_500,
            width=960,
            height=960,
            status="ready",
            metadata_={"purpose": "avatar_source", "video_codec": "h264", "audio_codec": "aac"},
        )
        db.add_all(
            [
                too_long,
                wrong_type,
                other_tenant_video,
                bad_codec,
                bad_resolution,
                too_large,
            ]
        )
        db.commit()
        voice_id = voice.id
        cases = [
            (too_long.id, 422),
            (wrong_type.id, 404),
            (other_tenant_video.id, 404),
            (bad_codec.id, 422),
            (bad_resolution.id, 422),
            (too_large.id, 413),
            (valid_video.id, 202),
        ]

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _FakeTask())
    client = TestClient(app)
    for asset_id, expected_status in cases:
        resp = client.post(
            "/api/v1/videos",
            json={
                "topic": "cashmere coat",
                "script": "short avatar video source script",
                "voice_id": voice_id,
                "avatar_video_asset_id": asset_id,
                "video_mode": "avatar_talk",
            },
            headers=auth_context["headers"],
        )
        assert resp.status_code == expected_status


def test_avatar_talk_video_source_checks_storage_size_when_metadata_size_missing(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 10000
        voice, _valid_video = _seed_voice_and_avatar_video(db, auth_context["tenant_id"])
        from app.db.models import Asset

        storage_key = f"tenants/{auth_context['tenant_id']}/uploads/missing-size.mp4"
        missing_size = Asset(
            tenant_id=auth_context["tenant_id"],
            type="video",
            source="upload",
            storage_key=storage_key,
            mime_type="video/mp4",
            size_bytes=None,
            duration_ms=9_500,
            width=960,
            height=960,
            status="ready",
            metadata_={"purpose": "avatar_source", "video_codec": "h264", "audio_codec": "aac"},
        )
        db.add(missing_size)
        db.commit()
        voice_id = voice.id
        asset_id = missing_size.id

    from app.api.v1.routes import videos as videos_route

    storage = _FakeStorage()
    storage.saved[storage_key] = (b"too-large", "video/mp4")
    monkeypatch.setattr(videos_route, "_AVATAR_VIDEO_SOURCE_MAX_BYTES", 4)
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/videos",
            json={
                "topic": "cashmere coat",
                "script": "short avatar video source script",
                "voice_id": voice_id,
                "avatar_video_asset_id": asset_id,
                "video_mode": "avatar_talk",
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 413


def test_seedance_i2v_order_routes_before_avatar_when_voice_is_present(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 10000
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

    scene_prompt = "hero product on a bright kitchen counter" + ("，细节镜头" * 900)
    negative_prompt = "No warped product, no duplicated parts, no flicker."
    storage = _tenant_storage(
        auth_context["tenant_id"],
        "uploads/product-front.png",
        "uploads/product-side.webp",
    )
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/videos",
            json={
                "topic": "premium scarf product benefits",
                "video_mode": "seedance_i2v",
                "product_image_keys": [
                    "uploads/product-front.png",
                    "uploads/product-side.webp",
                ],
                "voice_id": voice_id,
                "scene_prompt": scene_prompt,
                "negative_prompt": negative_prompt,
                "duration_sec": 30,
                "resolution": "1080p",
                "speed": 1.0,
                "aspect_ratio": "9:16",
                "subtitle_enabled": True,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert enqueued["task_id"] == data["id"]
    assert enqueued["queue"] == "video"
    assert enqueued["args"][0]["video_mode"] == "seedance_i2v"
    assert enqueued["args"][0]["product_image_keys"] == [
        "uploads/product-front.png",
        "uploads/product-side.webp",
    ]
    assert enqueued["args"][0]["scene_prompt"] == scene_prompt
    assert enqueued["args"][0]["negative_prompt"] == negative_prompt
    assert enqueued["args"][0]["duration_sec"] == 30
    assert enqueued["args"][0]["resolution"] == "1080p"
    with auth_db() as db:
        task = db.get(VideoTask, data["id"])
        assert task is not None
        assert task.mode == "seedance_i2v"
        assert task.video_mode == "seedance_i2v"
        assert task.voice_id == voice_id
        assert task.params["product_image_keys"] == [
            "uploads/product-front.png",
            "uploads/product-side.webp",
        ]
        assert "image_key" not in task.params
        assert task.params["scene_prompt"] == scene_prompt
        assert task.params["negative_prompt"] == negative_prompt
        assert task.params["duration_sec"] == 30
        assert task.params["resolution"] == "1080p"
        assert task.duration_sec == 30
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == 8408
        reserved = db.query(UsageRecord).filter_by(video_task_id=data["id"]).one()
        assert reserved.status == "reserved"
        assert reserved.capability == "video"
        assert reserved.provider == "apimart"
        assert reserved.model == "doubao-seedance-2.0"
        assert reserved.quantity == Decimal("30")
        assert reserved.credits == Decimal("8403.00")


def test_seedance_i2v_rejects_missing_product_image_without_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 10000
        voice, _avatar = _seed_voice_and_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved
        used_before = subscription.quota_credits_used
        task_count_before = db.scalar(select(func.count()).select_from(VideoTask))
        usage_count_before = db.scalar(select(func.count()).select_from(UsageRecord))

    enqueued = False

    class _FakeI2VTask:
        def apply_async(self, *, args, task_id, queue=None):
            nonlocal enqueued
            enqueued = True
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_seedance_i2v_task", _FakeI2VTask())
    missing_key = f"tenants/{auth_context['tenant_id']}/uploads/product.png"
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/videos",
            json={
                "video_mode": "seedance_i2v",
                "product_image_keys": ["uploads/product.png"],
                "voice_id": voice_id,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PRODUCT_IMAGE_NOT_FOUND"
    assert storage.existence_checks == [missing_key]
    assert enqueued is False
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == reserved_before
        assert subscription.quota_credits_used == used_before
        assert db.scalar(select(func.count()).select_from(VideoTask)) == task_count_before
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == usage_count_before


def test_seedance_i2v_rejects_cross_tenant_product_image_without_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 10000
        voice, _avatar = _seed_voice_and_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved
        used_before = subscription.quota_credits_used
        task_count_before = db.scalar(select(func.count()).select_from(VideoTask))
        usage_count_before = db.scalar(select(func.count()).select_from(UsageRecord))

    enqueued = False

    class _FakeI2VTask:
        def apply_async(self, *, args, task_id, queue=None):
            nonlocal enqueued
            enqueued = True
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_seedance_i2v_task", _FakeI2VTask())
    foreign_key = "tenants/other-tenant/uploads/product.png"
    monkeypatch.setattr(
        videos_route,
        "tenant_storage_key",
        lambda _tenant_id, _image_key: foreign_key,
    )
    storage = _FakeStorage(existing_keys={foreign_key})
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/videos",
            json={
                "video_mode": "seedance_i2v",
                "product_image_keys": ["uploads/product.png"],
                "voice_id": voice_id,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PRODUCT_IMAGE_NOT_FOUND"
    assert storage.existence_checks == []
    assert enqueued is False
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == reserved_before
        assert subscription.quota_credits_used == used_before
        assert db.scalar(select(func.count()).select_from(VideoTask)) == task_count_before
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == usage_count_before


def test_photo_order_routes_before_avatar_when_voice_is_present(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 200
        voice, avatar = _seed_voice_and_avatar(db, auth_context["tenant_id"])
        voice_id = voice.id
        avatar_id = avatar.id
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved

    enqueued: dict[str, object] = {}

    class _FakeImageTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.update({"args": args, "task_id": task_id, "queue": queue})
            return type("Result", (), {"status": "PENDING"})()

    class _UnexpectedAvatarTask:
        def apply_async(self, **_kwargs):  # pragma: no cover
            raise AssertionError("photo must route before avatar_talk")

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_image_task", _FakeImageTask())
    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _UnexpectedAvatarTask())

    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={
            "topic": "luxury leather handbag product photo",
            "video_mode": "photo",
            "image_key": "uploads/product.png",
            "aspect_ratio": "21:9",
            "image_size": "1024x1536",
            "image_quality": "medium",
            "purpose": "cover",
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert enqueued["task_id"] == data["id"]
    assert enqueued["queue"] == "image"
    assert enqueued["args"][0]["video_mode"] == "photo"
    assert enqueued["args"][0]["image_key"] == "uploads/product.png"
    assert enqueued["args"][0]["aspect_ratio"] == "21:9"
    assert enqueued["args"][0]["requested_aspect_ratio"] == "21:9"
    assert enqueued["args"][0]["purpose"] == "cover"
    assert "image_size" not in enqueued["args"][0]
    assert "image_quality" not in enqueued["args"][0]
    with auth_db() as db:
        task = db.get(VideoTask, data["id"])
        assert task is not None
        assert task.mode == "photo"
        assert task.video_mode == "photo"
        assert task.aspect_ratio == "21:9"
        assert task.params["image_key"] == "uploads/product.png"
        assert task.params["aspect_ratio"] == "21:9"
        assert task.params["requested_aspect_ratio"] == "21:9"
        assert task.params["purpose"] == "cover"
        assert "image_size" not in task.params
        assert "image_quality" not in task.params
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved - reserved_before == 10
        reserved = db.query(UsageRecord).filter_by(video_task_id=data["id"]).one()
        assert reserved.status == "reserved"
        assert reserved.capability == "image"
        assert reserved.provider == "apimart"
        assert reserved.quantity == Decimal("1.000")
        assert reserved.credits == Decimal("10.00")


def test_photo_accepts_six_reference_images_without_changing_flat_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 200
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved

    enqueued: dict[str, object] = {}

    class _FakeImageTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.update({"args": args, "task_id": task_id, "queue": queue})
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_image_task", _FakeImageTask())
    image_keys = [f"uploads/reference-{index}.png" for index in range(6)]

    response = TestClient(app).post(
        "/api/v1/videos",
        json={
            "topic": "combine all references into one premium product image",
            "video_mode": "photo",
            "image_keys": image_keys,
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 202
    task_id = response.json()["data"]["id"]
    assert enqueued["task_id"] == task_id
    assert enqueued["queue"] == "image"
    assert enqueued["args"][0]["image_keys"] == image_keys
    assert enqueued["args"][0]["image_resolution"] == "1k"
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        subscription = db.get(Subscription, subscription_id)
        usage = db.query(UsageRecord).filter_by(video_task_id=task_id).one()

    assert task.params["image_keys"] == image_keys
    assert task.params["image_resolution"] == "1k"
    assert subscription.quota_credits_reserved - reserved_before == 10
    assert usage.quantity == Decimal("1.000")
    assert usage.credits == Decimal("10.00")


def test_photo_rejects_seven_reference_images_with_friendly_message(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 200
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved
        task_count_before = db.scalar(select(func.count()).select_from(VideoTask))
        usage_count_before = db.scalar(select(func.count()).select_from(UsageRecord))

    enqueued = False

    class _FakeImageTask:
        def apply_async(self, **_kwargs):
            nonlocal enqueued
            enqueued = True
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_image_task", _FakeImageTask())

    response = TestClient(app).post(
        "/api/v1/videos",
        json={
            "topic": "too many product references",
            "video_mode": "photo",
            "image_keys": [f"uploads/reference-{index}.png" for index in range(7)],
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert "参考图最多支持 6 张" in response.text
    assert enqueued is False
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == reserved_before
        assert db.scalar(select(func.count()).select_from(VideoTask)) == task_count_before
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == usage_count_before


def test_photo_propagates_layered_prompt_and_strength_controls(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 200
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved

    enqueued: dict[str, object] = {}

    class _FakeImageTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.update({"args": args, "task_id": task_id, "queue": queue})
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_image_task", _FakeImageTask())
    controls = {
        "master_prompt": "Warm editorial campaign styling.",
        "master_negative_prompt": "watermarks and illegible text",
        "negative_prompt": "duplicate handles and warped edges",
        "similarity_strength": 20,
        "creativity_strength": 40,
        "subject_strength": 60,
    }

    response = TestClient(app).post(
        "/api/v1/videos",
        json={
            "topic": "Reimagine the product on a sculptural pedestal.",
            "video_mode": "photo",
            **controls,
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 202
    task_id = response.json()["data"]["id"]
    for field_name, value in controls.items():
        assert enqueued["args"][0][field_name] == value
    assert "background_strength" not in enqueued["args"][0]
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        subscription = db.get(Subscription, subscription_id)
        usage = db.query(UsageRecord).filter_by(video_task_id=task_id).one()
    for field_name, value in controls.items():
        assert task.params[field_name] == value
    assert "background_strength" not in task.params
    assert subscription.quota_credits_reserved - reserved_before == 10
    assert usage.quantity == Decimal("1.000")
    assert usage.credits == Decimal("10.00")


def test_photo_rejects_removed_background_strength(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 200
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved
        task_count_before = db.scalar(select(func.count()).select_from(VideoTask))
        usage_count_before = db.scalar(select(func.count()).select_from(UsageRecord))

    enqueued = False

    class _FakeImageTask:
        def apply_async(self, **_kwargs):
            nonlocal enqueued
            enqueued = True
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_image_task", _FakeImageTask())
    response = TestClient(app).post(
        "/api/v1/videos",
        json={
            "topic": "premium product image",
            "video_mode": "photo",
            "background_strength": 80,
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert any(
        detail["type"] == "extra_forbidden"
        and detail["loc"] == ["body", "background_strength"]
        for detail in error["details"]
    )
    assert enqueued is False
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == reserved_before
        assert db.scalar(select(func.count()).select_from(VideoTask)) == task_count_before
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == usage_count_before


@pytest.mark.parametrize("image_resolution", ["1k", "2k", "4k"])
def test_photo_propagates_requested_image_resolution(
    image_resolution,
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        subscription.quota_credits_total = 200
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved

    enqueued: dict[str, object] = {}

    class _FakeImageTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.update({"args": args, "task_id": task_id, "queue": queue})
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_image_task", _FakeImageTask())
    response = TestClient(app).post(
        "/api/v1/videos",
        json={
            "topic": "premium product image",
            "video_mode": "photo",
            "image_resolution": image_resolution,
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 202
    task_id = response.json()["data"]["id"]
    assert enqueued["args"][0]["image_resolution"] == image_resolution
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        subscription = db.get(Subscription, subscription_id)
        usage = db.query(UsageRecord).filter_by(video_task_id=task_id).one()
    assert task.params["image_resolution"] == image_resolution
    assert subscription.quota_credits_reserved - reserved_before == 10
    assert usage.quantity == Decimal("1.000")
    assert usage.credits == Decimal("10.00")


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
        "product_image_keys": ["uploads/product.png"],
        "voice_id": voice_id,
        "duration_sec": 30,
    }

    storage = _tenant_storage(auth_context["tenant_id"], "uploads/product.png")
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
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
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert estimate_resp.status_code == 200
    assert estimate_resp.json()["data"] == {
        "estimated_credits": 150,
        "unit": "credits",
        "note": "Estimated reservation; final settlement uses actual generated duration.",
    }
    assert create_resp.status_code == 202
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved - reserved_before == 150
        reserved = db.query(UsageRecord).filter_by(
            video_task_id=create_resp.json()["data"]["id"]
        ).one()
        assert reserved.quantity == Decimal("30")
        assert reserved.credits == Decimal("149.25")


def test_video_estimate_photo_matches_flat_tenant_image_rate(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = _seed_billing(db, auth_context["tenant_id"])
        db.add(
            CreditRate(
                tenant_id=auth_context["tenant_id"],
                capability="image",
                unit="image",
                credits_per_unit=Decimal("2.0000"),
            )
        )
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved
        db.commit()

    class _FakeImageTask:
        def apply_async(self, *, args, task_id, queue=None):
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_image_task", _FakeImageTask())
    client = TestClient(app)
    payload = {
        "topic": "premium mug on a clean studio table",
        "video_mode": "photo",
        "image_quality": "medium",
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
        "estimated_credits": 2,
        "unit": "credits",
        "note": "Estimated reservation; final settlement uses actual generated duration.",
    }
    assert create_resp.status_code == 202
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved - reserved_before == 2
        reserved = db.query(UsageRecord).filter_by(
            video_task_id=create_resp.json()["data"]["id"]
        ).one()
        assert reserved.capability == "image"
        assert reserved.unit == "image"
        assert reserved.quantity == Decimal("1.000")
        assert reserved.credits == Decimal("2.00")


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
                    unit="character",
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
    assert data["estimated_credits"] == 16
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
            "product_image_keys": ["uploads/product.png"],
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
            "product_image_keys": ["uploads/product.png"],
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422


@pytest.mark.parametrize(
    "aspect_ratio",
    ["1:1", "4:3", "3:2", "16:9", "21:9", "3:4", "2:3", "9:16", "auto"],
)
def test_photo_schema_accepts_frozen_aspect_ratios(aspect_ratio: str) -> None:
    request = VideoGenerateRequest.model_validate(
        {
            "topic": "premium mug product image",
            "video_mode": "photo",
            "aspect_ratio": aspect_ratio,
        }
    )

    assert request.aspect_ratio == aspect_ratio


@pytest.mark.parametrize(
    ("legacy_size", "expected_ratio"),
    [
        ("1024x1024", "1:1"),
        ("1536x1024", "3:2"),
        ("1024x1536", "2:3"),
        ("2048x2048", "1:1"),
        ("unknown-legacy-size-" + "x" * 64, "1:1"),
    ],
)
def test_photo_schema_translates_legacy_size_only_when_ratio_is_missing(
    legacy_size: str,
    expected_ratio: str,
) -> None:
    translated = VideoGenerateRequest.model_validate(
        {
            "topic": "premium mug product image",
            "video_mode": "photo",
            "image_size": legacy_size,
            "image_quality": "legacy-ui-quality-" + "x" * 64,
        }
    )
    explicit = VideoGenerateRequest.model_validate(
        {
            "topic": "premium mug product image",
            "video_mode": "photo",
            "aspect_ratio": "21:9",
            "image_size": legacy_size,
        }
    )

    assert translated.aspect_ratio == expected_ratio
    assert translated.image_quality == "legacy-ui-quality-" + "x" * 64
    assert explicit.aspect_ratio == "21:9"


def test_photo_schema_defaults_to_square_without_changing_video_defaults() -> None:
    photo = VideoGenerateRequest.model_validate(
        {"topic": "premium mug product image", "video_mode": "photo"}
    )
    video = VideoGenerateRequest.model_validate({"topic": "vertical product video"})

    assert photo.aspect_ratio == "1:1"
    assert photo.image_resolution == "1k"
    assert video.aspect_ratio == "9:16"
    assert photo.voice_id is None
    assert photo.avatar_asset_id is None
    assert photo.apply_visible_label is False


@pytest.mark.parametrize("aspect_ratio", ["4:3", "3:2", "21:9", "3:4", "2:3", "auto"])
def test_non_photo_schema_rejects_image_only_aspect_ratios(aspect_ratio: str) -> None:
    with pytest.raises(ValidationError):
        VideoGenerateRequest.model_validate(
            {"topic": "vertical product video", "aspect_ratio": aspect_ratio}
        )


def test_photo_schema_still_rejects_blank_topic() -> None:
    with pytest.raises(ValidationError):
        VideoGenerateRequest.model_validate({"topic": "   ", "video_mode": "photo"})


def test_photo_schema_accepts_twenty_thousand_character_prompt_layers() -> None:
    prompt = "图" * 20_000

    request = VideoGenerateRequest.model_validate(
        {
            "topic": prompt,
            "video_mode": "photo",
            "master_prompt": prompt,
            "master_negative_prompt": prompt,
            "negative_prompt": prompt,
        }
    )

    assert request.topic == prompt
    assert request.master_prompt == prompt
    assert request.master_negative_prompt == prompt
    assert request.negative_prompt == prompt


def test_non_photo_topic_keeps_existing_two_thousand_character_limit() -> None:
    with pytest.raises(ValidationError, match="non-photo topic must contain at most 2000"):
        VideoGenerateRequest.model_validate(
            {"topic": "x" * 2001, "video_mode": "static_template"}
        )


def test_photo_negative_prompt_rejects_more_than_twenty_thousand_characters() -> None:
    with pytest.raises(ValidationError, match="photo prompt fields must contain at most 20000"):
        VideoGenerateRequest.model_validate(
            {
                "topic": "premium product image",
                "video_mode": "photo",
                "negative_prompt": "x" * 20_001,
            }
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "similarity_strength",
        "creativity_strength",
        "subject_strength",
    ],
)
def test_photo_strength_contract_uses_ten_percent_steps(field_name: str) -> None:
    for value in range(10, 101, 10):
        request = VideoGenerateRequest.model_validate(
            {
                "topic": "premium product image",
                "video_mode": "photo",
                field_name: value,
            }
        )
        assert getattr(request, field_name) == value

    for invalid_value in (0, 15, 101):
        with pytest.raises(ValidationError):
            VideoGenerateRequest.model_validate(
                {
                    "topic": "premium product image",
                    "video_mode": "photo",
                    field_name: invalid_value,
                }
            )


def test_video_negative_prompt_keeps_existing_unrestricted_contract() -> None:
    negative_prompt = "n" * 20_001

    request = VideoGenerateRequest.model_validate(
        {
            "topic": "premium product video",
            "video_mode": "seedance_i2v",
            "product_image_keys": ["uploads/product.png"],
            "voice_id": "voice-1",
            "negative_prompt": negative_prompt,
        }
    )

    assert request.negative_prompt == negative_prompt


@pytest.mark.parametrize(
    ("video_mode", "mode_fields"),
    [
        ("static_template", {}),
        ("seedance_t2v", {}),
        ("photo", {}),
        ("avatar_talk", {"voice_id": "voice-1", "avatar_asset_id": "avatar-1"}),
    ],
)
def test_non_seedance_video_modes_still_require_nonblank_topic(
    video_mode: str,
    mode_fields: dict,
) -> None:
    for topic in (None, "   "):
        with pytest.raises(ValidationError):
            VideoGenerateRequest.model_validate(
                {"topic": topic, "video_mode": video_mode, **mode_fields}
            )


def test_video_gen_uses_prompt_when_topic_is_omitted() -> None:
    payload = VideoGenerateRequest.model_validate(
        {
            "video_mode": "video_gen",
            "prompt": "cinematic product reveal",
            "duration_sec": 5,
        }
    )

    assert payload.topic == "cinematic product reveal"


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
            "product_image_keys": ["uploads/product.png"],
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
        subscription.quota_credits_total = 30000
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
    storage = _tenant_storage(auth_context["tenant_id"], "uploads/product.png")
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/videos",
            json={
                "topic": "premium scarf product benefits",
                "video_mode": "seedance_i2v",
                "product_image_keys": ["uploads/product.png"],
                "voice_id": voice_id,
                "duration_sec": 999,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 202
    assert enqueued["args"][0]["duration_sec"] == 120
    assert enqueued["args"][0]["resolution"] == "720p"
    with auth_db() as db:
        task = db.get(VideoTask, resp.json()["data"]["id"])
        assert task.duration_sec == 120
        assert task.params["duration_sec"] == 120
        assert task.params["resolution"] == "720p"


def test_video_generate_request_clamps_seedance_duration() -> None:
    low = VideoGenerateRequest(
        topic="x",
        video_mode="seedance_i2v",
        product_image_keys=["uploads/product.png"],
        voice_id="voice",
        duration_sec=3,
    )
    high = VideoGenerateRequest(
        topic="x",
        video_mode="seedance_i2v",
        product_image_keys=["uploads/product.png"],
        voice_id="voice",
        duration_sec=999,
    )

    assert low.duration_sec == 5
    assert high.duration_sec == 120


def test_scene_prompt_request_keeps_optional_clamped_duration_contract() -> None:
    common = {"product_image_keys": ["uploads/product.png"]}

    omitted = ScenePromptRequest(**common)
    low = ScenePromptRequest(**common, duration_sec=3)
    high = ScenePromptRequest(**common, duration_sec=999)

    assert omitted.duration_sec is None
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


def test_video_list_filters_by_mode_and_keeps_pagination(
    auth_context,
    auth_db,
) -> None:
    store = _MemProgressStore()
    storage = _FakeStorage()
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    with auth_db() as db:
        rows = [
            ("history-avatar-a", "avatar_talk", 0),
            ("history-i2v-a", "seedance_i2v", 1),
            ("history-i2v-b", "seedance_i2v", 2),
        ]
        for item_id, mode, minutes in rows:
            db.add(
                VideoTask(
                    id=item_id,
                    tenant_id=auth_context["tenant_id"],
                    created_by_user_id=auth_context["user_id"],
                    mode=mode,
                    video_mode=mode,
                    status="done",
                    progress=100,
                    topic=f"topic-{item_id}",
                    script=f"script-{item_id}",
                    created_at=base_time + timedelta(minutes=minutes),
                )
            )
        db.commit()

    app.dependency_overrides[get_progress_store] = lambda: store
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/videos?mode=seedance_i2v&limit=1&offset=1",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_progress_store, None)
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 2
    assert [item["id"] for item in data["items"]] == ["history-i2v-a"]
    assert all(item["mode"] == "seedance_i2v" for item in data["items"])


def test_seedance_video_history_exposes_scene_prompt_from_params_not_topic(
    auth_context,
    auth_db,
) -> None:
    store = _MemProgressStore()
    storage = _FakeStorage()
    with auth_db() as db:
        db.add(
            VideoTask(
                id="history-i2v-scene-prompt",
                tenant_id=auth_context["tenant_id"],
                created_by_user_id=auth_context["user_id"],
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="done",
                progress=100,
                topic="portable thermos product benefits",
                params={"scene_prompt": "warm tabletop close-up with drifting steam"},
            )
        )
        db.commit()

    app.dependency_overrides[get_progress_store] = lambda: store
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).get(
            "/api/v1/videos?mode=seedance_i2v",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_progress_store, None)
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    item = resp.json()["data"]["items"][0]
    assert item["topic"] == "portable thermos product benefits"
    assert item["prompt"] == "portable thermos product benefits"
    assert item["scene_prompt"] == "warm tabletop close-up with drifting steam"
    assert item["scene_prompt"] != item["topic"]


def test_seedance_video_history_missing_scene_prompt_is_null_not_topic(
    auth_context,
    auth_db,
) -> None:
    store = _MemProgressStore()
    storage = _FakeStorage()
    with auth_db() as db:
        db.add(
            VideoTask(
                id="history-i2v-missing-scene",
                tenant_id=auth_context["tenant_id"],
                created_by_user_id=auth_context["user_id"],
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="done",
                progress=100,
                topic="ceramic teapot product benefits",
                params={},
            )
        )
        db.commit()

    app.dependency_overrides[get_progress_store] = lambda: store
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).get(
            "/api/v1/videos?mode=seedance_i2v",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_progress_store, None)
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    item = resp.json()["data"]["items"][0]
    assert item["topic"] == "ceramic teapot product benefits"
    assert item["prompt"] == "ceramic teapot product benefits"
    assert item["scene_prompt"] is None


def test_video_read_openapi_marks_legacy_prompt_deprecated() -> None:
    resp = TestClient(app).get("/openapi.json")

    assert resp.status_code == 200
    schema = resp.json()["components"]["schemas"]["VideoRead"]
    assert schema["properties"]["prompt"]["deprecated"] is True
    assert "prompt" in schema["required"]


def test_video_openapi_documents_optional_one_to_nine_product_images() -> None:
    resp = TestClient(app).get("/openapi.json")

    assert resp.status_code == 200
    schema = resp.json()["components"]["schemas"]["VideoGenerateRequest"]
    product_images = schema["properties"]["product_image_keys"]
    assert product_images["minItems"] == 1
    assert product_images["maxItems"] == 9
    assert "product_image_keys" not in schema.get("required", [])


def test_video_openapi_documents_photo_optimization_contract() -> None:
    resp = TestClient(app).get("/openapi.json")

    assert resp.status_code == 200
    schema = resp.json()["components"]["schemas"]["VideoGenerateRequest"]
    properties = schema["properties"]
    required = schema.get("required", [])

    assert "image_key" in properties
    assert "image_key" not in required
    assert properties["image_keys"]["minItems"] == 1
    assert properties["image_keys"]["maxItems"] == 6
    assert "image_keys" not in required
    image_resolution = properties["image_resolution"]
    assert image_resolution["anyOf"][0] == {
        "type": "string",
        "enum": ["1k", "2k", "4k"],
    }
    assert image_resolution["default"] == "1k"
    assert "image_resolution" not in required
    assert "background_strength" not in properties
    for field_name in (
        "similarity_strength",
        "creativity_strength",
        "subject_strength",
    ):
        integer_schema = properties[field_name]["anyOf"][0]
        assert integer_schema == {
            "type": "integer",
            "maximum": 100,
            "minimum": 10,
            "multipleOf": 10,
        }
        assert field_name not in required
    for field_name in ("topic", "master_prompt", "master_negative_prompt"):
        assert properties[field_name]["anyOf"][0]["maxLength"] == 20_000
    negative_description = properties["negative_prompt"]["description"]
    assert "photo" in negative_description
    assert "soft" in negative_description
    assert "20,000" in negative_description


def test_scripts_openapi_documents_seedance_length_tiers() -> None:
    resp = TestClient(app).get("/openapi.json")

    assert resp.status_code == 200
    schema = resp.json()["components"]["schemas"]["ScriptGenerateRequest"]
    length_tier = schema["properties"]["length_tier"]
    assert length_tier["enum"] == ["short", "medium", "long"]
    assert length_tier["default"] == "medium"
    assert "seedance_i2v" in length_tier["description"]


def test_scene_prompt_request_openapi_requires_one_to_nine_product_images() -> None:
    resp = TestClient(app).get("/openapi.json")

    assert resp.status_code == 200
    schema = resp.json()["components"]["schemas"]["ScenePromptRequest"]
    product_images = schema["properties"]["product_image_keys"]
    duration = schema["properties"]["duration_sec"]
    assert "product_image_keys" in schema["required"]
    assert "duration_sec" not in schema["required"]
    assert product_images["minItems"] == 1
    assert product_images["maxItems"] == 9
    assert duration["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert schema["additionalProperties"] is False


def test_scene_prompt_response_openapi_requires_unbounded_prompts() -> None:
    resp = TestClient(app).get("/openapi.json")

    assert resp.status_code == 200
    schema = resp.json()["components"]["schemas"]["ScenePromptResponse"]
    assert set(schema["required"]) == {"scene_prompt", "negative_prompt"}
    assert "maxLength" not in schema["properties"]["scene_prompt"]
    assert "maxLength" not in schema["properties"]["negative_prompt"]


def test_video_generate_openapi_documents_unbounded_scene_prompts() -> None:
    resp = TestClient(app).get("/openapi.json")

    assert resp.status_code == 200
    schema = resp.json()["components"]["schemas"]["VideoGenerateRequest"]
    assert "maxLength" not in schema["properties"]["scene_prompt"]["anyOf"][0]
    assert "maxLength" not in schema["properties"]["negative_prompt"]["anyOf"][0]


def test_video_list_filters_photo_and_returns_image_urls(
    auth_context,
    auth_db,
) -> None:
    store = _MemProgressStore()
    storage = _FakeStorage()
    with auth_db() as db:
        db.add_all(
            [
                VideoTask(
                    id="history-photo-a",
                    tenant_id=auth_context["tenant_id"],
                    created_by_user_id=auth_context["user_id"],
                    mode="photo",
                    video_mode="photo",
                    status="done",
                    progress=100,
                    topic="premium mug",
                    aspect_ratio="auto",
                    params={
                        "image_resolution": "4k",
                        "requested_aspect_ratio": "auto",
                        "resolved_aspect_ratio": "16:9",
                        "resolved_size": "16:9",
                        "actual_aspect_ratio": "16:9",
                        "actual_width": 160,
                        "actual_height": 90,
                        "actual_size": "160x90",
                    },
                    storage_key=f"tenants/{auth_context['tenant_id']}/photos/history-photo-a/output.png",
                    content_type="image/png",
                ),
                VideoTask(
                    id="history-avatar-b",
                    tenant_id=auth_context["tenant_id"],
                    created_by_user_id=auth_context["user_id"],
                    mode="avatar_talk",
                    video_mode="avatar_talk",
                    status="done",
                    progress=100,
                    topic="avatar",
                ),
            ]
        )
        db.commit()

    app.dependency_overrides[get_progress_store] = lambda: store
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/videos?mode=photo",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_progress_store, None)
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    item = data["items"][0]
    assert item["id"] == "history-photo-a"
    assert item["mode"] == "photo"
    assert item["aspect_ratio"] == "auto"
    assert item["image_resolution"] == "4k"
    assert item["requested_aspect_ratio"] == "auto"
    assert item["resolved_aspect_ratio"] == "16:9"
    assert item["resolved_size"] == "16:9"
    assert item["actual_aspect_ratio"] == "16:9"
    assert item["actual_width"] == 160
    assert item["actual_height"] == 90
    assert item["actual_size"] == "160x90"
    assert item["apply_visible_label"] is False
    assert item["playback_url"].endswith("/output.png?ttl=3600")
    assert item["download_url"].endswith("/output.png?ttl=3600&download=1")
    assert item["thumbnail_url"] == item["playback_url"]


def test_scene_prompt_endpoint_generates_visual_prompt_without_creating_video(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    payloads: list[dict] = []

    class _FakeLuna:
        async def generate_scene_prompt(self, payload: dict):
            payloads.append(payload)
            return {
                "scene_prompt": "Bright tabletop product video with slow push-in.",
                "negative_prompt": "No warped mug, no extra handles, no text.",
                "provider": "apimart",
                "model": "gpt-5.6-luna",
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
                "credits": Decimal("1.25"),
                "cost_cents": 90,
            }

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(
        videos_route,
        "resolve",
        lambda _db, *, tenant_id, capability: (
            _FakeLuna()
            if capability == "scene_prompt"
            else (_ for _ in ()).throw(AssertionError(f"unexpected capability {capability}"))
        ),
    )
    storage = _tenant_storage(
        auth_context["tenant_id"],
        "uploads/product-front.png",
        "uploads/product-side.webp",
    )
    app.dependency_overrides[get_object_storage] = lambda: storage
    with auth_db() as db:
        before = db.scalar(select(func.count()).select_from(VideoTask))

    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/videos/scene-prompt",
            json={
                "topic": "premium ceramic mug",
                "script": "Show the glaze, comfortable handle, and gift-ready finish.",
                "product_image_keys": [
                    "uploads/product-front.png",
                    "uploads/product-side.webp",
                ],
                "duration_sec": 30,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "scene_prompt": "Bright tabletop product video with slow push-in.",
        "negative_prompt": "No warped mug, no extra handles, no text.",
    }
    assert payloads[0]["video_mode"] == "seedance_i2v"
    assert payloads[0]["target_duration_sec"] == 30
    assert payloads[0]["topic"] == "premium ceramic mug"
    assert payloads[0]["script"] == (
        "Show the glaze, comfortable handle, and gift-ready finish."
    )
    assert payloads[0]["image_urls"] == [
        (
            "https://storage.test/tenants/"
            f"{auth_context['tenant_id']}/uploads/product-front.png"
            f"?ttl={videos_route.settings.engine_s3_presign_ttl}"
        ),
        (
            "https://storage.test/tenants/"
            f"{auth_context['tenant_id']}/uploads/product-side.webp"
            f"?ttl={videos_route.settings.engine_s3_presign_ttl}"
        ),
    ]
    with auth_db() as db:
        after = db.scalar(select(func.count()).select_from(VideoTask))
        usage = db.query(UsageRecord).filter_by(capability="scene_prompt").one()
    assert after == before
    assert usage.provider == "apimart"
    assert usage.model == "gpt-5.6-luna"
    assert usage.unit == "token"
    assert usage.quantity == Decimal("200")
    assert usage.credits == Decimal("0")
    assert usage.cost_cents == 90
    assert usage.status == "settled"


def test_scene_prompt_endpoint_requires_at_least_one_product_image(
    monkeypatch,
    auth_context,
) -> None:
    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(
        videos_route,
        "resolve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not resolve when product images are missing")
        ),
    )

    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos/scene-prompt",
        json={"topic": "premium ceramic mug"},
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422


def test_scene_prompt_endpoint_maps_provider_resolution_failure_to_503(
    monkeypatch,
    auth_context,
) -> None:
    from app.api.v1.routes import videos as videos_route
    from app.providers.base import ProviderResolutionError

    def fail_resolve(*_args, **_kwargs):
        raise ProviderResolutionError("No scene prompt provider configured.")

    monkeypatch.setattr(videos_route, "resolve", fail_resolve)
    app.dependency_overrides[get_object_storage] = lambda: _tenant_storage(
        auth_context["tenant_id"], "uploads/product.png"
    )
    try:
        resp = TestClient(app).post(
            "/api/v1/videos/scene-prompt",
            json={"product_image_keys": ["uploads/product.png"]},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SCENE_PROMPT_PROVIDER_NOT_CONFIGURED"


def test_scene_prompt_endpoint_rejects_missing_product_image_before_provider_resolution(
    monkeypatch,
    auth_context,
) -> None:
    from app.api.v1.routes import videos as videos_route

    provider_resolved = False

    class _FakeLuna:
        async def generate_scene_prompt(self, _payload: dict):
            return {
                "scene_prompt": "Image-grounded product showcase.",
                "negative_prompt": "No product distortion.",
            }

    def resolve_provider(*_args, **_kwargs):
        nonlocal provider_resolved
        provider_resolved = True
        return _FakeLuna()

    first_key = f"tenants/{auth_context['tenant_id']}/uploads/product-front.png"
    missing_key = f"tenants/{auth_context['tenant_id']}/uploads/product-missing.png"
    storage = _FakeStorage(existing_keys={first_key})
    monkeypatch.setattr(videos_route, "resolve", resolve_provider)
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/videos/scene-prompt",
            json={
                "product_image_keys": [
                    "uploads/product-front.png",
                    "uploads/product-missing.png",
                ]
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PRODUCT_IMAGE_NOT_FOUND"
    assert provider_resolved is False
    assert storage.existence_checks == [first_key, missing_key]


def test_scene_prompt_endpoint_rejects_cross_tenant_product_image_before_provider_resolution(
    monkeypatch,
    auth_context,
) -> None:
    from app.api.v1.routes import videos as videos_route

    provider_resolved = False

    def resolve_provider(*_args, **_kwargs):
        nonlocal provider_resolved
        provider_resolved = True
        raise AssertionError("provider must not resolve for a cross-tenant product image")

    foreign_key = "tenants/other-tenant/uploads/product.png"
    monkeypatch.setattr(videos_route, "resolve", resolve_provider)
    monkeypatch.setattr(
        videos_route,
        "tenant_storage_key",
        lambda _tenant_id, _image_key: foreign_key,
    )
    storage = _FakeStorage(existing_keys={foreign_key})
    monkeypatch.setattr(
        videos_route,
        "presign_tenant_storage_key",
        lambda *_args, **_kwargs: "https://storage.test/foreign-product.png",
    )
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/videos/scene-prompt",
            json={"product_image_keys": ["uploads/product.png"]},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PRODUCT_IMAGE_NOT_FOUND"
    assert provider_resolved is False
    assert storage.existence_checks == []


def test_scene_prompt_endpoint_maps_provider_invocation_failure_to_502(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.api.v1.routes import videos as videos_route
    from app.providers.base import ProviderInvocationError

    class _FakeScenePromptProvider:
        async def generate_scene_prompt(self, _payload: dict):
            raise AssertionError("route-level invoke mock should run first")

    class _BillableScenePromptFailure(RuntimeError):
        usage_result = {
            "provider": "apimart",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "cost_cents": 45,
        }

    invoke_timeouts: list[float | None] = []

    async def fail_invoke(*_args, **kwargs):
        invoke_timeouts.append(kwargs["timeout_seconds"])
        try:
            raise _BillableScenePromptFailure("Luna returned invalid JSON twice.")
        except _BillableScenePromptFailure as exc:
            raise ProviderInvocationError("Scene prompt invocation failed.") from exc

    monkeypatch.setattr(
        videos_route,
        "resolve",
        lambda *_args, **_kwargs: _FakeScenePromptProvider(),
    )
    monkeypatch.setattr(videos_route, "invoke", fail_invoke)
    app.dependency_overrides[get_object_storage] = lambda: _tenant_storage(
        auth_context["tenant_id"], "uploads/product.png"
    )
    try:
        resp = TestClient(app).post(
            "/api/v1/videos/scene-prompt",
            json={"product_image_keys": ["uploads/product.png"]},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "SCENE_PROMPT_PROVIDER_FAILED"
    assert invoke_timeouts == [None]
    with auth_db() as db:
        usage = db.query(UsageRecord).filter_by(capability="scene_prompt").one()
    assert usage.quantity == Decimal("150")
    assert usage.credits == Decimal("0")
    assert usage.cost_cents == 45


def test_scene_prompt_endpoint_records_usage_for_incomplete_provider_result(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.api.v1.routes import videos as videos_route

    class _IncompleteScenePromptProvider:
        async def generate_scene_prompt(self, _payload: dict):
            return {
                "scene_prompt": "",
                "negative_prompt": "No distortion.",
                "provider": "apimart",
                "model": "gpt-5.6-luna",
                "prompt_tokens": 90,
                "completion_tokens": 10,
                "total_tokens": 100,
                "cost_cents": 30,
            }

    monkeypatch.setattr(
        videos_route,
        "resolve",
        lambda *_args, **_kwargs: _IncompleteScenePromptProvider(),
    )
    app.dependency_overrides[get_object_storage] = lambda: _tenant_storage(
        auth_context["tenant_id"], "uploads/product.png"
    )
    try:
        resp = TestClient(app).post(
            "/api/v1/videos/scene-prompt",
            json={"product_image_keys": ["uploads/product.png"]},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "SCENE_PROMPT_EMPTY_RESULT"
    with auth_db() as db:
        usage = db.query(UsageRecord).filter_by(capability="scene_prompt").one()
    assert usage.quantity == Decimal("100")
    assert usage.credits == Decimal("0")
    assert usage.cost_cents == 30


def test_scene_prompt_endpoint_allows_images_without_topic_or_script(
    monkeypatch,
    auth_context,
) -> None:
    payloads: list[dict] = []

    class _FakeLuna:
        async def generate_scene_prompt(self, payload: dict):
            payloads.append(payload)
            return {
                "scene_prompt": "Image-grounded product showcase.",
                "negative_prompt": "No product distortion.",
            }

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(
        videos_route,
        "resolve",
        lambda _db, *, tenant_id, capability: _FakeLuna(),
    )
    app.dependency_overrides[get_object_storage] = lambda: _tenant_storage(
        auth_context["tenant_id"], "uploads/product.png"
    )
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/videos/scene-prompt",
            json={"product_image_keys": ["uploads/product.png"]},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    assert payloads[0]["topic"] == ""
    assert payloads[0]["script"] == ""


def test_scene_prompt_endpoint_requires_auth(auth_context) -> None:
    client = TestClient(app)

    resp = client.post(
        "/api/v1/videos/scene-prompt",
        json={
            "topic": "premium ceramic mug",
            "product_image_keys": ["uploads/product.png"],
        },
    )

    assert resp.status_code == 401


@pytest.mark.parametrize("role", [Role.REVIEWER, Role.OPS])
def test_scene_prompt_endpoint_requires_video_create_permission(
    monkeypatch,
    auth_context,
    auth_db,
    role: Role,
) -> None:
    from app.api.v1.routes import videos as videos_route

    provider_resolved = False

    def resolve_provider(*_args, **_kwargs):
        nonlocal provider_resolved
        provider_resolved = True
        raise AssertionError("provider must not resolve without video:create permission")

    with auth_db() as db:
        user = db.get(User, auth_context["user_id"])
        assert user is not None
        user.role = role.value
        db.commit()

    monkeypatch.setattr(videos_route, "resolve", resolve_provider)
    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    try:
        resp = TestClient(app).post(
            "/api/v1/videos/scene-prompt",
            json={"product_image_keys": ["uploads/product.png"]},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
    assert provider_resolved is False


def test_video_schema_rejects_invalid_aspect_ratio_before_db_check() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        VideoGenerateRequest.model_validate({"topic": "bad ratio", "aspect_ratio": "4:3"})


def test_seedance_schema_allows_image_only_product_keys() -> None:
    payload = VideoGenerateRequest.model_validate(
        {
            "video_mode": "seedance_i2v",
            "voice_id": "voice-1",
            "product_image_keys": [
                "uploads/product-a.png",
                "uploads/product-b.webp",
            ],
        }
    )

    assert payload.topic is None
    assert payload.script is None
    assert payload.scene_prompt is None
    assert payload.product_image_keys == [
        "uploads/product-a.png",
        "uploads/product-b.webp",
    ]


@pytest.mark.parametrize(("topic", "expected"), [(None, None), ("   ", "")])
def test_seedance_schema_allows_omitted_or_blank_topic(
    topic: str | None,
    expected: str | None,
) -> None:
    payload = VideoGenerateRequest.model_validate(
        {
            "topic": topic,
            "video_mode": "seedance_i2v",
            "voice_id": "voice-1",
            "product_image_keys": ["uploads/product.png"],
        }
    )

    assert payload.topic == expected


@pytest.mark.parametrize(
    "image_fields",
    [
        {"image_key": "uploads/legacy-product.png"},
        {"product_image_keys": []},
        {"product_image_keys": [f"uploads/product-{index}.png" for index in range(10)]},
        {"product_image_keys": ["../private/product.png"]},
    ],
)
def test_seedance_schema_rejects_legacy_or_invalid_product_images(
    image_fields: dict,
) -> None:
    with pytest.raises(ValidationError):
        VideoGenerateRequest.model_validate(
            {
                "video_mode": "seedance_i2v",
                "voice_id": "voice-1",
                **image_fields,
            }
        )


def test_seedance_schema_accepts_nine_product_images() -> None:
    product_image_keys = [f"uploads/product-{index}.png" for index in range(9)]

    payload = VideoGenerateRequest.model_validate(
        {
            "video_mode": "seedance_i2v",
            "voice_id": "voice-1",
            "product_image_keys": product_image_keys,
        }
    )

    assert payload.product_image_keys == product_image_keys


def test_seedance_schema_allows_unlimited_scene_and_negative_prompts() -> None:
    scene_prompt = "镜" * 5001
    negative_prompt = "避免变形、闪烁和重复主体。" * 500

    payload = VideoGenerateRequest.model_validate(
        {
            "video_mode": "seedance_i2v",
            "voice_id": "voice-1",
            "product_image_keys": ["uploads/product.png"],
            "scene_prompt": scene_prompt,
            "negative_prompt": negative_prompt,
        }
    )

    assert payload.scene_prompt == scene_prompt
    assert payload.negative_prompt == negative_prompt


def test_video_progress_snapshot_accepts_percent_and_legacy_fraction() -> None:
    from app.api.v1.routes import videos as videos_route

    task = VideoTask(progress=5)

    assert videos_route._progress(task, {"progress": 10}) == 10
    assert videos_route._progress(task, {"progress": 85}) == 85
    assert videos_route._progress(task, {"progress": 0.1}) == 10
    assert videos_route._progress(task, {"progress": 200}) == 100
    assert videos_route._sse_progress({"progress": 10}) == 10
    assert videos_route._sse_progress({"progress": 0.1}) == 10


def test_video_sse_payload_preserves_fractional_progress_for_watchdog() -> None:
    from app.api.v1.routes import videos as videos_route

    payload = videos_route._sse_payload(
        "fractional-progress-task",
        {
            "status": "running",
            "progress": 25.125,
            "step": "seedance",
        },
    )

    assert payload["progress"] == 25.125


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
