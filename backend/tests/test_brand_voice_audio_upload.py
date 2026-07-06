from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_object_storage
from app.db.models import Asset, BrandVoice
from app.main import app


class _Storage:
    bucket = "bucket"

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
        return f"https://storage.test/{key}"


class _CloneProvider:
    def __init__(self) -> None:
        self.clone_calls: list[dict[str, Any]] = []

    async def clone_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.clone_calls.append(dict(payload))
        return {
            "speaker_id": "brand-speaker-upload",
            "status": "ready",
            "provider": "doubao-voice-clone",
        }

    async def delete_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"released": True}


def test_upload_audio_creates_tenant_scoped_audio_asset(auth_context, auth_db, monkeypatch):
    from app.api.v1.routes import uploads

    storage = _Storage()
    monkeypatch.setattr(uploads, "_probe_audio_duration_ms", lambda *_args, **_kwargs: 6250)
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        resp = client.post(
            "/api/v1/uploads/audio",
            files={"file": ("voice.wav", b"RIFF fake wav bytes", "audio/wav")},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["type"] == "audio"
    assert data["status"] == "ready"
    with auth_db() as db:
        asset = db.get(Asset, data["asset_id"])
        assert asset is not None
        assert asset.tenant_id == auth_context["tenant_id"]
        assert asset.type == "audio"
        assert asset.source == "upload"
        assert asset.status == "ready"
        assert asset.mime_type == "audio/wav"
        assert asset.size_bytes == len(b"RIFF fake wav bytes")
        assert asset.duration_ms == 6250
        assert asset.storage_key.startswith(f"tenants/{auth_context['tenant_id']}/uploads/")
        assert asset.storage_key.endswith(".wav")
        assert storage.saved[asset.storage_key] == (b"RIFF fake wav bytes", "audio/wav")


def test_upload_audio_accepts_codec_param_and_normalizes_mime(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.api.v1.routes import uploads

    storage = _Storage()
    monkeypatch.setattr(uploads, "_probe_audio_duration_ms", lambda *_args, **_kwargs: 8000)
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        resp = client.post(
            "/api/v1/uploads/audio",
            files={
                "file": (
                    "voice.webm",
                    b"webm opus bytes",
                    "audio/webm;codecs=opus",
                )
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 201
    with auth_db() as db:
        asset = db.get(Asset, resp.json()["data"]["asset_id"])
        assert asset is not None
        assert asset.mime_type == "audio/webm"
        assert asset.storage_key.endswith(".webm")
        assert storage.saved[asset.storage_key] == (b"webm opus bytes", "audio/webm")


def test_upload_audio_rejects_unsupported_type_without_asset(auth_context, auth_db):
    storage = _Storage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        resp = client.post(
            "/api/v1/uploads/audio",
            files={"file": ("voice.flac", b"not accepted", "audio/flac")},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 415
    assert storage.saved == {}
    with auth_db() as db:
        assert db.scalar(select(Asset).where(Asset.type == "audio")) is None


def test_upload_audio_rejects_empty_file_without_asset(auth_context, auth_db):
    storage = _Storage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        resp = client.post(
            "/api/v1/uploads/audio",
            files={"file": ("voice.wav", b"", "audio/wav")},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 400
    assert storage.saved == {}
    with auth_db() as db:
        assert db.scalar(select(Asset).where(Asset.type == "audio")) is None


def test_upload_audio_keeps_asset_when_duration_probe_fails(auth_context, auth_db, monkeypatch):
    from app.workers import avatar_talk

    storage = _Storage()

    def fail_probe(*_args, **_kwargs):
        raise RuntimeError("ffprobe unavailable")

    monkeypatch.setattr(avatar_talk, "_audio_duration_sec", fail_probe)
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        resp = client.post(
            "/api/v1/uploads/audio",
            files={"file": ("voice.webm", b"webm audio bytes", "audio/webm")},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 201
    with auth_db() as db:
        asset = db.get(Asset, resp.json()["data"]["asset_id"])
        assert asset is not None
        assert asset.type == "audio"
        assert asset.duration_ms is None
        assert asset.mime_type == "audio/webm"
        assert storage.saved[asset.storage_key] == (b"webm audio bytes", "audio/webm")


def test_upload_audio_then_create_brand_voice_without_direct_db_audio_seed(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.api.v1.routes import brand_voices, uploads

    provider = _CloneProvider()
    storage = _Storage()
    monkeypatch.setattr(uploads, "_probe_audio_duration_ms", lambda *_args, **_kwargs: 6100)
    monkeypatch.setattr(
        brand_voices,
        "resolve",
        lambda _db, *, tenant_id, capability: provider,
    )
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        upload_resp = client.post(
            "/api/v1/uploads/audio",
            files={"file": ("voice.mp3", b"ID3 fake mp3 bytes", "audio/mpeg")},
            headers=auth_context["headers"],
        )
        assert upload_resp.status_code == 201

        create_resp = client.post(
            "/api/v1/brand-voices",
            json={
                "name": "Uploaded Voice",
                "source_audio_asset_id": upload_resp.json()["data"]["asset_id"],
                "consent_confirmed": True,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert create_resp.status_code == 201
    created = create_resp.json()["data"]
    assert created["status"] == "ready"
    assert provider.clone_calls[0]["source_audio_url"].startswith(
        f"https://storage.test/tenants/{auth_context['tenant_id']}/uploads/"
    )
    with auth_db() as db:
        audio_assets = list(db.scalars(select(Asset).where(Asset.type == "audio")))
        assert len(audio_assets) == 1
        assert audio_assets[0].duration_ms == 6100
        brand_voice = db.get(BrandVoice, created["id"])
        assert brand_voice is not None
        assert brand_voice.source_audio_asset_id == audio_assets[0].id


def test_create_brand_voice_accepts_historical_codec_param_audio_asset(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.api.v1.routes import brand_voices

    provider = _CloneProvider()
    storage = _Storage()
    monkeypatch.setattr(
        brand_voices,
        "resolve",
        lambda _db, *, tenant_id, capability: provider,
    )
    with auth_db() as db:
        asset = Asset(
            tenant_id=auth_context["tenant_id"],
            type="audio",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/legacy.webm",
            mime_type="audio/webm;codecs=opus",
            size_bytes=1234,
            duration_ms=8000,
            status="ready",
        )
        db.add(asset)
        db.commit()
        asset_id = asset.id

    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        create_resp = client.post(
            "/api/v1/brand-voices",
            json={
                "name": "Legacy WebM Voice",
                "source_audio_asset_id": asset_id,
                "consent_confirmed": True,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert create_resp.status_code == 201
    assert provider.clone_calls[0]["source_audio_asset_id"] == asset_id
