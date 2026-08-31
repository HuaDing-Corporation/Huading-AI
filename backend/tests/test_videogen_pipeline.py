from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_object_storage, get_progress_store
from app.core.image_aspect_ratio import VIDEO_GEN_ASPECT_RATIOS
from app.db.models import (
    Asset,
    BatchJob,
    BgmLibraryTrack,
    Subscription,
    TaskAsset,
    UsageRecord,
    VideoTask,
)
from app.main import app
from app.schemas.videos import VideoGenerateRequest
from app.services import bgm_library
from app.services.quota import (
    _VIDEO_GEN_RESOLUTION_MULTIPLIERS,
    estimate_video_gen_quota,
)


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


def _seed_video_reference_asset(
    db,
    *,
    tenant_id: str,
    asset_id: str,
    duration_ms: int,
) -> Asset:
    asset = Asset(
        id=asset_id,
        tenant_id=tenant_id,
        type="video",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/{asset_id}.mp4",
        mime_type="video/mp4",
        size_bytes=16,
        duration_ms=duration_ms,
        width=720,
        height=1280,
        status="ready",
        metadata_={"purpose": "video_gen_reference"},
    )
    db.add(asset)
    return asset


def _subscription(db, tenant_id: str) -> Subscription:
    subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    assert subscription is not None
    return subscription


def _reset_subscription_quota(db, tenant_id: str) -> Subscription:
    subscription = _subscription(db, tenant_id)
    subscription.quota_credits_total = 10000
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


def test_bgm_library_returns_seeded_royalty_free_tracks(auth_context, auth_db) -> None:
    storage = _Storage()
    with auth_db() as db:
        bgm_library.ensure_default_bgm_tracks(db, storage=storage)
        db.commit()

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
    assert len(items) == 10
    assert all(item["track_id"].startswith("mixkit-") for item in items)
    assert all(item["preview_url"].startswith("https://storage.test/") for item in items)
    assert all("platform/bgm/" in item["preview_url"] for item in items)
    assert all(item["license"] == "Mixkit License" for item in items)


def test_video_gen_schema_allows_t2v_or_i2v_and_validates_duration_range() -> None:
    ok = VideoGenerateRequest.model_validate(
        {
            "video_mode": "video_gen",
            "prompt": "A cinematic product reveal",
            "duration_sec": 10,
            "resolution": "1080p",
        }
    )
    assert ok.topic == "A cinematic product reveal"
    assert ok.prompt == "A cinematic product reveal"
    assert ok.duration_sec == 10
    assert ok.resolution == "1080p"
    assert ok.reference_image_asset_ids == []

    for payload, expected in [
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
                "duration_sec": 3,
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


def test_video_gen_schema_accepts_custom_duration_inside_four_to_fifteen() -> None:
    request = VideoGenerateRequest.model_validate(
        {
            "video_mode": "video_gen",
            "prompt": "A seven second product reveal",
            "duration_sec": 7,
        }
    )

    assert request.duration_sec == 7


def test_video_gen_schema_enforces_video_reference_count_uniqueness_and_media_exclusivity(
    auth_context,
) -> None:
    valid = VideoGenerateRequest.model_validate(
        {
            "video_mode": "video_gen",
            "prompt": "A product inherits motion from the references",
            "duration_sec": 5,
            "reference_video_asset_ids": ["video-a", "video-b", "video-c"],
        }
    )
    assert valid.reference_video_asset_ids == ["video-a", "video-b", "video-c"]
    assert valid.aspect_ratio == "auto"

    client = TestClient(app)
    conflict = client.post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "conflicting references",
            "duration_sec": 5,
            "reference_image_asset_ids": ["image-a"],
            "reference_video_asset_ids": ["video-a"],
        },
        headers=auth_context["headers"],
    )
    too_many = client.post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "too many references",
            "duration_sec": 5,
            "reference_video_asset_ids": ["a", "b", "c", "d"],
        },
        headers=auth_context["headers"],
    )
    duplicate = client.post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "duplicate references",
            "duration_sec": 5,
            "reference_video_asset_ids": ["a", "a"],
        },
        headers=auth_context["headers"],
    )

    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "VIDEO_GEN_REFERENCE_MEDIA_CONFLICT"
    assert conflict.json()["error"]["message"] == (
        "参考图与参考视频不能同时使用，请选择其中一种。"
    )
    assert too_many.status_code == 422
    assert duplicate.status_code == 422


@pytest.mark.parametrize(
    "aspect_ratio",
    sorted(VIDEO_GEN_ASPECT_RATIOS),
)
def test_video_gen_all_seven_aspect_ratios_reach_provider(
    aspect_ratio: str,
) -> None:
    from app.providers.video.apimart import APIMartVideoProvider
    from app.workers import video_gen

    request = VideoGenerateRequest.model_validate(
        {
            "video_mode": "video_gen",
            "prompt": "A product reveal",
            "duration_sec": 5,
            "aspect_ratio": aspect_ratio,
        }
    )
    ctx = video_gen.VideoGenContext(
        task_id="ratio-contract",
        tenant_id="tenant-ratio",
        db=None,
        store=None,
        storage=_Storage(),
        task=SimpleNamespace(topic=request.topic),
        reference_assets=[],
        duration_sec=5,
        resolution="720p",
        aspect_ratio=request.aspect_ratio,
        generate_audio=False,
        negative_prompt=None,
    )

    provider = APIMartVideoProvider(api_key="test-apimart-key")
    provider_size = video_gen._provider_payload(ctx, provider)["size"]
    assert provider_size in provider.capabilities.supported_sizes
    assert provider_size == (
        provider.capabilities.automatic_size if aspect_ratio == "auto" else aspect_ratio
    )


def test_video_gen_provider_payload_uses_declared_automatic_size() -> None:
    from app.providers.base import VideoProviderCapabilities
    from app.workers import video_gen

    class _Provider:
        capabilities = VideoProviderCapabilities(
            supported_sizes=frozenset({"9:16", "provider-auto"}),
            automatic_size="provider-auto",
        )

    ctx = video_gen.VideoGenContext(
        task_id="automatic-size-contract",
        tenant_id="tenant-automatic-size",
        db=None,
        store=None,
        storage=_Storage(),
        task=SimpleNamespace(topic="A product reveal"),
        reference_assets=[],
        duration_sec=5,
        resolution="720p",
        aspect_ratio="auto",
        generate_audio=False,
        negative_prompt=None,
    )

    assert video_gen._provider_payload(ctx, _Provider())["size"] == "provider-auto"


def test_video_gen_schema_resolutions_all_have_quota_multipliers() -> None:
    schema_resolutions = set(
        get_args(VideoGenerateRequest.model_fields["resolution"].annotation)
    )

    assert schema_resolutions == set(_VIDEO_GEN_RESOLUTION_MULTIPLIERS)


def test_video_gen_1080p_quota_estimate_uses_resolution_multiplier(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        estimate = estimate_video_gen_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            duration_sec=15,
            resolution="1080p",
        )

    assert estimate.estimated_seconds == 15
    assert estimate.estimated_credits == Decimal("7500.00")
    assert estimate.reservation_units == 7500


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
            "apply_visible_label": True,
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
                    "negative_prompt": None,
                    "aspect_ratio": "auto",
                    "generate_audio": False,
                    "bgm": {"source": "upload", "asset_id": "asset-bgm-a"},
                    "apply_visible_label": True,
                    "tenant_id": auth_context["tenant_id"],
                    "video_task_id": task_id,
                }
            ],
            "task_id": task_id,
            "queue": "video",
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
        assert task.params["apply_visible_label"] is True
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
        assert usage.provider == "apimart"
        assert usage.model == "doubao-seedance-2.0"
        assert usage.status == "reserved"
        assert usage.quantity == Decimal("15")
        assert usage.credits == Decimal("1500.00")
        subscription = _subscription(db, auth_context["tenant_id"])
        assert subscription.quota_credits_reserved == 1500
        assert subscription.quota_credits_used == 0


def test_create_video_gen_preserves_custom_provider_params_and_four_second_quota(
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

    monkeypatch.setattr(videos_route, "generate_video_gen_task", _Task, raising=False)
    with auth_db() as db:
        _reset_subscription_quota(db, auth_context["tenant_id"])
        _seed_image_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="asset-param-ref",
        )
        db.commit()

    response = TestClient(app).post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "A cinematic watch reveal",
            "negative_prompt": "warped hands, duplicate watches",
            "reference_image_asset_ids": ["asset-param-ref"],
            "duration_sec": 4,
            "resolution": "480p",
            "aspect_ratio": "21:9",
            "generate_audio": True,
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 202
    task_id = response.json()["data"]["task_id"]
    worker_params = enqueued[0]["args"][0]
    assert worker_params["negative_prompt"] == "warped hands, duplicate watches"
    assert worker_params["aspect_ratio"] == "21:9"
    assert worker_params["generate_audio"] is True
    assert worker_params["duration_sec"] == 4

    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task is not None
        assert task.aspect_ratio == "21:9"
        assert task.params["negative_prompt"] == "warped hands, duplicate watches"
        assert task.params["aspect_ratio"] == "21:9"
        assert task.params["generate_audio"] is True
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        assert usage is not None
        assert usage.quantity == Decimal("4")
        assert usage.credits == Decimal("400.00")
        assert _subscription(db, auth_context["tenant_id"]).quota_credits_reserved == 400


@pytest.mark.parametrize("duration_sec", [3, 16, 20])
def test_create_video_gen_rejects_out_of_range_duration_without_side_effects(
    duration_sec: int,
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

    monkeypatch.setattr(videos_route, "generate_video_gen_task", _Task, raising=False)
    with auth_db() as db:
        _reset_subscription_quota(db, auth_context["tenant_id"])
        initial_task_ids = set(db.scalars(select(VideoTask.id)))
        initial_usage_ids = set(db.scalars(select(UsageRecord.id)))
        db.commit()

    response = TestClient(app).post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "An invalid duration must stop at the API boundary",
            "duration_sec": duration_sec,
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert enqueued == []
    with auth_db() as db:
        assert set(db.scalars(select(VideoTask.id))) == initial_task_ids
        assert set(db.scalars(select(UsageRecord.id))) == initial_usage_ids
        assert _subscription(db, auth_context["tenant_id"]).quota_credits_reserved == 0


@pytest.mark.parametrize("include_topic", [False, True])
def test_create_video_gen_reports_friendly_prompt_limit_without_side_effects(
    include_topic: bool,
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

    monkeypatch.setattr(videos_route, "generate_video_gen_task", _Task, raising=False)
    with auth_db() as db:
        _reset_subscription_quota(db, auth_context["tenant_id"])
        initial_task_ids = set(db.scalars(select(VideoTask.id)))
        initial_usage_ids = set(db.scalars(select(UsageRecord.id)))
        db.commit()

    long_prompt = "提" * 2001
    payload = {
        "video_mode": "video_gen",
        "prompt": long_prompt,
        "duration_sec": 5,
    }
    if include_topic:
        payload["topic"] = long_prompt
    response = TestClient(app).post(
        "/api/v1/videos",
        json=payload,
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VIDEO_GEN_PROMPT_TOO_LONG"
    assert response.json()["error"]["message"] == "提示词输入最大上限为 2000 字"
    assert enqueued == []
    with auth_db() as db:
        assert set(db.scalars(select(VideoTask.id))) == initial_task_ids
        assert set(db.scalars(select(UsageRecord.id))) == initial_usage_ids
        assert _subscription(db, auth_context["tenant_id"]).quota_credits_reserved == 0


def test_create_video_gen_allows_t2v_without_reference_images(
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
        db.commit()

    resp = TestClient(app).post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "A clean text to video product reveal",
            "duration_sec": 5,
            "resolution": "720p",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    task_id = resp.json()["data"]["id"]
    assert enqueued[0]["args"][0]["reference_image_asset_ids"] == []
    assert enqueued[0]["args"][0]["aspect_ratio"] == "9:16"
    assert enqueued[0]["args"][0]["generate_audio"] is False
    assert enqueued[0]["task_id"] == task_id
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task is not None
        assert task.params["reference_image_asset_ids"] == []
        roles = list(db.scalars(select(TaskAsset).where(TaskAsset.video_task_id == task_id)))
        assert roles == []
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        assert usage is not None
        assert usage.provider == "apimart"
        assert usage.model == "doubao-seedance-2.0"


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


def test_create_video_gen_links_video_references_and_validates_database_total_duration(
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

    monkeypatch.setattr(videos_route, "generate_video_gen_task", _Task, raising=False)
    with auth_db() as db:
        _reset_subscription_quota(db, auth_context["tenant_id"])
        for index, duration_ms in enumerate((2_000, 3_000, 4_000), start=1):
            _seed_video_reference_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"video-ref-{index}",
                duration_ms=duration_ms,
            )
        _seed_video_reference_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="video-too-short",
            duration_ms=1_800,
        )
        for index in range(1, 4):
            _seed_video_reference_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"video-total-too-long-{index}",
                duration_ms=5_200,
            )
        db.commit()

    valid = TestClient(app).post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "Use three motion references",
            "duration_sec": 5,
            "reference_video_asset_ids": [
                "video-ref-1",
                "video-ref-2",
                "video-ref-3",
            ],
        },
        headers=auth_context["headers"],
    )
    invalid = TestClient(app).post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "Reference is not strictly over the lower bound",
            "duration_sec": 5,
            "reference_video_asset_ids": ["video-too-short"],
        },
        headers=auth_context["headers"],
    )
    invalid_total_too_long = TestClient(app).post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "Combined references exceed the upper bound",
            "duration_sec": 5,
            "reference_video_asset_ids": [
                "video-total-too-long-1",
                "video-total-too-long-2",
                "video-total-too-long-3",
            ],
        },
        headers=auth_context["headers"],
    )

    assert valid.status_code == 202
    task_id = valid.json()["data"]["task_id"]
    assert enqueued[0]["args"][0]["reference_video_asset_ids"] == [
        "video-ref-1",
        "video-ref-2",
        "video-ref-3",
    ]
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task is not None
        assert task.params["reference_video_asset_ids"] == [
            "video-ref-1",
            "video-ref-2",
            "video-ref-3",
        ]
        roles = list(
            db.scalars(select(TaskAsset.role).where(TaskAsset.video_task_id == task_id))
        )
        assert roles == [
            "input_reference_video",
            "input_reference_video",
            "input_reference_video",
        ]

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VIDEO_GEN_REFERENCE_TOTAL_DURATION_INVALID"
    assert invalid.json()["error"]["message"] == (
        "参考视频合计时长需大于 1.8 秒且小于 15.2 秒。"
    )
    assert invalid_total_too_long.status_code == 422
    assert invalid_total_too_long.json()["error"]["code"] == (
        "VIDEO_GEN_REFERENCE_TOTAL_DURATION_INVALID"
    )


def test_video_gen_video_reference_is_tenant_safe_and_invalid_request_has_no_side_effects(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)
    other_resp = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": "video-ref-other",
            "tenant_name": "Video Ref Other",
            "email": "video-ref-other@example.com",
            "password": "unit-pass-123",
        },
    )
    other_tenant_id = other_resp.json()["data"]["tenant"]["id"]
    with auth_db() as db:
        _reset_subscription_quota(db, auth_context["tenant_id"])
        _seed_video_reference_asset(
            db,
            tenant_id=other_tenant_id,
            asset_id="cross-tenant-video-ref",
            duration_ms=3_000,
        )
        wrong_purpose = _seed_video_reference_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="wrong-purpose-video-ref",
            duration_ms=3_000,
        )
        wrong_purpose.metadata_ = {"purpose": "avatar_source"}
        initial_tasks = set(db.scalars(select(VideoTask.id)))
        initial_usage = set(db.scalars(select(UsageRecord.id)))
        db.commit()

    response = client.post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "Cross tenant references are hidden",
            "duration_sec": 5,
            "reference_video_asset_ids": ["cross-tenant-video-ref"],
        },
        headers=auth_context["headers"],
    )
    wrong_purpose_response = client.post(
        "/api/v1/videos",
        json={
            "video_mode": "video_gen",
            "prompt": "Wrong-purpose assets are hidden",
            "duration_sec": 5,
            "reference_video_asset_ids": ["wrong-purpose-video-ref"],
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REFERENCE_VIDEO_NOT_FOUND"
    assert wrong_purpose_response.status_code == 404
    assert wrong_purpose_response.json()["error"]["code"] == "REFERENCE_VIDEO_NOT_FOUND"
    with auth_db() as db:
        assert set(db.scalars(select(VideoTask.id))) == initial_tasks
        assert set(db.scalars(select(UsageRecord.id))) == initial_usage
        assert _subscription(db, auth_context["tenant_id"]).quota_credits_reserved == 0


def test_video_gen_provider_payload_uses_public_video_urls_exclusively(monkeypatch) -> None:
    from app.providers.video.apimart import APIMartVideoProvider
    from app.workers import video_gen

    storage = _Storage()
    video_asset = SimpleNamespace(
        storage_key="tenants/tenant-v2v/uploads/ref.mp4",
    )
    ctx = video_gen.VideoGenContext(
        task_id="v2v-payload",
        tenant_id="tenant-v2v",
        db=None,
        store=None,
        storage=storage,
        task=SimpleNamespace(topic="A product follows reference motion"),
        reference_assets=[SimpleNamespace(storage_key="should-not-be-used.png")],
        reference_video_assets=[video_asset],
        duration_sec=5,
        resolution="720p",
        aspect_ratio="auto",
        generate_audio=False,
        negative_prompt=None,
    )
    monkeypatch.setattr(video_gen.settings, "engine_s3_presign_ttl", 60)
    monkeypatch.setattr(video_gen.settings, "engine_apimart_video_timeout_seconds", 300)

    payload = video_gen._provider_payload(
        ctx,
        APIMartVideoProvider(api_key="test-apimart-key"),
    )

    assert payload["video_urls"] == [
        "https://storage.test/tenants/tenant-v2v/uploads/ref.mp4?ttl=7200"
    ]
    assert "image_urls" not in payload


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
        ref_storage_key = ref.storage_key
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
                "negative_prompt": "warped hands, duplicate watches",
                "aspect_ratio": "21:9",
                "generate_audio": True,
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
        from app.providers.video.apimart import APIMartVideoProvider

        provider = APIMartVideoProvider(api_key="test-apimart-key")
        provider_payloads.append(video_gen._provider_payload(ctx, provider))
        ctx.provider_cost_cents = 511
        return b"MP4"

    def fake_label_artifact_bytes(
        content: bytes,
        *,
        kind: str,
        settings: LabelSettings,
        meta: SyntheticLabelMeta,
        suffix: str | None = None,
        visible: bool = True,
    ) -> bytes:
        label_calls.append(
            {
                "kind": kind,
                "settings": settings,
                "meta": meta,
                "suffix": suffix,
                "visible": visible,
            }
        )
        return content + b"|LABEL"

    monkeypatch.setattr(video_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(video_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(video_gen, "build_progress_store", lambda _url: _MemProgressStore())
    monkeypatch.setattr(video_gen, "_generate_seedance_mini_video", fake_generate)
    monkeypatch.setattr(video_gen, "label_artifact_bytes", fake_label_artifact_bytes)
    monkeypatch.setattr(video_gen.settings, "engine_s3_presign_ttl", 60)

    result = video_gen.run_video_gen_pipeline(
        tenant_id=auth_context["tenant_id"],
        task_id=task_id,
    )

    output_key = f"tenants/{auth_context['tenant_id']}/videos/{task_id}/final.mp4"
    assert storage.objects[output_key] == (b"MP4|LABEL", "video/mp4")
    assert result["storage_key"] == output_key
    assert provider_payloads[0]["model"] == "doubao-seedance-2.0"
    assert provider_payloads[0]["duration"] == 5
    assert provider_payloads[0]["resolution"] == "720p"
    assert provider_payloads[0]["negative_prompt"] == "warped hands, duplicate watches"
    assert provider_payloads[0]["size"] == "21:9"
    assert provider_payloads[0]["generate_audio"] is True
    assert provider_payloads[0]["image_urls"] == [
        f"https://storage.test/{ref_storage_key}?ttl=7200"
    ]
    assert "reference_images" not in provider_payloads[0]
    assert "fps" not in provider_payloads[0]
    assert label_calls[0]["kind"] == "video"
    assert label_calls[0]["suffix"] == ".mp4"
    assert label_calls[0]["meta"].content_id == task_id
    assert label_calls[0]["visible"] is False
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task.status == "done"
        assert task.storage_key == output_key
        assert task.content_type == "video/mp4"
        assert task.progress == 100
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        assert usage.status == "settled"
        assert usage.cost_cents == 511
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


def test_video_gen_compensation_commit_failure_is_eventually_recovered(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services.task_recovery import recover_orphaned_image_queue_tasks
    from app.workers import video_gen

    storage = _Storage()
    task_id = "video-gen-compensation-commit-failure"
    batch_id = "video-gen-recovery-batch"
    with auth_db() as db:
        subscription = _reset_subscription_quota(db, auth_context["tenant_id"])
        batch = BatchJob(
            id=batch_id,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            kind="prompt_set",
            status="running",
            total=2,
            common_params={},
        )
        db.add(batch)
        task = _add_video_gen_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            task_id=task_id,
            params={"duration_sec": 5, "resolution": "720p"},
        )
        task.batch_id = batch.id
        db.add(
            VideoTask(
                id="video-gen-recovery-batch-done",
                tenant_id=auth_context["tenant_id"],
                created_by_user_id=auth_context["user_id"],
                batch_id=batch.id,
                status="done",
                mode="video_gen",
                video_mode="video_gen",
                params={"batch_row_index": 1},
            )
        )
        _add_reserved_video_gen_usage(
            db,
            tenant_id=auth_context["tenant_id"],
            subscription=subscription,
            task_id=task_id,
        )
        db.commit()

    worker_db = auth_db()
    real_commit = worker_db.commit
    commit_calls = 0

    def fail_compensation_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("injected compensation commit failure")
        real_commit()

    def fail_provider(_ctx) -> bytes:
        raise RuntimeError("provider generation failed")

    monkeypatch.setattr(worker_db, "commit", fail_compensation_commit)
    monkeypatch.setattr(video_gen, "SessionLocal", lambda: worker_db)
    monkeypatch.setattr(video_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(video_gen, "build_progress_store", lambda _url: _MemProgressStore())
    monkeypatch.setattr(video_gen, "_generate_seedance_mini_video", fail_provider)

    with pytest.raises(RuntimeError, match="injected compensation commit failure"):
        video_gen.run_video_gen_pipeline(
            tenant_id=auth_context["tenant_id"],
            task_id=task_id,
        )

    assert commit_calls == 2
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        subscription = _subscription(db, auth_context["tenant_id"])
        assert task is not None
        assert task.status == "running"
        assert usage is not None
        assert usage.status == "reserved"
        assert subscription.quota_credits_reserved == 10

    recovery_store = _MemProgressStore()
    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=datetime.now(UTC) + timedelta(seconds=1901),
        stale_after_seconds=1800,
        progress_store=recovery_store,
    )
    repeated = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=datetime.now(UTC) + timedelta(seconds=1901),
        stale_after_seconds=1800,
    )

    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        subscription = _subscription(db, auth_context["tenant_id"])
        batch = db.get(BatchJob, batch_id)

    assert task is not None
    assert task.status == "failed"
    assert task.error_code == "VIDEO_GEN_FAILED"
    assert task.error_message == "Video generation worker stopped before completion."
    assert usage is not None
    assert usage.status == "released"
    assert subscription.quota_credits_reserved == 0
    assert batch is not None
    assert batch.status == "partial_failed"
    assert batch.succeeded == 1
    assert batch.failed == 1
    assert result.video_gen_tasks == 1
    assert repeated.video_gen_tasks == 0
    assert recovery_store.snapshots[f'{auth_context["tenant_id"]}:{task_id}'] == {
        "status": "failed",
        "stage": "failed",
        "error": "Video generation worker stopped before completion.",
        "error_code": "VIDEO_GEN_FAILED",
        "error_message": "Video generation worker stopped before completion.",
    }


def test_classify_video_error_uses_provider_and_connection_signals() -> None:
    from app.providers.video.apimart import APIMartVideoProviderError
    from app.workers.video_gen import classify_video_error

    assert (
        classify_video_error(
            APIMartVideoProviderError("payment required", status_code=402)
        )
        == "VIDEO_INSUFFICIENT_BALANCE"
    )
    assert (
        classify_video_error(
            APIMartVideoProviderError("video task timed out", error_type="timeout")
        )
        == "VIDEO_TIMEOUT"
    )
    assert (
        classify_video_error(requests.exceptions.ConnectionError("network down"))
        == "VIDEO_CONNECTION_ERROR"
    )
    assert classify_video_error(RuntimeError("unexpected provider failure")) == "VIDEO_GEN_FAILED"


def test_classify_video_error_maps_real_apimart_reference_moderation_message_only_for_v2v(
) -> None:
    from app.providers.video.apimart import APIMartVideoProviderError
    from app.workers.video_gen import classify_video_error

    error = APIMartVideoProviderError(
        "Your current call may be flagged as containing prohibited words or images.",
        error_type="task_failed",
        usage_result={"credits": Decimal("0"), "cost_cents": 0},
    )

    assert classify_video_error(error) == "VIDEO_GEN_FAILED"
    assert (
        classify_video_error(error, has_reference_video=True)
        == "VIDEO_REFERENCE_CONTENT_REJECTED"
    )


def test_classify_video_error_checks_wrapped_causes() -> None:
    from app.providers.video.apimart import APIMartVideoProviderError
    from app.workers.video_gen import classify_video_error

    inner = APIMartVideoProviderError("video task timed out", error_type="timeout")
    outer = RuntimeError("wrapped provider error")
    outer.__cause__ = inner

    assert classify_video_error(outer) == "VIDEO_TIMEOUT"


def test_classify_video_error_handles_cyclic_causes() -> None:
    from app.workers.video_gen import classify_video_error

    first = RuntimeError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first

    assert classify_video_error(first) == "VIDEO_GEN_FAILED"


def test_classify_video_error_still_detects_balance_error_in_cause_chain() -> None:
    from app.providers.video.apimart import APIMartVideoProviderError
    from app.workers.video_gen import classify_video_error

    inner = APIMartVideoProviderError("payment required", status_code=402)
    middle = RuntimeError("middle")
    outer = RuntimeError("outer")
    middle.__cause__ = inner
    outer.__cause__ = middle

    assert classify_video_error(outer) == "VIDEO_INSUFFICIENT_BALANCE"


def test_video_gen_pipeline_failure_persists_classified_provider_error_code(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.providers.video.apimart import APIMartVideoProviderError
    from app.workers import video_gen

    storage = _Storage()
    store = _MemProgressStore()
    task_id = "video-gen-provider-402"
    with auth_db() as db:
        subscription = _reset_subscription_quota(db, auth_context["tenant_id"])
        _add_video_gen_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            task_id=task_id,
            params={"duration_sec": 5, "resolution": "720p"},
        )
        _add_reserved_video_gen_usage(
            db,
            tenant_id=auth_context["tenant_id"],
            subscription=subscription,
            task_id=task_id,
        )
        db.commit()

    def fail_with_provider_402(_ctx):
        raise APIMartVideoProviderError("payment required", status_code=402)

    monkeypatch.setattr(video_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(video_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(video_gen, "build_progress_store", lambda _url: store)
    monkeypatch.setattr(video_gen, "_generate_seedance_mini_video", fail_with_provider_402)

    with pytest.raises(APIMartVideoProviderError, match="payment required"):
        video_gen.run_video_gen_pipeline(
            tenant_id=auth_context["tenant_id"],
            task_id=task_id,
        )

    scoped_id = f"{auth_context['tenant_id']}:{task_id}"
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        subscription = _subscription(db, auth_context["tenant_id"])

    assert task is not None
    assert task.status == "failed"
    assert task.error_code == "VIDEO_INSUFFICIENT_BALANCE"
    assert task.error_message == "payment required"
    assert usage is not None
    assert usage.status == "released"
    assert subscription.quota_credits_reserved == 0
    assert store.snapshots[scoped_id]["status"] == "failed"
    assert store.snapshots[scoped_id]["error_code"] == "VIDEO_INSUFFICIENT_BALANCE"
    assert store.snapshots[scoped_id]["error_message"] == "payment required"


def test_video_gen_moderation_rejection_is_friendly_and_releases_reserved_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.providers.video.apimart import APIMartVideoProviderError
    from app.workers import video_gen

    storage = _Storage()
    store = _MemProgressStore()
    task_id = "video-gen-v2v-moderation"
    with auth_db() as db:
        subscription = _reset_subscription_quota(db, auth_context["tenant_id"])
        _add_video_gen_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            task_id=task_id,
            params={
                "reference_video_asset_ids": ["video-ref-person"],
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

    def reject_reference(_ctx):
        raise APIMartVideoProviderError(
            "reference video rejected by content moderation",
            error_type="task_failed",
            usage_result={"credits": Decimal("0"), "cost_cents": 0},
        )

    monkeypatch.setattr(video_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(video_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(video_gen, "build_progress_store", lambda _url: store)
    monkeypatch.setattr(video_gen, "_generate_seedance_mini_video", reject_reference)

    with pytest.raises(APIMartVideoProviderError):
        video_gen.run_video_gen_pipeline(
            tenant_id=auth_context["tenant_id"],
            task_id=task_id,
        )

    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        usage = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        subscription = _subscription(db, auth_context["tenant_id"])

    assert task is not None
    assert task.status == "failed"
    assert task.error_code == "VIDEO_REFERENCE_CONTENT_REJECTED"
    assert task.error_message == "参考视频未通过内容审核，请确认视频不含真人或违规内容后重试。"
    assert usage is not None
    assert usage.status == "released"
    assert usage.cost_cents == 0
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
        assert usage.cost_cents == 497
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
