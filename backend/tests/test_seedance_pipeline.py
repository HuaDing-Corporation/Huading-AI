"""Seedance pipelines + worker routing + uploads (#M2-SEEDANCE-P2-BE).

Network, LLM, TTS and ffmpeg are mocked throughout — these tests cover the
orchestration, routing, validation, and tenant scoping.
"""

import sys
from pathlib import Path

_ENGINE_DIR = Path(__file__).resolve().parents[1] / "app" / "engine"
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_object_storage  # noqa: E402
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

    def put_bytes(self, key, content, *, content_type):
        self.objects[key] = content
        return f"memory://{key}"

    def get_bytes(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]


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
