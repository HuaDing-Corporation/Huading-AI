"""Seedance pipelines + worker routing + uploads (#M2-SEEDANCE-P2-BE).

Network, LLM, TTS and ffmpeg are mocked throughout — these tests cover the
orchestration, routing, validation, and tenant scoping.
"""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

_ENGINE_DIR = Path(__file__).resolve().parents[1] / "app" / "engine"
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_object_storage  # noqa: E402
from app.api.v1.routes import uploads as uploads_route  # noqa: E402
from app.core.exceptions import AppError  # noqa: E402
from app.db.models import VideoTask  # noqa: E402
from app.engine import EngineConfig, seedance_pipeline  # noqa: E402
from app.main import app  # noqa: E402
from app.workers import video_tasks  # noqa: E402


def _cfg(**overrides) -> EngineConfig:
    base = {
        "llm_api_key": "llm-k",
        "llm_base_url": "https://llm.example",
        "llm_model": "m",
        "seedance_api_key": "sd-k",
    }
    base.update(overrides)
    return EngineConfig(**base)


@pytest.fixture
def piped(monkeypatch, tmp_path):
    """Mock every external effect of the pipeline; record calls."""
    calls = {"seedance": [], "tts": [], "mux": [], "concat": None}

    async def fake_plan(cfg, topic, n_scenes):
        return [
            seedance_pipeline.Scene(narration=f"旁白{i}", video_prompt=f"画面{i}")
            for i in range(n_scenes)
        ]

    def fake_seedance(cfg, prompt, *, image=None, image_path=None, image_role=None,
                      save_path=None, **params):
        calls["seedance"].append(
            {"prompt": prompt, "image": image, "image_path": image_path,
             "image_role": image_role, **params}
        )
        Path(save_path).write_bytes(b"CLIP")
        return None

    async def fake_tts(narration, voice, speed, out_path):
        calls["tts"].append({"narration": narration, "voice": voice, "speed": speed})
        Path(out_path).write_bytes(b"MP3")
        return out_path

    def fake_mux(video, audio, out):
        calls["mux"].append((video, audio))
        Path(out).write_bytes(b"SEGMENT")
        return out

    def fake_concat(segments, output, bgm, vol):
        calls["concat"] = {"segments": list(segments), "bgm": bgm}
        Path(output).write_bytes(b"FINAL-MP4")
        return output

    monkeypatch.setattr(seedance_pipeline, "_plan_scenes", fake_plan)
    monkeypatch.setattr(seedance_pipeline, "generate_seedance_video", fake_seedance)
    monkeypatch.setattr(seedance_pipeline, "_tts", fake_tts)
    monkeypatch.setattr(seedance_pipeline, "_mux_scene", fake_mux)
    monkeypatch.setattr(seedance_pipeline, "_concat_with_bgm", fake_concat)
    monkeypatch.setattr(seedance_pipeline, "_probe_duration", lambda path: 10.0)
    return calls


@pytest.mark.asyncio
async def test_t2v_pipeline_flow(piped, tmp_path):
    events = []
    result = await seedance_pipeline.run_seedance_pipeline(
        _cfg(), "如何挑选羊绒大衣", n_scenes=2,
        work_dir=str(tmp_path), progress_callback=events.append,
    )

    assert len(piped["seedance"]) == 2
    assert piped["seedance"][0]["image_path"] is None  # no image -> t2v
    assert len(piped["tts"]) == 2
    assert len(piped["concat"]["segments"]) == 2
    assert Path(result["video_path"]).read_bytes() == b"FINAL-MP4"
    assert result["duration"] == 10.0 and result["file_size"] > 0
    stages = [e.event_type for e in events]
    assert stages[0] == "planning_scenes" and stages[-1] == "completed"
    assert "generating_clip" in stages and "concatenating" in stages


@pytest.mark.asyncio
async def test_i2v_pipeline_passes_image(piped, tmp_path):
    img = tmp_path / "product.jpg"
    img.write_bytes(b"JPG")
    await seedance_pipeline.run_seedance_pipeline(
        _cfg(), "商品种草", image_path=str(img), n_scenes=2, work_dir=str(tmp_path),
    )
    # every clip is conditioned on the product image
    assert all(c["image_path"] == str(img) for c in piped["seedance"])
    assert all(c["image_role"] == "first_frame" for c in piped["seedance"])


@pytest.mark.asyncio
async def test_pipeline_rejects_empty_topic(piped):
    with pytest.raises(ValueError):
        await seedance_pipeline.run_seedance_pipeline(_cfg(), "   ")


# ---------------- worker routing ----------------


def test_worker_routes_seedance_modes(monkeypatch, tmp_path):
    seen = {}
    out = tmp_path / "final.mp4"
    out.write_bytes(b"V")

    async def fake_seedance(params, cb):
        seen["mode"] = params.get("video_mode")
        return {"video_path": str(out), "duration": 1.0, "file_size": 1}

    async def fail_engine(params, cb):  # pragma: no cover
        raise AssertionError("static engine path must not run for seedance modes")

    monkeypatch.setattr(video_tasks, "_generate_with_seedance", fake_seedance)
    monkeypatch.setattr(video_tasks, "_generate_with_engine", fail_engine)
    monkeypatch.setattr(video_tasks, "build_progress_store", lambda url: _Store())
    monkeypatch.setattr(
        video_tasks, "create_object_storage", lambda settings: _FakeStorage()
    )

    video_tasks.generate_video_task.apply(
        args=[{"topic": "t", "video_mode": "seedance_t2v", "tenant_id": "tn1"}]
    ).get()
    assert seen["mode"] == "seedance_t2v"


class _Store:
    def update(self, *a, **k):
        pass


class _FakeStorage:
    def __init__(self):
        self.objects = {}
        self.put_calls = 0

    def put_bytes(self, key, content, *, content_type):
        self.put_calls += 1
        self.objects[key] = content
        return f"memory://{key}"

    def get_bytes(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]


class _ChunkedUpload:
    content_type = "image/png"
    filename = "product.png"

    def __init__(self, size: int) -> None:
        self.remaining = size
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        if size <= 0:
            raise AssertionError("upload must be read in bounded chunks")
        self.read_sizes.append(size)
        if self.remaining <= 0:
            return b""
        chunk_size = min(size, self.remaining)
        self.remaining -= chunk_size
        return b"0" * chunk_size


def test_resolve_i2v_image_tenant_scoped(monkeypatch, tmp_path):
    storage = _FakeStorage()
    storage.objects["tenants/tn1/uploads/abc.jpg"] = b"IMG-BYTES"
    monkeypatch.setattr(video_tasks, "create_object_storage", lambda settings: storage)

    path = video_tasks._resolve_i2v_image({"tenant_id": "tn1", "image_key": "uploads/abc.jpg"})
    try:
        assert Path(path).read_bytes() == b"IMG-BYTES"
    finally:
        Path(path).unlink(missing_ok=True)

    with pytest.raises(ValueError):
        video_tasks._resolve_i2v_image({"tenant_id": "tn1", "image_key": "../../etc/passwd"})


@pytest.mark.parametrize(
    "bad_key",
    [
        "tenants/tn2/uploads/abc.jpg",
        "uploads/../abc.jpg",
        "uploads/nested/abc.jpg",
        "uploads/abc$.jpg",
        "uploads/abc.jpg/extra",
        "uploads/abc.gif",
        "uploads\\abc.jpg",
    ],
)
def test_resolve_i2v_image_rejects_non_whitelisted_keys(monkeypatch, bad_key):
    storage = _FakeStorage()
    monkeypatch.setattr(video_tasks, "create_object_storage", lambda settings: storage)

    with pytest.raises(ValueError, match="unsafe image_key"):
        video_tasks._resolve_i2v_image({"tenant_id": "tn1", "image_key": bad_key})
    assert storage.objects == {}


# ---------------- uploads endpoint ----------------


def test_upload_image_tenant_scoped(monkeypatch, auth_context):
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/uploads",
            files={"file": ("p.png", b"\x89PNG fakebytes", "image/png")},
            headers=auth_context["headers"],
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["key"].startswith("uploads/") and data["key"].endswith(".png")
        tenant_id = auth_context["tenant_id"]
        assert f"tenants/{tenant_id}/{data['key']}" in storage.objects
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_upload_reads_stream_in_bounded_chunks() -> None:
    storage = _FakeStorage()
    request = SimpleNamespace(state=SimpleNamespace(request_id="req-test"))
    user = SimpleNamespace(tenant_id="tn1")
    upload = _ChunkedUpload(size=uploads_route._MAX_BYTES)

    response = await uploads_route.upload_image(request, upload, user=user, storage=storage)

    assert response.data is not None
    assert response.data.size == uploads_route._MAX_BYTES
    assert storage.put_calls == 1
    assert upload.read_sizes
    assert all(0 < size <= uploads_route._UPLOAD_READ_CHUNK_BYTES for size in upload.read_sizes)


@pytest.mark.asyncio
async def test_upload_over_limit_413_without_storage_write() -> None:
    storage = _FakeStorage()
    request = SimpleNamespace(state=SimpleNamespace(request_id="req-test"))
    user = SimpleNamespace(tenant_id="tn1")
    upload = _ChunkedUpload(size=uploads_route._MAX_BYTES + 1)

    with pytest.raises(AppError) as exc:
        await uploads_route.upload_image(request, upload, user=user, storage=storage)

    assert exc.value.status_code == 413
    assert exc.value.code == "UPLOAD_TOO_LARGE"
    assert storage.put_calls == 0
    assert storage.objects == {}


def test_upload_rejects_bad_type_and_size(auth_context):
    client = TestClient(app)
    bad_type = client.post(
        "/api/v1/uploads",
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
        headers=auth_context["headers"],
    )
    assert bad_type.status_code == 415

    too_big = client.post(
        "/api/v1/uploads",
        files={"file": ("big.png", b"0" * (10 * 1024 * 1024 + 1), "image/png")},
        headers=auth_context["headers"],
    )
    assert too_big.status_code == 413


# ---------------- schema ----------------


def test_schema_video_mode_validation(auth_context):
    client = TestClient(app)
    bad_mode = client.post(
        "/api/v1/videos",
        json={"topic": "x", "video_mode": "evil"},
        headers=auth_context["headers"],
    )
    assert bad_mode.status_code == 422

    i2v_without_image = client.post(
        "/api/v1/videos",
        json={"topic": "x", "video_mode": "seedance_i2v"},
        headers=auth_context["headers"],
    )
    assert i2v_without_image.status_code == 422

    bad_key = client.post(
        "/api/v1/videos",
        json={"topic": "x", "video_mode": "seedance_i2v", "image_key": "../../sneak.png"},
        headers=auth_context["headers"],
    )
    assert bad_key.status_code == 422


# ---------------- #M2-SEEDANCE-FIX contract ----------------


def test_video_client_seedance_returns_str(monkeypatch, tmp_path):
    from pixelle_video.services.api_services.video_client import VideoClient
    from pixelle_video.services.api_services.video_seedance import SeedanceResult

    client = VideoClient.__new__(VideoClient)  # skip heavy __init__

    class _FakeSeedance:
        def generate_video(self, **kwargs):
            return SeedanceResult(task_id="t", video_url="https://v.example/x.mp4")

    client.seedance_client = _FakeSeedance()
    url = client._generate_seedance("p", None, str(tmp_path / "o.mp4"), "doubao-seedance-2-0")
    assert url == "https://v.example/x.mp4"  # str contract preserved


class _RecordingStore:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def update(self, task_id, **fields):
        self.events.append({"task_id": task_id, **fields})


def test_seedance_to_thread_provider_timeout_marks_failed_without_zombie(
    monkeypatch, auth_context, auth_db
):
    task_id = "seedance-thread-timeout"
    tenant_id = auth_context["tenant_id"]
    with auth_db() as db:
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=tenant_id,
                created_by_user_id=auth_context["user_id"],
                status="queued",
                topic="thread timeout test",
                video_mode="seedance_t2v",
            )
        )
        db.commit()

    async def fake_plan(cfg, topic, n_scenes):
        return [seedance_pipeline.Scene(narration="旁白", video_prompt="画面")]

    finished = threading.Event()
    thread_seen: list[threading.Thread] = []

    def blocking_provider_timeout(*args, **kwargs):
        thread = threading.current_thread()
        thread_seen.append(thread)
        time.sleep(0.05)
        finished.set()
        raise TimeoutError("Ark request timed out after 0.05s")

    store = _RecordingStore()
    monkeypatch.setattr(seedance_pipeline, "_plan_scenes", fake_plan)
    monkeypatch.setattr(seedance_pipeline, "generate_seedance_video", blocking_provider_timeout)
    monkeypatch.setattr(video_tasks, "build_progress_store", lambda url: store)
    monkeypatch.setattr(video_tasks, "create_object_storage", lambda settings: _FakeStorage())
    monkeypatch.setattr(video_tasks, "SessionLocal", auth_db)
    monkeypatch.setattr(video_tasks.settings, "engine_llm_api_key", "llm-k")
    monkeypatch.setattr(video_tasks.settings, "engine_llm_base_url", "https://llm.example")
    monkeypatch.setattr(video_tasks.settings, "engine_llm_model", "m")
    monkeypatch.setattr(video_tasks.settings, "engine_seedance_api_key", "sd-k")

    with pytest.raises(TimeoutError, match="Ark request timed out"):
        video_tasks.generate_video_task.apply(
            args=[
                {
                    "topic": "thread timeout test",
                    "video_mode": "seedance_t2v",
                    "tenant_id": tenant_id,
                }
            ],
            task_id=task_id,
        ).get(propagate=True)

    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task is not None
        assert task.status == "failed"
        assert "Ark request timed out" in (task.error or "")
    assert store.events[-1]["status"] == "FAILURE"
    assert "Ark request timed out" in store.events[-1]["error"]
    assert finished.wait(0.1)
    assert thread_seen
    assert not thread_seen[0].is_alive()
