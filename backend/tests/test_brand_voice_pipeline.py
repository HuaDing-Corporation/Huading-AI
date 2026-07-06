from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_object_storage
from app.db.models import (
    Asset,
    BrandVoice,
    CreditRate,
    ProviderConfig,
    Subscription,
    Tenant,
    UsageRecord,
    VideoTask,
    Voice,
)
from app.main import app
from app.workers import avatar_talk


class _CloneProvider:
    def __init__(self) -> None:
        self.clone_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    async def clone_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.clone_calls.append(dict(payload))
        return {
            "speaker_id": str(payload.get("speaker_id") or "brand-speaker-001"),
            "status": "ready",
            "provider": "doubao-voice-clone",
        }

    async def delete_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.delete_calls.append(dict(payload))
        return {"released": True}


class _CloneFailureProvider:
    async def clone_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise _HttpCloneError()


class _HttpCloneError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("volcengine voice clone rejected request")
        self.response = _HttpErrorResponse()


class _HttpErrorResponse:
    status_code = 400
    text = '{"code":"InvalidAudio","message":"audio duration too short"}'


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append((event, kwargs))


class _Storage:
    bucket = "bucket"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.objects[key] = content
        return f"memory://{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        return f"https://storage.test/{key}"

    def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)


class _Store:
    def update(self, *args, **kwargs) -> None:
        return None


def _passthrough_label(content: bytes, **_kwargs) -> bytes:
    return content


def _active_subscription(db, tenant_id: str) -> Subscription:
    subscription = db.scalar(
        select(Subscription)
        .where(Subscription.tenant_id == tenant_id, Subscription.status == "active")
        .order_by(Subscription.period_end.desc())
    )
    assert subscription is not None
    return subscription


def _seed_brand_voice_billing(
    db,
    tenant_id: str,
    *,
    speaker_ids: tuple[str, ...] = ("S_brand_slot_001",),
) -> str:
    subscription = _active_subscription(db, tenant_id)
    subscription.quota_credits_total = 10000
    subscription.quota_credits_used = 0
    subscription.quota_credits_reserved = 0
    db.add_all(
        [
            CreditRate(
                tenant_id=None,
                capability="voice_clone",
                unit="call",
                credits_per_unit=Decimal("30.0000"),
            ),
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
            ProviderConfig(
                tenant_id=None,
                capability="voice_clone",
                provider="doubao-voice-clone",
                config={"speaker_ids": list(speaker_ids)},
                is_active=True,
            ),
        ]
    )
    db.commit()
    return subscription.id


def _seed_audio_asset(db, tenant_id: str) -> str:
    asset = Asset(
        tenant_id=tenant_id,
        type="audio",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/brand-voice.wav",
        mime_type="audio/wav",
        size_bytes=512_000,
        duration_ms=6_000,
        status="ready",
    )
    db.add(asset)
    db.commit()
    return asset.id


def _seed_avatar_asset(db, tenant_id: str) -> str:
    asset = Asset(
        tenant_id=tenant_id,
        type="avatar_image",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/avatar.png",
        mime_type="image/png",
        status="ready",
    )
    db.add(asset)
    db.commit()
    return asset.id


def test_create_brand_voice_requires_confirmed_consent(auth_context, auth_db, monkeypatch):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    provider = _CloneProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve",
        lambda _db, *, tenant_id, capability: provider,
    )
    with auth_db() as db:
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])

    client = TestClient(app)
    resp = client.post(
        "/api/v1/brand-voices",
        json={
            "name": "Store Voice",
            "source_audio_asset_id": asset_id,
            "consent_confirmed": False,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "BRAND_VOICE_CONSENT_REQUIRED"
    assert provider.clone_calls == []
    with auth_db() as db:
        assert db.scalar(select(BrandVoice)) is None
        assert db.scalar(select(UsageRecord)) is None


def test_create_brand_voice_clones_and_charges_once(auth_context, auth_db, monkeypatch):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    provider = _CloneProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve",
        lambda _db, *, tenant_id, capability: provider,
    )
    with auth_db() as db:
        subscription_id = _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])

    storage = _Storage()
    storage.objects[f"tenants/{auth_context['tenant_id']}/uploads/brand-voice.wav"] = b"WAVDATA"
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        resp = client.post(
            "/api/v1/brand-voices",
            json={
                "name": "Store Voice",
                "source_audio_asset_id": asset_id,
                "consent_confirmed": True,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["status"] == "ready"
    assert data["name"] == "Store Voice"
    assert provider.clone_calls == [
        {
            "tenant_id": auth_context["tenant_id"],
            "brand_voice_id": data["id"],
            "name": "Store Voice",
            "speaker_id": "S_brand_slot_001",
            "source_audio_asset_id": asset_id,
            "source_audio_storage_key": (
                f"tenants/{auth_context['tenant_id']}/uploads/brand-voice.wav"
            ),
            "source_audio_bytes": b"WAVDATA",
            "source_audio_mime_type": "audio/wav",
        }
    ]

    with auth_db() as db:
        brand_voice = db.get(BrandVoice, data["id"])
        assert brand_voice is not None
        assert brand_voice.tenant_id == auth_context["tenant_id"]
        assert brand_voice.source_audio_asset_id == asset_id
        assert brand_voice.speaker_id == "S_brand_slot_001"
        assert brand_voice.status == "ready"
        assert brand_voice.consent_confirmed is True
        assert brand_voice.consent_confirmed_at is not None

        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 30

        usage = db.scalar(select(UsageRecord).where(UsageRecord.capability == "voice_clone"))
        assert usage is not None
        assert usage.video_task_id is None
        assert usage.provider == "doubao-voice-clone"
        assert usage.unit == "call"
        assert usage.quantity == Decimal("1.000")
        assert usage.credits == Decimal("30.00")
        assert usage.status == "settled"
        config = db.scalar(select(ProviderConfig).where(ProviderConfig.capability == "voice_clone"))
        assert config.config["used_speaker_ids"] == {"S_brand_slot_001": data["id"]}


def test_create_brand_voice_logs_clone_http_error_details(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    logger = _Logger()
    monkeypatch.setattr(brand_voice_routes, "logger", logger)
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve",
        lambda _db, *, tenant_id, capability: _CloneFailureProvider(),
    )
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])

    storage = _Storage()
    storage.objects[f"tenants/{auth_context['tenant_id']}/uploads/brand-voice.wav"] = b"WAVDATA"
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        resp = client.post(
            "/api/v1/brand-voices",
            json={
                "name": "Store Voice",
                "source_audio_asset_id": asset_id,
                "consent_confirmed": True,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "VOICE_CLONE_FAILED"
    assert len(logger.warnings) == 1
    event, details = logger.warnings[0]
    assert event == "brand_voice.clone_failed"
    assert details["tenant_id"] == auth_context["tenant_id"]
    assert details["brand_voice_id"]
    assert details["source_audio_asset_id"] == asset_id
    assert details["error"] == "volcengine voice clone rejected request"
    assert details["error_type"] == "_HttpCloneError"
    assert details["http_status_code"] == 400
    assert details["http_response_text"] == (
        '{"code":"InvalidAudio","message":"audio duration too short"}'
    )
    with auth_db() as db:
        config = db.scalar(select(ProviderConfig).where(ProviderConfig.capability == "voice_clone"))
        assert config.config.get("used_speaker_ids", {}) == {}


def test_create_brand_voice_returns_friendly_error_when_speaker_slots_exhausted(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    provider = _CloneProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve",
        lambda _db, *, tenant_id, capability: provider,
    )
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"], speaker_ids=())
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])

    client = TestClient(app)
    resp = client.post(
        "/api/v1/brand-voices",
        json={
            "name": "Store Voice",
            "source_audio_asset_id": asset_id,
            "consent_confirmed": True,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "VOICE_CLONE_SLOT_UNAVAILABLE"
    assert provider.clone_calls == []
    with auth_db() as db:
        assert db.scalar(select(UsageRecord).where(UsageRecord.capability == "voice_clone")) is None


def test_clone_http_error_details_truncates_long_response_text():
    from app.api.v1.routes import brand_voices as brand_voice_routes

    class _LongHttpResponse:
        status_code = 429
        text = "x" * 1200

    class _LongHttpError(Exception):
        response = _LongHttpResponse()

    details = brand_voice_routes._http_error_details(_LongHttpError())

    assert details["http_status_code"] == 429
    assert details["http_response_text"] == f"{'x' * 1000}..."


def test_create_brand_voice_rejects_cross_tenant_source_audio(auth_context, auth_db, monkeypatch):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    provider = _CloneProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve",
        lambda _db, *, tenant_id, capability: provider,
    )
    other_tenant_id = "other-source-audio-tenant"
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        db.add(Tenant(id=other_tenant_id, slug="other-source-audio", name="Other Audio"))
        db.flush()
        other_asset_id = _seed_audio_asset(db, other_tenant_id)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/brand-voices",
        json={
            "name": "Store Voice",
            "source_audio_asset_id": other_asset_id,
            "consent_confirmed": True,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SOURCE_AUDIO_ASSET_NOT_FOUND"
    assert provider.clone_calls == []
    with auth_db() as db:
        assert db.scalar(select(UsageRecord).where(UsageRecord.capability == "voice_clone")) is None


def test_brand_voice_list_and_delete_are_tenant_scoped_404(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    provider = _CloneProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve",
        lambda _db, *, tenant_id, capability: provider,
    )
    other_tenant_id = "other-brand-tenant"
    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug="other-brand", name="Other Brand"))
        own = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Own Voice",
            source_audio_asset_id=_seed_audio_asset(db, auth_context["tenant_id"]),
            provider="doubao-voice-clone",
            speaker_id="own-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        other = BrandVoice(
            tenant_id=other_tenant_id,
            name="Other Voice",
            source_audio_asset_id=None,
            provider="doubao-voice-clone",
            speaker_id="other-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add_all(
            [
                own,
                other,
                ProviderConfig(
                    tenant_id=None,
                    capability="voice_clone",
                    provider="doubao-voice-clone",
                    config={
                        "speaker_ids": ["own-speaker"],
                        "used_speaker_ids": {"own-speaker": "pending"},
                    },
                    is_active=True,
                ),
            ]
        )
        db.commit()
        own_id = own.id
        other_id = other.id
        config = db.scalar(select(ProviderConfig).where(ProviderConfig.capability == "voice_clone"))
        config.config = {
            "speaker_ids": ["own-speaker"],
            "used_speaker_ids": {"own-speaker": own_id},
        }
        db.commit()

    client = TestClient(app)
    list_resp = client.get("/api/v1/brand-voices", headers=auth_context["headers"])
    assert list_resp.status_code == 200
    assert [item["id"] for item in list_resp.json()["data"]["items"]] == [own_id]

    cross_delete = client.delete(
        f"/api/v1/brand-voices/{other_id}",
        headers=auth_context["headers"],
    )
    assert cross_delete.status_code == 404

    own_delete = client.delete(
        f"/api/v1/brand-voices/{own_id}",
        headers=auth_context["headers"],
    )
    assert own_delete.status_code == 200
    assert own_delete.json()["data"] == {"deleted": True}
    assert provider.delete_calls == [
        {
            "tenant_id": auth_context["tenant_id"],
            "brand_voice_id": own_id,
            "speaker_id": "own-speaker",
        }
    ]
    with auth_db() as db:
        assert db.get(BrandVoice, own_id).deleted_at is not None
        assert db.get(BrandVoice, other_id).deleted_at is None
        config = db.scalar(select(ProviderConfig).where(ProviderConfig.capability == "voice_clone"))
        assert config.config["used_speaker_ids"] == {}


def test_brand_voice_get_and_patch_are_tenant_scoped(auth_context, auth_db):
    other_tenant_id = "other-brand-crud-tenant"
    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug="other-brand-crud", name="Other CRUD"))
        db.flush()
        own = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Own Voice",
            provider="doubao-voice-clone",
            speaker_id="own-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        other = BrandVoice(
            tenant_id=other_tenant_id,
            name="Other Voice",
            provider="doubao-voice-clone",
            speaker_id="other-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add_all([own, other])
        db.commit()
        own_id = own.id
        other_id = other.id

    client = TestClient(app)
    get_resp = client.get(f"/api/v1/brand-voices/{own_id}", headers=auth_context["headers"])
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["name"] == "Own Voice"

    patch_resp = client.patch(
        f"/api/v1/brand-voices/{own_id}",
        json={"name": "Renamed Voice"},
        headers=auth_context["headers"],
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["name"] == "Renamed Voice"

    cross_get = client.get(
        f"/api/v1/brand-voices/{other_id}",
        headers=auth_context["headers"],
    )
    cross_patch = client.patch(
        f"/api/v1/brand-voices/{other_id}",
        json={"name": "Should Not Rename"},
        headers=auth_context["headers"],
    )
    assert cross_get.status_code == 404
    assert cross_patch.status_code == 404
    with auth_db() as db:
        assert db.get(BrandVoice, own_id).name == "Renamed Voice"
        assert db.get(BrandVoice, other_id).name == "Other Voice"


def test_voices_catalog_merges_ready_brand_voices_only(auth_context, auth_db):
    other_tenant_id = "other-catalog-tenant"
    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug="other-catalog", name="Other Catalog"))
        db.flush()
        preset = Voice(
            provider="edge-tts",
            voice_code="zh-CN-XiaoxiaoNeural",
            display_name="Xiaoxiao",
            gender="female",
            language="zh-CN",
            is_active=True,
        )
        ready_brand = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Ready Brand",
            provider="doubao-voice-clone",
            speaker_id="ready-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        failed_brand = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Failed Brand",
            provider="doubao-voice-clone",
            speaker_id=None,
            status="failed",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        other_brand = BrandVoice(
            tenant_id=other_tenant_id,
            name="Other Brand",
            provider="doubao-voice-clone",
            speaker_id="other-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add_all([preset, ready_brand, failed_brand, other_brand])
        db.commit()
        preset_id = preset.id
        ready_brand_id = ready_brand.id

    client = TestClient(app)
    resp = client.get("/api/v1/voices", headers=auth_context["headers"])

    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    preset_item = next(item for item in items if item["id"] == preset_id)
    assert preset_item["source"] == "preset"
    brand_items = [item for item in items if item["source"] == "brand_voice"]
    assert brand_items == [
        {
            "id": ready_brand_id,
            "provider": "doubao-voice-clone",
            "voice_code": ready_brand_id,
            "display_name": "Ready Brand",
            "gender": "neutral",
            "language": "zh-CN",
            "sample_url": None,
            "source": "brand_voice",
        }
    ]
    assert all("speaker_id" not in item for item in items)


def test_avatar_talk_accepts_brand_voice_and_worker_uses_speaker_id(
    auth_context,
    auth_db,
    monkeypatch,
    tmp_path: Path,
):
    captured_tts_payloads: list[dict[str, Any]] = []
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        avatar_id = _seed_avatar_asset(db, auth_context["tenant_id"])
        brand_voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Oral Brand",
            provider="doubao-voice-clone",
            speaker_id="oral-brand-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add(brand_voice)
        db.commit()
        brand_voice_id = brand_voice.id

    monkeypatch.setattr(
        "app.api.v1.routes.videos.generate_avatar_talk_task.apply_async",
        lambda *args, **kwargs: None,
    )
    client = TestClient(app)
    create_resp = client.post(
        "/api/v1/videos",
        json={
            "topic": "brand voice oral demo",
            "script": "hello from brand voice",
            "voice_id": brand_voice_id,
            "avatar_asset_id": avatar_id,
            "video_mode": "avatar_talk",
        },
        headers=auth_context["headers"],
    )

    assert create_resp.status_code == 202
    unit_id = create_resp.json()["data"]["id"]
    with auth_db() as db:
        task = db.get(VideoTask, unit_id)
        assert task.voice_id is None
        assert task.brand_voice_id == brand_voice_id
        assert task.params["voice_source"] == "brand_voice"
        assert task.params["brand_voice_id"] == brand_voice_id
        assert task.params["tts_speaker_id"] == "oral-brand-speaker"

        class _FakeTTS:
            async def synthesize_speech(self, payload: dict[str, Any]):
                captured_tts_payloads.append(dict(payload))
                audio = tmp_path / "brand-voice.mp3"
                audio.write_bytes(b"MP3")
                return {
                    "audio_path": str(audio),
                    "timeline": [{"text": "hello", "start_ms": 0, "end_ms": 1000}],
                    "duration_ms": 1000,
                }

        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeTTS(),
        )
        monkeypatch.setattr(avatar_talk, "_work_dir", lambda _unit_id: tmp_path)
        monkeypatch.setattr(avatar_talk, "_audio_duration_sec", lambda _path: 1.0)
        monkeypatch.setattr(avatar_talk, "label_artifact_bytes", _passthrough_label)

        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=auth_context["tenant_id"],
            db=db,
            store=_Store(),
            storage=_Storage(),
        )
        avatar_talk.tts_step(ctx)

    assert captured_tts_payloads[0]["voice"] == "oral-brand-speaker"
    assert captured_tts_payloads[0]["voice_source"] == "brand_voice"


def test_avatar_talk_preset_voice_still_uses_voice_code(
    auth_context,
    auth_db,
    monkeypatch,
    tmp_path: Path,
):
    captured_tts_payloads: list[dict[str, Any]] = []
    unit_id = "preset-voice-unit"
    with auth_db() as db:
        voice = Voice(
            provider="edge-tts",
            voice_code="zh-CN-XiaoxiaoNeural",
            display_name="Xiaoxiao",
            gender="female",
            language="zh-CN",
            is_active=True,
        )
        db.add(voice)
        db.flush()
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=auth_context["tenant_id"],
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="preset voice",
                script="preset script",
                voice_id=voice.id,
            )
        )
        db.commit()

        class _FakeTTS:
            async def synthesize_speech(self, payload: dict[str, Any]):
                captured_tts_payloads.append(dict(payload))
                audio = tmp_path / "preset-voice.mp3"
                audio.write_bytes(b"MP3")
                return {
                    "audio_path": str(audio),
                    "timeline": [],
                    "duration_ms": 1000,
                }

        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeTTS(),
        )
        monkeypatch.setattr(avatar_talk, "_work_dir", lambda _unit_id: tmp_path)
        monkeypatch.setattr(avatar_talk, "_audio_duration_sec", lambda _path: 1.0)
        monkeypatch.setattr(avatar_talk, "label_artifact_bytes", _passthrough_label)

        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=auth_context["tenant_id"],
            db=db,
            store=_Store(),
            storage=_Storage(),
        )
        avatar_talk.tts_step(ctx)

    assert captured_tts_payloads[0]["voice"] == "zh-CN-XiaoxiaoNeural"
    assert captured_tts_payloads[0]["voice_source"] == "preset"
