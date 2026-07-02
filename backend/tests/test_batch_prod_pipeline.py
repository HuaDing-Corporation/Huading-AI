from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_object_storage
from app.db.models import Asset, BatchJob, Subscription, TaskAsset, UsageRecord, VideoTask, Voice
from app.main import app


class _FakeStorage:
    bucket = "batch-test-bucket"

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


def _subscription(db, tenant_id: str) -> Subscription:
    subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    assert subscription is not None
    return subscription


def _set_quota(db, tenant_id: str, *, total: int) -> None:
    subscription = _subscription(db, tenant_id)
    subscription.quota_credits_total = total
    subscription.quota_credits_used = 0
    subscription.quota_credits_reserved = 0


def _seed_voice(db) -> Voice:
    voice = Voice(
        id="voice-batch",
        provider="edge-tts",
        voice_code="zh-CN-XiaoxiaoNeural",
        display_name="Batch Voice",
        gender="female",
        is_active=True,
    )
    db.add(voice)
    return voice


def _seed_image_asset(db, *, tenant_id: str, asset_id: str = "asset-batch-product") -> Asset:
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


def _seed_audio_asset(db, *, tenant_id: str, asset_id: str = "asset-batch-bgm") -> Asset:
    asset = Asset(
        id=asset_id,
        tenant_id=tenant_id,
        type="audio",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/{asset_id}.mp3",
        mime_type="audio/mpeg",
        size_bytes=8,
        duration_ms=3000,
        status="ready",
    )
    db.add(asset)
    return asset


def _stub_batch_tasks(monkeypatch):
    from app.api.v1.routes import batches as batches_route

    seedance_calls: list[dict[str, Any]] = []
    video_calls: list[dict[str, Any]] = []

    class _SeedanceTask:
        @staticmethod
        def apply_async(*, args: list[dict[str, Any]], task_id: str, queue: str):
            seedance_calls.append({"args": args, "task_id": task_id, "queue": queue})

            class _Result:
                status = "queued"

            return _Result()

    class _VideoTask:
        @staticmethod
        def apply_async(*, args: list[dict[str, Any]], task_id: str, queue: str):
            video_calls.append({"args": args, "task_id": task_id, "queue": queue})

            class _Result:
                status = "queued"

            return _Result()

    monkeypatch.setattr(batches_route, "generate_seedance_i2v_task", _SeedanceTask)
    monkeypatch.setattr(batches_route, "generate_video_gen_task", _VideoTask)
    return seedance_calls, video_calls


def test_batch_estimate_prompt_set_sums_video_gen_quota(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        _set_quota(db, auth_context["tenant_id"], total=1000)
        db.commit()

    resp = TestClient(app).post(
        "/api/v1/batches/estimate",
        json={
            "kind": "prompt_set",
            "rows": [{"prompt": "shot one"}, {"prompt": "shot two"}],
            "common": {
                "video_mode": "video_gen",
                "duration_sec": 10,
                "resolution": "720p",
                "reference_image_asset_ids": [],
                "apply_visible_label": True,
            },
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data == {
        "total_rows": 2,
        "per_row_credits": 30,
        "total_credits": 60,
        "insufficient": False,
        "balance_credits": 1000,
    }


def test_batch_create_prompt_set_fans_out_video_gen_tasks_on_video_queue(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    seedance_calls, video_calls = _stub_batch_tasks(monkeypatch)
    with auth_db() as db:
        _set_quota(db, auth_context["tenant_id"], total=1000)
        ref = _seed_image_asset(db, tenant_id=auth_context["tenant_id"], asset_id="asset-ref")
        bgm = _seed_audio_asset(db, tenant_id=auth_context["tenant_id"])
        ref_id = ref.id
        bgm_id = bgm.id
        db.commit()

    resp = TestClient(app).post(
        "/api/v1/batches",
        json={
            "kind": "prompt_set",
            "rows": [{"prompt": "first product reveal"}, {"prompt": "second product reveal"}],
            "common": {
                "video_mode": "video_gen",
                "duration_sec": 5,
                "resolution": "1080p",
                "reference_image_asset_ids": [ref_id],
                "bgm": {"source": "upload", "asset_id": bgm_id},
                "apply_visible_label": True,
            },
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert len(data["task_ids"]) == 2
    assert seedance_calls == []
    assert len(video_calls) == 2
    assert {call["queue"] for call in video_calls} == {"video"}
    assert all(call["args"][0]["apply_visible_label"] is True for call in video_calls)
    assert all(call["args"][0]["batch_id"] == data["batch_id"] for call in video_calls)

    with auth_db() as db:
        batch = db.get(BatchJob, data["batch_id"])
        assert batch.kind == "prompt_set"
        assert batch.status == "running"
        assert batch.total == 2
        tasks = list(db.scalars(select(VideoTask).where(VideoTask.batch_id == batch.id)))
        assert {task.video_mode for task in tasks} == {"video_gen"}
        assert {task.params["batch_row_index"] for task in tasks} == {0, 1}
        assert all(task.params["apply_visible_label"] is True for task in tasks)
        assert all(task.params["resolution"] == "1080p" for task in tasks)
        usage_count = db.scalar(
            select(func.count()).select_from(UsageRecord).where(UsageRecord.status == "reserved")
        )
        assert usage_count == 2
        assert _subscription(db, auth_context["tenant_id"]).quota_credits_reserved == 46
        roles = {
            item.role
            for item in db.scalars(
                select(TaskAsset).where(TaskAsset.video_task_id.in_(data["task_ids"]))
            )
        }
        assert roles == {"input_reference_image", "input_bgm"}


def test_batch_create_insufficient_credits_rejects_without_partial_rows(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _stub_batch_tasks(monkeypatch)
    with auth_db() as db:
        _set_quota(db, auth_context["tenant_id"], total=10)
        db.commit()

    resp = TestClient(app).post(
        "/api/v1/batches",
        json={
            "kind": "prompt_set",
            "rows": [{"prompt": "expensive one"}, {"prompt": "expensive two"}],
            "common": {
                "video_mode": "video_gen",
                "duration_sec": 15,
                "resolution": "1080p",
            },
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INSUFFICIENT_CREDITS"
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BatchJob)) == 0
        assert db.scalar(select(func.count()).select_from(VideoTask)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        assert _subscription(db, auth_context["tenant_id"]).quota_credits_reserved == 0


def test_batch_create_ecom_table_download_failure_marks_one_row_failed_not_whole_batch(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    seedance_calls, video_calls = _stub_batch_tasks(monkeypatch)
    storage = _FakeStorage()
    with auth_db() as db:
        _set_quota(db, auth_context["tenant_id"], total=1000)
        _seed_voice(db)
        asset = _seed_image_asset(db, tenant_id=auth_context["tenant_id"])
        asset_id = asset.id
        db.commit()

    def fake_download(*_args, **_kwargs):
        raise RuntimeError("download blocked")

    from app.api.v1.routes import batches as batches_route

    monkeypatch.setattr(batches_route, "download_image_url_to_asset", fake_download)
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/batches",
            json={
                "kind": "ecom_table",
                "rows": [
                    {
                        "product_name": "bad product",
                        "selling_points": "bad image",
                        "image_url": "https://images.example/bad.png",
                    },
                    {
                        "product_name": "good product",
                        "selling_points": "good image",
                        "image_asset_id": asset_id,
                    },
                ],
                "common": {
                    "video_mode": "seedance_i2v",
                    "voice_id": "voice-batch",
                    "duration_sec": 10,
                    "apply_visible_label": True,
                },
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert len(data["task_ids"]) == 2
    assert video_calls == []
    assert len(seedance_calls) == 1
    assert seedance_calls[0]["queue"] == "video"
    assert seedance_calls[0]["args"][0]["apply_visible_label"] is True

    with auth_db() as db:
        batch = db.get(BatchJob, data["batch_id"])
        assert batch.status == "running"
        assert batch.total == 2
        assert batch.succeeded == 0
        assert batch.failed == 1
        failed_task = db.get(VideoTask, data["task_ids"][0])
        queued_task = db.get(VideoTask, data["task_ids"][1])
        assert failed_task.status == "failed"
        assert failed_task.error_code == "BATCH_IMAGE_DOWNLOAD_FAILED"
        assert queued_task.status == "queued"
        assert queued_task.params["image_key"].startswith("uploads/")
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 1
        assert _subscription(db, auth_context["tenant_id"]).quota_credits_reserved == 20


def test_batch_cancel_releases_queued_tasks_and_leaves_running_tasks(
    auth_context,
    auth_db,
) -> None:
    from app.services.batches import refresh_batch_job

    with auth_db() as db:
        _set_quota(db, auth_context["tenant_id"], total=1000)
        batch = BatchJob(
            id="batch-cancel",
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            kind="prompt_set",
            status="running",
            total=2,
            common_params={},
        )
        queued = VideoTask(
            id="batch-cancel-queued",
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            batch_id=batch.id,
            mode="video_gen",
            video_mode="video_gen",
            status="queued",
            params={"batch_row_index": 0},
        )
        running = VideoTask(
            id="batch-cancel-running",
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            batch_id=batch.id,
            mode="video_gen",
            video_mode="video_gen",
            status="running",
            params={"batch_row_index": 1},
        )
        db.add_all([batch, queued, running])
        db.flush()
        from app.services.quota import reserve_video_gen_quota

        reserve_video_gen_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            video_task_id=queued.id,
            duration_sec=5,
            resolution="720p",
        )
        reserve_video_gen_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            video_task_id=running.id,
            duration_sec=5,
            resolution="720p",
        )
        refresh_batch_job(db, batch_id=batch.id)
        db.commit()

    resp = TestClient(app).post(
        "/api/v1/batches/batch-cancel/cancel",
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "batch_id": "batch-cancel",
        "cancelled": 1,
        "running": 1,
    }
    with auth_db() as db:
        assert db.get(VideoTask, "batch-cancel-queued").status == "cancelled"
        assert db.get(VideoTask, "batch-cancel-running").status == "running"
        released = db.scalar(
            select(UsageRecord).where(UsageRecord.video_task_id == "batch-cancel-queued")
        )
        reserved = db.scalar(
            select(UsageRecord).where(UsageRecord.video_task_id == "batch-cancel-running")
        )
        assert released.status == "released"
        assert reserved.status == "reserved"
        assert _subscription(db, auth_context["tenant_id"]).quota_credits_reserved == 15
        assert db.get(BatchJob, "batch-cancel").status == "running"


def test_batch_refresh_aggregates_terminal_children_idempotently(
    auth_context,
    auth_db,
) -> None:
    from app.services.batches import refresh_batch_job

    with auth_db() as db:
        batch = BatchJob(
            id="batch-aggregate",
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            kind="prompt_set",
            status="running",
            total=3,
            common_params={},
        )
        db.add(batch)
        db.add_all(
            [
                VideoTask(
                    id="batch-agg-done",
                    tenant_id=auth_context["tenant_id"],
                    batch_id=batch.id,
                    status="done",
                    params={"batch_row_index": 0},
                ),
                VideoTask(
                    id="batch-agg-failed",
                    tenant_id=auth_context["tenant_id"],
                    batch_id=batch.id,
                    status="failed",
                    params={"batch_row_index": 1},
                ),
                VideoTask(
                    id="batch-agg-cancelled",
                    tenant_id=auth_context["tenant_id"],
                    batch_id=batch.id,
                    status="cancelled",
                    params={"batch_row_index": 2},
                ),
            ]
        )
        refresh_batch_job(db, batch_id=batch.id)
        refresh_batch_job(db, batch_id=batch.id)
        db.commit()

    with auth_db() as db:
        batch = db.get(BatchJob, "batch-aggregate")
        assert batch.succeeded == 1
        assert batch.failed == 2
        assert batch.status == "partial_failed"


def test_batch_detail_returns_child_rows_and_presigned_urls(
    auth_context,
    auth_db,
) -> None:
    storage = _FakeStorage()
    with auth_db() as db:
        batch = BatchJob(
            id="batch-detail",
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            kind="prompt_set",
            status="completed",
            total=1,
            succeeded=1,
            failed=0,
            common_params={"duration_sec": 5},
            created_at=datetime(2026, 7, 2, tzinfo=UTC),
        )
        task = VideoTask(
            id="batch-detail-task",
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            batch_id=batch.id,
            status="done",
            mode="video_gen",
            video_mode="video_gen",
            storage_key=f"tenants/{auth_context['tenant_id']}/videos/batch-detail-task/final.mp4",
            params={"batch_row_index": 0},
        )
        db.add_all([batch, task])
        db.commit()

    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).get(
            "/api/v1/batches/batch-detail",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["batch"]["id"] == "batch-detail"
    assert data["batch"]["status"] == "completed"
    assert data["tasks"] == [
        {
            "task_id": "batch-detail-task",
            "row_index": 0,
            "status": "done",
            "video_url": (
                f"https://storage.test/tenants/{auth_context['tenant_id']}/videos/"
                "batch-detail-task/final.mp4?ttl=3600"
            ),
            "error": None,
            "error_code": None,
            "error_message": None,
        }
    ]


def test_video_queue_routes_and_compose_worker_are_isolated() -> None:
    from app.workers.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert routes["app.workers.avatar_talk.generate_seedance_i2v"]["queue"] == "video"
    assert routes["app.workers.video_gen.generate"]["queue"] == "video"
    assert routes["app.workers.image_gen.generate"]["queue"] == "image"

    full_compose = Path("../infra/docker-compose.full.yml").read_text(encoding="utf-8")
    prod_compose = Path("../infra/docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "worker-video:" in full_compose
    assert "worker-video:" in prod_compose
    assert "-Q video" in full_compose
    assert "-Q video" in prod_compose
    assert "-Q default,avatar,image" in full_compose
    assert "-Q default,avatar,image" in prod_compose
