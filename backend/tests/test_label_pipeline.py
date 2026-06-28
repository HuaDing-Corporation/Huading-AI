from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.db.models import Asset, Tenant, VideoTask, Voice
from app.main import app
from app.workers import avatar_talk, image_gen, video_tasks


class _Storage:
    def __init__(self) -> None:
        self.bucket = "label-test-bucket"
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


class _Store:
    def __init__(self) -> None:
        self.history: list[tuple[str, dict[str, object]]] = []

    def update(self, key: str, **payload: object) -> None:
        self.history.append((key, payload))


class _ImageProvider:
    def __init__(self, image_bytes: bytes) -> None:
        self.image_bytes = image_bytes
        self.payloads: list[dict[str, object]] = []

    async def generate_image(self, payload: dict[str, object]) -> dict[str, object]:
        self.payloads.append(payload)
        return {
            "image_bytes": self.image_bytes,
            "model": "mock-image",
            "size": payload.get("size") or "1024x1024",
            "quality": payload.get("quality") or "medium",
            "mode": "edit" if payload.get("input_image_path") else "generate",
        }


def _png_bytes(
    *,
    size: tuple[int, int] = (320, 240),
    color: tuple[int, int, int, int] = (240, 240, 240, 255),
    transparent_corner: bool = False,
) -> bytes:
    image = Image.new("RGBA", size, color)
    if transparent_corner:
        for x in range(0, size[0] // 4):
            for y in range(0, size[1] // 4):
                image.putpixel((x, y), (0, 0, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _product_png_bytes() -> bytes:
    image = Image.new("RGBA", (360, 260), (255, 255, 255, 0))
    for x in range(90, 270):
        for y in range(60, 210):
            image.putpixel((x, y), (80, 120, 220, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _png_text(image_bytes: bytes) -> dict[str, str]:
    with Image.open(BytesIO(image_bytes)) as image:
        return dict(getattr(image, "text", {}) or image.info)


def _assert_png_has_synthetic_metadata(image_bytes: bytes, *, content_id: str) -> None:
    text = _png_text(image_bytes)
    assert text["aigc_label"] == "AI_GENERATED_SYNTHETIC"
    assert text["aigc_provider_name"] == "Huading"
    assert "aigc_provider_code" in text
    assert text["aigc_content_id"] == content_id


def _assert_bottom_center_has_visible_label(image_bytes: bytes) -> None:
    with Image.open(BytesIO(image_bytes)) as image:
        rgba = image.convert("RGBA")
        width, height = rgba.size
        crop = rgba.crop((width // 2 - 80, height - 70, width // 2 + 80, height - 8))
        pixels = [pixel for pixel in crop.getdata() if pixel[3] > 0]
    assert any(sum(pixel[:3]) > 735 for pixel in pixels)
    assert any(sum(pixel[:3]) < 240 for pixel in pixels)


def _seed_photo_task(db, *, tenant_id: str, user_id: str, task_id: str) -> None:
    db.add(
        VideoTask(
            id=task_id,
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            mode="photo",
            video_mode="photo",
            status="queued",
            progress=0,
            topic="label test photo",
            params={},
        )
    )
    db.commit()


def _seed_source_asset(db, *, tenant_id: str, asset_id: str) -> str:
    storage_key = f"tenants/{tenant_id}/uploads/{asset_id}.png"
    db.add(
        Asset(
            id=asset_id,
            tenant_id=tenant_id,
            type="product_image",
            source="upload",
            storage_key=storage_key,
            mime_type="image/png",
            status="ready",
        )
    )
    db.commit()
    return storage_key


def _patch_image_worker(monkeypatch, auth_db, storage: _Storage, store: _Store, provider):
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(image_gen, "build_progress_store", lambda _redis_url: store)
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "resolve", lambda _db, *, tenant_id, capability: provider)


def test_tenant_label_settings_default_update_false_is_ignored_and_isolated(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)

    default_resp = client.get(
        "/api/v1/tenant/label-settings",
        headers=auth_context["headers"],
    )
    assert default_resp.status_code == 200
    assert default_resp.json()["data"] == {
        "position": "br",
        "text": "AI 生成",
        "enabled": True,
    }

    update_resp = client.put(
        "/api/v1/tenant/label-settings",
        json={"position": "bc", "text": "LABEL", "enabled": False},
        headers=auth_context["headers"],
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"] == {
        "position": "bc",
        "text": "LABEL",
        "enabled": True,
    }

    register_other = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": "label-other",
            "tenant_name": "Other Label",
            "email": "other-owner@example.com",
            "password": "secret-pass",
        },
    )
    assert register_other.status_code == 201
    other_headers = {
        "Authorization": f"Bearer {register_other.json()['data']['token']['access_token']}"
    }
    other_resp = client.get("/api/v1/tenant/label-settings", headers=other_headers)
    assert other_resp.status_code == 200
    assert other_resp.json()["data"] == {
        "position": "br",
        "text": "AI 生成",
        "enabled": True,
    }

    with auth_db() as db:
        assert db.scalar(select(Tenant).where(Tenant.slug == "acme")) is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"position": "center", "text": "LABEL"},
        {"position": "br", "text": ""},
        {"position": "br", "text": "X" * 21},
    ],
)
def test_tenant_label_settings_rejects_invalid_payloads(auth_context, payload) -> None:
    resp = TestClient(app).put(
        "/api/v1/tenant/label-settings",
        json=payload,
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422


def test_image_label_util_writes_visible_watermark_and_png_metadata() -> None:
    from app.services.synthetic_label import LabelSettings, SyntheticLabelMeta, label_artifact_bytes

    source = _png_bytes(size=(320, 240))

    labeled = label_artifact_bytes(
        source,
        kind="image",
        settings=LabelSettings(position="bc", text="LABEL"),
        meta=SyntheticLabelMeta(
            content_id="asset-label-unit",
            provider_name="Huading",
            provider_code="provider-pending",
        ),
    )

    _assert_png_has_synthetic_metadata(labeled, content_id="asset-label-unit")
    _assert_bottom_center_has_visible_label(labeled)


@pytest.mark.parametrize(
    ("product_kind", "params", "provider_bytes"),
    [
        ("ai_photo", {}, _png_bytes()),
        (
            "ecom_cutout",
            {
                "kind": "ecom_cutout",
                "background": "transparent",
                "source_asset_id": "label-cutout-source",
            },
            _png_bytes(transparent_corner=True),
        ),
        (
            "ecom_model",
            {
                "kind": "ecom_model",
                "gender": "female",
                "style_id": "street",
                "source_asset_id": "label-model-source",
            },
            _png_bytes(),
        ),
        (
            "ecom_poster",
            {
                "kind": "ecom_poster",
                "template_id": "minimal",
                "title": "Poster",
                "subtitle": "Sale",
                "source_asset_id": "label-poster-source",
            },
            _png_bytes(),
        ),
    ],
)
def test_image_worker_labels_each_external_image_product(
    monkeypatch,
    auth_context,
    auth_db,
    product_kind: str,
    params: dict[str, object],
    provider_bytes: bytes,
) -> None:
    params = dict(params)
    client = TestClient(app)
    settings_resp = client.put(
        "/api/v1/tenant/label-settings",
        json={"position": "bc", "text": "LABEL", "enabled": False},
        headers=auth_context["headers"],
    )
    assert settings_resp.status_code == 200

    task_id = f"label-{product_kind}"
    storage = _Storage()
    with auth_db() as db:
        _seed_photo_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            task_id=task_id,
        )
        if product_kind != "ai_photo":
            source_key = _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=str(params["source_asset_id"]),
            )
            params["source_storage_key"] = source_key
            storage.saved[source_key] = (
                _product_png_bytes(),
                "image/png",
            )

    store = _Store()
    provider = _ImageProvider(provider_bytes)
    _patch_image_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "label test",
            **params,
        }
    )

    assert result["status"] == "SUCCESS"
    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    saved_bytes, content_type = storage.saved[output_key]
    assert content_type == "image/png"
    _assert_png_has_synthetic_metadata(saved_bytes, content_id=task_id)
    _assert_bottom_center_has_visible_label(saved_bytes)

    with auth_db() as db:
        asset = db.scalars(select(Asset).where(Asset.storage_key == output_key)).one()
        assert asset.metadata_["synthetic_label"]["content_id"] == task_id
        assert asset.metadata_["synthetic_label"]["text"] == "LABEL"
        assert asset.metadata_["synthetic_label"]["enabled"] is True


def test_image_worker_does_not_upload_unlabeled_output_when_labeling_fails(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "label-image-failure"
    with auth_db() as db:
        _seed_photo_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            task_id=task_id,
        )
    storage = _Storage()
    _patch_image_worker(
        monkeypatch,
        auth_db,
        storage,
        _Store(),
        _ImageProvider(_png_bytes()),
    )

    def fail_label(_content: bytes, **_kwargs) -> bytes:
        raise RuntimeError("label writer failed")

    monkeypatch.setattr(image_gen, "label_artifact_bytes", fail_label)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "label failure",
        }
    )

    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    assert result["status"] == "FAILURE"
    assert output_key not in storage.saved
    with auth_db() as db:
        assert db.get(VideoTask, task_id).status == "failed"


def test_legacy_video_worker_labels_output_before_storage(
    monkeypatch,
    tmp_path: Path,
    auth_context,
    auth_db,
) -> None:
    task_id = "label-static-video"
    storage = _Storage()
    store = _Store()
    calls: list[dict[str, object]] = []

    with auth_db() as db:
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=auth_context["tenant_id"],
                created_by_user_id=auth_context["user_id"],
                mode="static_template",
                video_mode="static_template",
                status="queued",
                progress=0,
                topic="legacy static",
            )
        )
        db.commit()

    async def fake_engine(_params, _progress_cb):
        output = tmp_path / "static.mp4"
        output.write_bytes(b"STATIC-MP4")
        return {"video_path": str(output), "duration": 2.0, "file_size": 10}

    def fake_label_artifact_bytes(content: bytes, **kwargs) -> bytes:
        calls.append(kwargs)
        return content + b"|LABEL"

    monkeypatch.setattr(video_tasks, "SessionLocal", auth_db)
    monkeypatch.setattr(video_tasks, "_generate_with_engine", fake_engine)
    monkeypatch.setattr(video_tasks, "build_progress_store", lambda _url: store)
    monkeypatch.setattr(video_tasks, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(video_tasks, "label_artifact_bytes", fake_label_artifact_bytes)

    result = video_tasks.generate_video_task.apply(
        args=[
            {
                "tenant_id": auth_context["tenant_id"],
                "video_task_id": task_id,
                "topic": "legacy static",
            }
        ],
        task_id=task_id,
    ).get()

    output_key = f"tenants/{auth_context['tenant_id']}/videos/{task_id}/output.mp4"
    assert storage.saved[output_key] == (b"STATIC-MP4|LABEL", "video/mp4")
    assert calls[0]["kind"] == "video"
    assert calls[0]["suffix"] == ".mp4"
    assert calls[0]["meta"].content_id == task_id
    assert result["file_size"] == len(b"STATIC-MP4|LABEL")
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task.status == "done"
        assert task.size_bytes == len(b"STATIC-MP4|LABEL")


@pytest.mark.parametrize("mode", ["avatar_talk", "seedance_i2v"])
def test_avatar_upload_step_labels_avatar_and_i2v_videos(
    monkeypatch,
    auth_context,
    auth_db,
    mode: str,
) -> None:
    from app.services.synthetic_label import LabelSettings, SyntheticLabelMeta

    task_id = f"label-video-{mode}"
    storage = _Storage()
    calls: list[dict[str, object]] = []

    with auth_db() as db:
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=auth_context["tenant_id"],
                created_by_user_id=auth_context["user_id"],
                mode=mode,
                video_mode=mode,
                status="running",
                progress=95,
            )
        )
        db.commit()

    def fake_label_artifact_bytes(
        content: bytes,
        *,
        kind: str,
        settings: LabelSettings,
        meta: SyntheticLabelMeta,
        suffix: str | None = None,
    ) -> bytes:
        calls.append(
            {
                "kind": kind,
                "settings": settings,
                "meta": meta,
                "suffix": suffix,
            }
        )
        return content + b"|LABELED"

    monkeypatch.setattr(avatar_talk, "label_artifact_bytes", fake_label_artifact_bytes)

    with auth_db() as db:
        ctx = avatar_talk.AvatarTalkContext(
            task_id=task_id,
            tenant_id=auth_context["tenant_id"],
            db=db,
            store=_Store(),
            storage=storage,
            duration_sec=2,
        )
        ctx.final_video_bytes = b"MP4"
        avatar_talk.upload_step(ctx)

    output_key = f"tenants/{auth_context['tenant_id']}/videos/{task_id}/final.mp4"
    assert storage.saved[output_key] == (b"MP4|LABELED", "video/mp4")
    assert calls[0]["kind"] == "video"
    assert calls[0]["suffix"] == ".mp4"
    assert calls[0]["settings"].enabled is True
    assert calls[0]["meta"].content_id == task_id


def test_avatar_tts_output_audio_is_implicitly_labeled_before_storage(
    monkeypatch,
    tmp_path: Path,
    auth_context,
    auth_db,
) -> None:
    from app.services.synthetic_label import LabelSettings, SyntheticLabelMeta

    task_id = "label-audio-avatar"
    storage = _Storage()
    calls: list[dict[str, object]] = []

    with auth_db() as db:
        voice = Voice(
            id="voice-label",
            provider="edge-tts",
            voice_code="zh-CN-XiaoxiaoNeural",
            display_name="Xiaoxiao",
            gender="female",
        )
        task = VideoTask(
            id=task_id,
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            mode="avatar_talk",
            video_mode="avatar_talk",
            status="running",
            script="hello",
            voice_id=voice.id,
        )
        db.add(voice)
        db.flush()
        db.add(task)
        db.commit()

    class _TtsProvider:
        async def synthesize_speech(self, payload: dict[str, object]) -> dict[str, object]:
            audio_path = tmp_path / "voice.mp3"
            audio_path.write_bytes(b"MP3")
            return {
                "audio_path": str(audio_path),
                "duration_ms": 1000,
                "timeline": [],
            }

    def fake_label_artifact_bytes(
        content: bytes,
        *,
        kind: str,
        settings: LabelSettings,
        meta: SyntheticLabelMeta,
        suffix: str | None = None,
    ) -> bytes:
        calls.append(
            {
                "kind": kind,
                "settings": settings,
                "meta": meta,
                "suffix": suffix,
            }
        )
        return content + b"|AUDIO-LABEL"

    monkeypatch.setattr(
        avatar_talk,
        "resolve",
        lambda _db, *, tenant_id, capability: _TtsProvider(),
    )
    monkeypatch.setattr(avatar_talk, "_work_dir", lambda _task_id: tmp_path)
    monkeypatch.setattr(avatar_talk, "_audio_duration_sec", lambda _path: 1.0)
    monkeypatch.setattr(avatar_talk, "label_artifact_bytes", fake_label_artifact_bytes)

    with auth_db() as db:
        ctx = avatar_talk.AvatarTalkContext(
            task_id=task_id,
            tenant_id=auth_context["tenant_id"],
            db=db,
            store=_Store(),
            storage=storage,
        )
        avatar_talk.tts_step(ctx)

    audio_key = f"tenants/{auth_context['tenant_id']}/videos/{task_id}/audio.mp3"
    assert storage.saved[audio_key] == (b"MP3|AUDIO-LABEL", "audio/mpeg")
    assert calls[0]["kind"] == "audio"
    assert calls[0]["suffix"] == ".mp3"
    assert calls[0]["meta"].content_id == task_id
