from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_object_storage, get_progress_store
from app.db.models import (
    Asset,
    BgmLibraryTrack,
    Subscription,
    TaskAsset,
    UsageRecord,
    VideoTask,
)
from app.main import app
from app.schemas.videos import VideoGenerateRequest


class _Storage:
    bucket = "videogen-test-bucket"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.objects[key] = (content, content_type)
        return f"memory://{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key][0]

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
        self.deleted.append(key)
        self.objects.pop(key, None)


class _MemProgressStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, Any]] = {}

    def update(self, task_id: str, **fields: Any) -> None:
        current = dict(self.snapshots.get(task_id, {}))
        current.update({name: value for name, value in fields.items() if value is not None})
        self.snapshots[task_id] = current

    def read(self, task_id: str) -> dict[str, Any] | None:
        return self.snapshots.get(task_id)


def _seed_image_asset(db, *, tenant_id: str, asset_id: str) -> Asset:
    asset = Asset(
        id=asset_id,
        tenant_id=tenant_id,
        type="product_image",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/{asset_id}.png",
        mime_type="image/png",
        size_bytes=8,
        status="ready",
    )
    db.add(asset)
    return asset


def _seed_audio_asset(db, *, tenant_id: str, asset_id: str) -> Asset:
    asset = Asset(
        id=asset_id,
        tenant_id=tenant_id,
        type="audio",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/{asset_id}.mp3",
        mime_type="audio/mpeg",
        size_bytes=8,
        duration_ms=12_000,
        status="ready",
    )
    db.add(asset)
    return asset


def _subscription(db, tenant_id: str) -> Subscription:
    subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    assert subscription is not None
    return subscription


def _reset_subscription_quota(db, tenant_id: str) -> Subscription:
    subscription = _subscription(db, tenant_id)
    subscription.quota_credits_total = 1000
    subscription.quota_credits_used = 0
    subscription.quota_credits_reserved = 0
    return subscription


def _add_video_gen_task(
    db,
    *,
    tenant_id: str,
    user_id: str,
    task_id: str,
    params: dict[str, Any],
) -> VideoTask:
    task = VideoTask(
        id=task_id,
        tenant_id=tenant_id,
        created_by_user_id=user_id,
        status="queued",
        mode="video_gen",
        video_mode="video_gen",
        topic="A clean product video",
        duration_sec=5,
        params=params,
    )
    db.add(task)
    db.flush()
    return task


def _add_reserved_video_gen_usage(
    db,
    *,
    tenant_id: str,
    subscription: Subscription,
    task_id: str,
) -> None:
    db.add(
        UsageRecord(
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            video_task_id=task_id,
            capability="video_gen",
            provider="seedance",
            model="doubao-seedance-2-0-mini-pending",
            unit="second",
            quantity=Decimal("5"),
            credits=Decimal("10.00"),
            cost_cents=0,
            status="reserved",
        )
    )
    subscription.quota_credits_reserved = 10


def _write_test_video(path: Path) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:d=1:r=24",
            "-an",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path.read_bytes()


def _write_test_wav(path: Path) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path.read_bytes()


def _probe_stream_types(path: Path) -> set[str]:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-print_format",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    return {stream["codec_type"] for stream in streams}


def test_bgm_library_returns_seeded_royalty_free_tracks(auth_context) -> None:
    storage = _Storage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).get(
            "/api/v1/bgm-library",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) >= 3
    assert {item["track_id"] for item in items} >= {
        "ambient-soft-loop",
        "bright-product-pop",
        "calm-tech-pulse",
    }
    assert all(item["preview_url"].startswith("https://storage.test/") for item in items)
    assert all("royalty-free" in item["license"].lower() for item in items)


def test_video_gen_schema_requires_reference_images_and_duration_enum() -> None:
    ok = VideoGenerateRequest.model_validate(
        {
            "video_mode": "video_gen",
            "prompt": "A cinematic product reveal",
            "reference_image_asset_ids": ["asset-ref-a"],
            "duration_sec": 10,
        }
    )
    assert ok.topic == "A cinematic product reveal"
    assert ok.prompt == "A cinematic product reveal"
    assert ok.duration_sec == 10
    assert ok.resolution == "720p"

    for payload, expected in [
        (
            {
                "video_mode": "video_gen",
                "prompt": "x",
                "reference_image_asset_ids": [],
                "duration_sec": 10,
            },
            "reference_image_asset_ids",
        ),
        (
            {
                "video_mode": "video_gen",
                "prompt": "x",
                "reference_image_asset_ids": [f"asset-ref-{index}" for index in range(10)],
                "duration_sec": 10,
            },
            "at most 9",
        ),
        (
            {
                "video_mode": "video_gen",
                "prompt": "x",
                "reference_image_asset_ids": ["asset-ref-a"],
                "duration_sec": 7,
            },
            "duration_sec",
        ),
        (
            {
                "video_mode": "video_gen",
                "prompt": " ",
                "reference_image_asset_ids": ["asset-ref-a"],
                "duration_sec": 10,
            },
            "prompt",
        ),
    ]:
        try:
            VideoGenerateRequest.model_validate(payload)
        except ValueError as exc:
            assert expected in str(exc)
        else:  # pragma: no cover - assertion aid
            raise AssertionError(f"payload unexpectedly passed: {payload}")


def test_create_video_gen_validates_assets_reserves_quota_and_enqueues(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.api.v1.routes import videos as videos_route

    enqueued: list[dict[str, Any]] = []

    class _Task:
        @staticmethod
        def apply_async(*, args: list[dict[str, Any]], task_id: str, queue: str):
            enqueued.append({"args": args, "task_id": task_id, "queue": queue})

            class _Result:
                status = "queued"

            return _Result()

    monkeypatch.setattr(videos_route, "generate_video_gen_task", _Task, raising=False)
    with auth_db() as db:
        _reset_subscription_quota(db, auth_context["tenant_id"])
        _seed_image_asset(db, tenant_id=auth_context["tenant_id"], asset_id="asset-ref-a")
        _seed_image_asset(db, tenant_id=auth_context["tenant_id"], asset_id="asset-ref-b")
        _seed_audio_asset(db, tenant_id=auth_context["tenant_id"], asset_id="asset-bgm-a")
        db.commit()

    resp = TestClient(app).post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "A premium handbag rotating under studio lights",
            "reference_image_asset_ids": ["asset-ref-a", "asset-ref-b"],
            "duration_sec": 15,
            "resolution": "480p",
            "bgm": {"source": "upload", "asset_id": "asset-bgm-a"},
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    task_id = resp.json()["data"]["id"]
    assert enqueued == [
        {
            "args": [
                {
                    "video_mode": "video_gen",
                    "prompt": "A premium handbag rotating under studio lights",
                    "topic": "A premium handbag rotating under studio lights",
                    "reference_image_asset_ids": ["asset-ref-a", "asset-ref-b"],
                    "duration_sec": 15,
                    "resolution": "480p",
                    "bgm": {"source": "upload", "asset_id": "asset-bgm-a"},
                    "tenant_id": auth_context["tenant_id"],
                    "video_task_id": task_id,
                }
            ],
            "task_id": task_id,
            "queue": "avatar",
        }
    ]
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task is not None
        assert task.mode == "video_gen"
        assert task.video_mode == "video_gen"
        assert task.topic == "A premium handbag rotating under studio lights"
        assert task.duration_sec == 15
        assert task.params["resolution"] == "480p"
        assert task.params["bgm"] == {"source": "upload", "asset_id": "asset-bgm-a"}
        roles = {
            item.role
            for item in db.scalars(
                select(TaskAsset).where(TaskAsset.video_task_id == task_id)
            )
        }
        assert roles == {"input_reference_image", "input_bgm"}
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        assert usage is not None
        assert usage.capability == "video_gen"
        assert usage.provider == "seedance"
        assert usage.status == "reserved"
        subscription = _subscription(db, auth_context["tenant_id"])
        assert subscription.quota_credits_reserved > 0
        assert subscription.quota_credits_used == 0


def test_create_video_gen_hides_cross_tenant_reference_and_bgm_assets(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)
    other_resp = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": "video-gen-other",
            "tenant_name": "Other",
            "email": "other-video-gen@example.com",
            "password": "unit-pass-123",
        },
    )
    assert other_resp.status_code == 201
    other_tenant_id = other_resp.json()["data"]["tenant"]["id"]

    with auth_db() as db:
        _reset_subscription_quota(db, auth_context["tenant_id"])
        _seed_image_asset(db, tenant_id=other_tenant_id, asset_id="asset-other-ref")
        _seed_image_asset(db, tenant_id=auth_context["tenant_id"], asset_id="asset-ref-owned")
        _seed_audio_asset(db, tenant_id=other_tenant_id, asset_id="asset-other-bgm")
        db.commit()

    cross_ref = client.post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "x",
            "reference_image_asset_ids": ["asset-other-ref"],
            "duration_sec": 5,
        },
        headers=auth_context["headers"],
    )
    cross_bgm = client.post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "x",
            "reference_image_asset_ids": ["asset-ref-owned"],
            "duration_sec": 5,
            "bgm": {"source": "upload", "asset_id": "asset-other-bgm"},
        },
        headers=auth_context["headers"],
    )

    assert cross_ref.status_code == 404
    assert cross_ref.json()["error"]["code"] == "REFERENCE_IMAGE_NOT_FOUND"
    assert cross_bgm.status_code == 404
    assert cross_bgm.json()["error"]["code"] == "BGM_ASSET_NOT_FOUND"


def test_video_gen_pipeline_settles_quota_stores_labeled_output_and_history(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services.synthetic_label import LabelSettings, SyntheticLabelMeta
    from app.workers import video_gen

    storage = _Storage()
    task_id = "video-gen-unit"
    with auth_db() as db:
        subscription = _reset_subscription_quota(db, auth_context["tenant_id"])
        ref = _seed_image_asset(db, tenant_id=auth_context["tenant_id"], asset_id="asset-ref-a")
        storage.objects[ref.storage_key] = (b"PNGDATA", "image/png")
        _add_video_gen_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            task_id=task_id,
            params={
                "reference_image_asset_ids": [ref.id],
                "duration_sec": 5,
                "resolution": "720p",
            },
        )
        db.add(TaskAsset(video_task_id=task_id, asset_id=ref.id, role="input_reference_image"))
        _add_reserved_video_gen_usage(
            db,
            tenant_id=auth_context["tenant_id"],
            subscription=subscription,
            task_id=task_id,
        )
        db.commit()

    provider_payloads: list[dict[str, Any]] = []
    label_calls: list[dict[str, Any]] = []

    def fake_generate(ctx: video_gen.VideoGenContext) -> bytes:
        provider_payloads.append(video_gen._provider_payload(ctx))
        return b"MP4"

    def fake_label_artifact_bytes(
        content: bytes,
        *,
        kind: str,
        settings: LabelSettings,
        meta: SyntheticLabelMeta,
        suffix: str | None = None,
    ) -> bytes:
        label_calls.append(
            {"kind": kind, "settings": settings, "meta": meta, "suffix": suffix}
        )
        return content + b"|LABEL"

    monkeypatch.setattr(video_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(video_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(video_gen, "build_progress_store", lambda _url: _MemProgressStore())
    monkeypatch.setattr(video_gen, "_generate_seedance_mini_video", fake_generate)
    monkeypatch.setattr(video_gen, "label_artifact_bytes", fake_label_artifact_bytes)

    result = video_gen.run_video_gen_pipeline(
        tenant_id=auth_context["tenant_id"],
        task_id=task_id,
    )

    output_key = f"tenants/{auth_context['tenant_id']}/videos/{task_id}/final.mp4"
    assert storage.objects[output_key] == (b"MP4|LABEL", "video/mp4")
    assert result["storage_key"] == output_key
    assert provider_payloads[0]["model"] == "doubao-seedance-2-0-mini-pending"
    assert provider_payloads[0]["duration_sec"] == 5
    assert provider_payloads[0]["resolution"] == "720p"
    assert provider_payloads[0]["reference_images"][0]["bytes"] == b"PNGDATA"
    assert label_calls[0]["kind"] == "video"
    assert label_calls[0]["suffix"] == ".mp4"
    assert label_calls[0]["meta"].content_id == task_id
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task.status == "done"
        assert task.storage_key == output_key
        assert task.content_type == "video/mp4"
        assert task.progress == 100
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        assert usage.status == "settled"
        subscription = _subscription(db, auth_context["tenant_id"])
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 10


def test_video_gen_pipeline_setup_failure_marks_failed_and_releases_reserved(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.workers import video_gen

    task_id = "video-gen-setup-failure"
    with auth_db() as db:
        subscription = _reset_subscription_quota(db, auth_context["tenant_id"])
        _add_video_gen_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            task_id=task_id,
            params={
                "reference_image_asset_ids": ["asset-ref-a"],
                "duration_sec": 5,
                "resolution": "720p",
            },
        )
        _add_reserved_video_gen_usage(
            db,
            tenant_id=auth_context["tenant_id"],
            subscription=subscription,
            task_id=task_id,
        )
        db.commit()

    seen_settings: list[object] = []

    def broken_create_object_storage(config: object):
        seen_settings.append(config)
        raise RuntimeError("storage boot failed")

    monkeypatch.setattr(video_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(video_gen, "create_object_storage", broken_create_object_storage)
    monkeypatch.setattr(video_gen, "build_progress_store", lambda _url: _MemProgressStore())

    with pytest.raises(RuntimeError, match="storage boot failed"):
        video_gen.run_video_gen_pipeline(
            tenant_id=auth_context["tenant_id"],
            task_id=task_id,
        )

    assert seen_settings == [video_gen.settings]
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task is not None
        assert task.status == "failed"
        assert task.error_code == "VIDEO_GEN_FAILED"
        assert task.error_message == "storage boot failed"
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        assert usage is not None
        assert usage.status == "released"
        subscription = _subscription(db, auth_context["tenant_id"])
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 0


def test_video_gen_pipeline_mixes_library_bgm_from_track_storage(
    monkeypatch,
    tmp_path: Path,
    auth_context,
    auth_db,
) -> None:
    from app.workers import video_gen

    storage = _Storage()
    task_id = "video-gen-library-bgm"
    generated_video = _write_test_video(tmp_path / "generated.mp4")
    library_audio = _write_test_wav(tmp_path / "library.wav")
    library_key = "library/bgm/ambient-soft-loop.wav"
    storage.objects[library_key] = (library_audio, "audio/wav")

    with auth_db() as db:
        subscription = _reset_subscription_quota(db, auth_context["tenant_id"])
        ref = _seed_image_asset(db, tenant_id=auth_context["tenant_id"], asset_id="asset-ref-a")
        storage.objects[ref.storage_key] = (b"PNGDATA", "image/png")
        db.add(
            BgmLibraryTrack(
                track_id="ambient-soft-loop",
                name="Ambient Soft Loop",
                duration_sec=2,
                storage_key=library_key,
                preview_storage_key=library_key,
                license="royalty-free",
                is_active=True,
            )
        )
        _add_video_gen_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            task_id=task_id,
            params={
                "reference_image_asset_ids": [ref.id],
                "duration_sec": 5,
                "resolution": "720p",
                "bgm": {"source": "library", "track_id": "ambient-soft-loop"},
            },
        )
        db.add(TaskAsset(video_task_id=task_id, asset_id=ref.id, role="input_reference_image"))
        _add_reserved_video_gen_usage(
            db,
            tenant_id=auth_context["tenant_id"],
            subscription=subscription,
            task_id=task_id,
        )
        db.commit()

    monkeypatch.setattr(video_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(video_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(video_gen, "build_progress_store", lambda _url: _MemProgressStore())
    monkeypatch.setattr(video_gen, "_generate_seedance_mini_video", lambda _ctx: generated_video)
    monkeypatch.setattr(video_gen, "label_artifact_bytes", lambda content, **_kwargs: content)

    result = video_gen.run_video_gen_pipeline(
        tenant_id=auth_context["tenant_id"],
        task_id=task_id,
    )

    output_key = result["storage_key"]
    output_path = tmp_path / "worker-library-mixed.mp4"
    output_path.write_bytes(storage.objects[output_key][0])
    assert _probe_stream_types(output_path) >= {"video", "audio"}
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task is not None
        assert task.status == "done"
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        assert usage is not None
        assert usage.status == "settled"
        subscription = _subscription(db, auth_context["tenant_id"])
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 10


def test_video_gen_bgm_mixing_adds_audio_stream(tmp_path: Path) -> None:
    from app.workers import video_gen

    video_path = tmp_path / "base.mp4"
    audio_path = tmp_path / "bgm.wav"
    mixed_path = tmp_path / "mixed.mp4"

    mixed = video_gen._mix_bgm_bytes(
        _write_test_video(video_path),
        _write_test_wav(audio_path),
        ".wav",
    )
    mixed_path.write_bytes(mixed)
    assert _probe_stream_types(mixed_path) >= {"video", "audio"}


def test_video_gen_list_filter_keeps_new_mode_isolated(auth_context, auth_db) -> None:
    storage = _Storage()
    with auth_db() as db:
        base_time = datetime(2026, 6, 29, tzinfo=UTC)
        db.add_all(
            [
                VideoTask(
                    id="video-gen-history-a",
                    tenant_id=auth_context["tenant_id"],
                    status="done",
                    mode="video_gen",
                    video_mode="video_gen",
                    topic="video gen",
                    created_at=base_time + timedelta(minutes=1),
                    storage_key=f"tenants/{auth_context['tenant_id']}/videos/video-gen-history-a/final.mp4",
                    content_type="video/mp4",
                ),
                VideoTask(
                    id="avatar-history-a",
                    tenant_id=auth_context["tenant_id"],
                    status="done",
                    mode="avatar_talk",
                    video_mode="avatar_talk",
                    topic="avatar",
                    created_at=base_time,
                ),
            ]
        )
        db.commit()

    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_progress_store] = lambda: _MemProgressStore()
    try:
        resp = TestClient(app).get(
            "/api/v1/videos?mode=video_gen",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_progress_store, None)
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["id"] == "video-gen-history-a"
    assert data["items"][0]["mode"] == "video_gen"
