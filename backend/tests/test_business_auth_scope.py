from pathlib import Path

from fastapi.testclient import TestClient

from app.api.deps import get_object_storage, get_progress_store
from app.api.v1.routes import videos as videos_route
from app.main import app
from app.services.progress import _KNOWN_FIELDS
from app.workers import video_tasks
from app.workers.celery_app import celery_app


class _MemProgressStore:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    def update(self, task_id: str, **fields) -> None:
        snapshot = self.data.get(task_id, {})
        snapshot.update({k: v for k, v in fields.items() if v is not None})
        snapshot["task_id"] = task_id
        self.data[task_id] = snapshot

    def read(self, task_id: str) -> dict | None:
        snapshot = self.data.get(task_id)
        if snapshot is None:
            return None
        return {k: v for k, v in snapshot.items() if k in _KNOWN_FIELDS}


class _FakeStorage:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def put_text(self, key: str, content: str, *, content_type: str) -> str:
        self.saved[key] = content_type
        return f"memory://{key}"


def _register(client: TestClient, slug: str) -> dict:
    resp = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": slug,
            "tenant_name": slug.title(),
            "email": "owner@example.com",
            "password": "secret-pass",
        },
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    token = data["token"]["access_token"]
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "tenant_id": data["tenant"]["id"],
    }


def test_anonymous_business_routes_are_401() -> None:
    client = TestClient(app)
    assert client.post("/api/v1/tasks/demo", json={"message": "x"}).status_code == 401
    assert client.post("/api/v1/videos", json={"topic": "x"}).status_code == 401
    assert client.post(
        "/api/v1/storage/objects",
        json={"key": "x.txt", "content": "x"},
    ).status_code == 401


def test_cross_tenant_task_id_is_not_disclosed(auth_db) -> None:
    client = TestClient(app)
    tenant_a = _register(client, "tenant-a")
    tenant_b = _register(client, "tenant-b")

    previous = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        created = client.post(
            "/api/v1/tasks/demo",
            json={"message": "x"},
            headers=tenant_a["headers"],
        )
        assert created.status_code == 202
        task_id = created.json()["data"]["task_id"]

        leaked = client.get(f"/api/v1/tasks/{task_id}", headers=tenant_b["headers"])
        assert leaked.status_code == 404
        assert leaked.json()["error"]["code"] == "TASK_NOT_FOUND"
    finally:
        celery_app.conf.task_always_eager = previous


def test_cross_tenant_video_task_id_is_not_disclosed(monkeypatch, tmp_path: Path, auth_db) -> None:
    client = TestClient(app)
    tenant_a = _register(client, "tenant-a")
    tenant_b = _register(client, "tenant-b")
    store = _MemProgressStore()

    async def fake_generate(params, progress_cb):
        out = tmp_path / "final.mp4"
        out.write_bytes(b"mp4")
        return {"video_path": str(out), "duration": 1.0, "file_size": 3}

    monkeypatch.setattr(video_tasks, "_generate_with_engine", fake_generate)
    monkeypatch.setattr(video_tasks, "build_progress_store", lambda url: store)
    monkeypatch.setattr(video_tasks, "create_object_storage", lambda settings: _FakeStorage())
    app.dependency_overrides[get_progress_store] = lambda: store

    previous = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        created = client.post(
            "/api/v1/videos",
            json={"topic": "x"},
            headers=tenant_a["headers"],
        )
        assert created.status_code == 202
        task_id = created.json()["data"]["task_id"]

        leaked = client.get(f"/api/v1/videos/{task_id}", headers=tenant_b["headers"])
        assert leaked.status_code == 404
        assert leaked.json()["error"]["code"] == "VIDEO_TASK_NOT_FOUND"
    finally:
        celery_app.conf.task_always_eager = previous
        app.dependency_overrides.pop(get_progress_store, None)
        videos_route._video_task_tenants.pop(task_id, None)


def test_storage_key_is_forced_under_current_tenant(auth_context) -> None:
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/storage/objects",
            json={"key": "asset.txt", "content": "x"},
            headers=auth_context["headers"],
        )
        assert resp.status_code == 200
        expected = f"tenants/{auth_context['tenant_id']}/asset.txt"
        assert expected in storage.saved

        cross = client.post(
            "/api/v1/storage/objects",
            json={"key": "tenants/other/asset.txt", "content": "x"},
            headers=auth_context["headers"],
        )
        assert cross.status_code == 400
        assert cross.json()["error"]["code"] == "INVALID_STORAGE_KEY"
    finally:
        app.dependency_overrides.pop(get_object_storage, None)
