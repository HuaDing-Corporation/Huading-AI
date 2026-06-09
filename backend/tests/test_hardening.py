import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_progress_store, get_redis_client
from app.core.config import settings
from app.db.session import _connect_args_for
from app.main import app
from app.services.progress import build_progress_store
from app.services.storage.base import StorageKeyError
from app.services.storage.local import LocalObjectStorage
from app.workers import video_tasks

# ---------------- #003-FIX P2: readiness timeouts ----------------

def test_db_connect_args_postgres_gets_timeout() -> None:
    args = _connect_args_for("postgresql+psycopg://u:p@h/db", 7)
    assert args == {"connect_timeout": 7}


def test_db_connect_args_sqlite_empty() -> None:
    assert _connect_args_for("sqlite:///x.db", 7) == {}


def test_redis_client_has_socket_timeouts() -> None:
    client = get_redis_client()
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == settings.redis_socket_connect_timeout
    assert kwargs["socket_timeout"] == settings.redis_socket_timeout


def test_progress_store_redis_has_socket_timeouts() -> None:
    store = build_progress_store(settings.redis_url)
    kwargs = store._redis.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == settings.redis_socket_connect_timeout
    assert kwargs["socket_timeout"] == settings.redis_socket_timeout


# ---------------- #003-FIX P2: X-Request-ID on error responses ----------------

def test_request_id_header_on_validation_error(auth_context) -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={"topic": "x", "pipeline": "evil"},
        headers=auth_context["headers"],
    )
    assert resp.status_code == 422
    assert resp.headers.get("X-Request-ID")


def test_request_id_header_on_500(monkeypatch, auth_context) -> None:
    # Force the unhandled-exception (500) path, produced by ServerErrorMiddleware
    # outside RequestIdMiddleware — the header must still be present.
    def boom() -> None:
        raise RuntimeError("kaboom")

    app.dependency_overrides[get_progress_store] = boom
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.get("/api/v1/videos/some-id", headers=auth_context["headers"])
        assert resp.status_code == 500
        assert resp.headers.get("X-Request-ID")
    finally:
        app.dependency_overrides.pop(get_progress_store, None)


# ---------------- #003-FIX P3: storage key escape -> 400 ----------------

def test_storage_key_escape_returns_400(auth_context) -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/storage/objects",
        json={"key": "../escape.txt", "content": "x"},
        headers=auth_context["headers"],
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_STORAGE_KEY"


def test_local_storage_raises_storage_key_error(tmp_path) -> None:
    storage = LocalObjectStorage(str(tmp_path))
    with pytest.raises(StorageKeyError):
        storage.put_text("../x.txt", "y", content_type="text/plain")


# ---------------- #005-FIX P2: SSE timeout event ----------------

class _NoneStore:
    def read(self, task_id):  # always pending
        return None


def test_sse_emits_timeout_event(monkeypatch, auth_context) -> None:
    monkeypatch.setattr(settings, "sse_timeout_seconds", 1)
    app.dependency_overrides[get_progress_store] = lambda: _NoneStore()
    try:
        client = TestClient(app)
        from app.api.v1.routes import videos as videos_route

        videos_route._video_task_tenants["abc"] = auth_context["tenant_id"]
        resp = client.get("/api/v1/videos/abc/events", headers=auth_context["headers"])
        assert resp.status_code == 200
        assert "sse_timeout" in resp.text
    finally:
        videos_route._video_task_tenants.pop("abc", None)
        app.dependency_overrides.pop(get_progress_store, None)


# ---------------- #005-FIX P2: extra="forbid" ----------------

def test_unknown_field_rejected(auth_context) -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={"topic": "x", "output_path": "/etc/x"},
        headers=auth_context["headers"],
    )
    assert resp.status_code == 422


# ---------------- #005-FIX P2: output prefix whitelist ----------------

def test_safe_prefix_accepts_simple() -> None:
    assert video_tasks._safe_prefix("videos") == "videos"
    assert video_tasks._safe_prefix("tenant/videos") == "tenant/videos"


@pytest.mark.parametrize("bad", ["../x", "/abs", "a/../b", "a\\b", ""])
def test_safe_prefix_rejects_bad(bad: str) -> None:
    with pytest.raises(ValueError):
        video_tasks._safe_prefix(bad)
