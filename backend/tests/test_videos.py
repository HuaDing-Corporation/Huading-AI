from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_object_storage, get_progress_store
from app.main import app
from app.services.progress import _KNOWN_FIELDS
from app.workers import video_tasks
from app.workers.celery_app import celery_app


class _MemProgressStore:
    """In-memory stand-in for the Redis ProgressStore (same update/read contract)."""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    def update(self, task_id: str, **fields) -> None:
        snapshot = self.data.get(task_id, {})
        for name, value in fields.items():
            if value is not None:
                snapshot[name] = value
        snapshot["task_id"] = task_id
        self.data[task_id] = snapshot

    def read(self, task_id: str) -> dict | None:
        snapshot = self.data.get(task_id)
        if not snapshot:
            return None
        return {k: v for k, v in snapshot.items() if k in _KNOWN_FIELDS}


class _FakeStorage:
    def __init__(self) -> None:
        self.bucket = "test-bucket"
        self.saved: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.saved[key] = (content, content_type)
        return f"memory://{key}"

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        suffix = "&download=1" if download_filename else ""
        return f"https://storage.test/{key}?ttl={expires_in}{suffix}"


def test_video_generation_end_to_end_eager(
    monkeypatch, tmp_path: Path, auth_context, auth_db
) -> None:
    store = _MemProgressStore()
    storage = _FakeStorage()

    async def fake_generate(params, progress_cb):
        class _Evt:
            event_type = "processing_frame"
            progress = 0.5
            frame_current = 1
            frame_total = 2

        progress_cb(_Evt())
        out = tmp_path / "final.mp4"
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-bytes")
        return {"video_path": str(out), "duration": 2.0, "file_size": out.stat().st_size}

    monkeypatch.setattr(video_tasks, "_generate_with_engine", fake_generate)
    monkeypatch.setattr(video_tasks, "build_progress_store", lambda url: store)
    monkeypatch.setattr(video_tasks, "create_object_storage", lambda settings: storage)
    monkeypatch.setattr(video_tasks, "SessionLocal", auth_db)
    app.dependency_overrides[get_progress_store] = lambda: store
    app.dependency_overrides[get_object_storage] = lambda: storage

    previous = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/videos",
            json={"topic": "learn faster", "n_scenes": 2},
            headers=auth_context["headers"],
        )
        assert resp.status_code == 202
        task_id = resp.json()["data"]["task_id"]
        assert task_id

        status = client.get(f"/api/v1/videos/{task_id}", headers=auth_context["headers"])
        assert status.status_code == 200
        body = status.json()["data"]
        assert body["status"] == "done"
        assert body["progress"] == 100

        tenant_id = auth_context["tenant_id"]
        assert body["playback_url"].startswith(
            f"https://storage.test/tenants/{tenant_id}/videos/{task_id}/output.mp4"
        )
        assert body["download_url"].endswith("&download=1")

        key = f"tenants/{tenant_id}/videos/{task_id}/output.mp4"
        assert key in storage.saved
        assert storage.saved[key][1] == "video/mp4"

        listing = client.get("/api/v1/videos", headers=auth_context["headers"])
        assert listing.status_code == 200
        assert listing.json()["data"]["items"][0]["id"] == task_id
    finally:
        celery_app.conf.task_always_eager = previous
        app.dependency_overrides.pop(get_progress_store, None)
        app.dependency_overrides.pop(get_object_storage, None)


def test_unknown_task_is_not_disclosed(auth_context) -> None:
    store = _MemProgressStore()
    app.dependency_overrides[get_progress_store] = lambda: store
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/videos/does-not-exist", headers=auth_context["headers"])
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "VIDEO_TASK_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_progress_store, None)


def test_rejects_bad_frame_template(auth_context) -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={"topic": "x", "frame_template": "../../etc/passwd.html"},
        headers=auth_context["headers"],
    )
    assert resp.status_code == 422


def test_rejects_unknown_pipeline(auth_context) -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={"topic": "x", "pipeline": "evil"},
        headers=auth_context["headers"],
    )
    assert resp.status_code == 422


def test_output_path_is_not_accepted() -> None:
    from pydantic import ValidationError

    from app.schemas.videos import VideoGenerateRequest

    with pytest.raises(ValidationError):
        VideoGenerateRequest(topic="x", output_path="/etc/cron.d/x")


def test_storage_key_rejects_traversal() -> None:
    assert video_tasks._storage_key("abc-123", "tenant-1").endswith("/output.mp4")
    with pytest.raises(ValueError):
        video_tasks._storage_key("../../etc", "tenant-1")


def test_anonymous_video_rejected() -> None:
    client = TestClient(app)
    resp = client.post("/api/v1/videos", json={"topic": "x"})
    assert resp.status_code == 401


def test_build_engine_config_requires_credentials(monkeypatch) -> None:
    monkeypatch.setattr(video_tasks.settings, "engine_llm_api_key", "")
    monkeypatch.setattr(video_tasks.settings, "engine_llm_base_url", "")
    monkeypatch.setattr(video_tasks.settings, "engine_llm_model", "")
    with pytest.raises(RuntimeError):
        video_tasks._build_engine_config({})
