"""Seedance (Ark) client tests with mocked network (#M2-SEEDANCE).

Verifies the t2v vs i2v request payloads, the submit->poll->download flow,
status handling, and credential guard — without hitting Ark.
"""

import sys
from pathlib import Path

# Inject backend/app/engine so the vendored pixelle_video package resolves
# (mirrors app/engine/__init__.py); must run before importing the client.
_ENGINE_DIR = Path(__file__).resolve().parents[1] / "app" / "engine"
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

import pytest  # noqa: E402
from pixelle_video.services.api_services import video_seedance  # noqa: E402
from pixelle_video.services.api_services.video_seedance import (  # noqa: E402
    SeedanceVideoClient,
)


class FakeResponse:
    def __init__(self, *, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self.ok = status_code < 400
        self._json = json_data or {}
        self.text = str(json_data)
        self._content = content

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        yield self._content


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Patch requests.request with a scripted Ark backend; record POST payloads."""
    monkeypatch.setattr(video_seedance.time, "sleep", lambda *_: None)
    calls = {"submit_payload": None, "poll_count": 0}
    # poll status sequence: queued -> running -> succeeded
    statuses = iter(["queued", "running", "succeeded"])

    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/contents/generations/tasks"):
            calls["submit_payload"] = kwargs.get("json")
            return FakeResponse(json_data={"id": "cgt-123"})
        if method == "GET" and "/tasks/cgt-123" in url:
            calls["poll_count"] += 1
            status = next(statuses)
            body = {"status": status}
            if status == "succeeded":
                body["content"] = {"video_url": "https://ark.example/v.mp4"}
            return FakeResponse(json_data=body)
        if method == "GET" and url == "https://ark.example/v.mp4":
            return FakeResponse(content=b"FAKE-MP4-BYTES")
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(video_seedance.requests, "request", fake_request)
    return calls


def test_t2v_payload_and_flow(patched, tmp_path):
    client = SeedanceVideoClient(api_key="k", base_url="https://ark.example/api/v3")
    out = tmp_path / "t2v.mp4"
    result = client.generate_video(
        "hello world", save_path=str(out), duration=7, resolution="1080p"
    )

    payload = patched["submit_payload"]
    assert payload["model"] == "doubao-seedance-2-0-260128"
    # text-only content -> text-to-video
    assert payload["content"] == [{"type": "text", "text": "hello world"}]
    assert payload["duration"] == 7
    assert payload["resolution"] == "1080p"
    assert payload["ratio"] == "16:9"
    assert result.task_id == "cgt-123"
    assert result.video_url == "https://ark.example/v.mp4"
    assert out.read_bytes() == b"FAKE-MP4-BYTES"
    assert patched["poll_count"] == 3  # queued, running, succeeded


def test_seedance_progress_callback_runs_for_every_poll(patched, tmp_path):
    client = SeedanceVideoClient(api_key="k", base_url="https://ark.example/api/v3")
    events = []

    client.generate_video(
        "hello world",
        save_path=str(tmp_path / "t2v.mp4"),
        progress_callback=events.append,
    )

    assert [event["status"] for event in events] == ["queued", "running", "succeeded"]
    assert [event["poll_count"] for event in events] == [1, 2, 3]
    assert {event["task_id"] for event in events} == {"cgt-123"}


def test_i2v_with_local_path_base64(patched, tmp_path):
    img = tmp_path / "in.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = SeedanceVideoClient(api_key="k")
    client.generate_video("animate", image_path=str(img))

    content = patched["submit_payload"]["content"]
    assert content[0] == {"type": "text", "text": "animate"}
    image_item = content[1]
    assert image_item["type"] == "image_url"
    assert image_item["role"] == "first_frame"
    assert image_item["image_url"]["url"].startswith("data:image/png;base64,")


def test_i2v_with_url_passthrough(patched):
    client = SeedanceVideoClient(api_key="k")
    client.generate_video(
        "animate", image="https://cdn.example/p.jpg", image_role="reference_image"
    )

    image_item = patched["submit_payload"]["content"][1]
    assert image_item["image_url"]["url"] == "https://cdn.example/p.jpg"
    assert image_item["role"] == "reference_image"


def test_failed_status_raises(monkeypatch):
    monkeypatch.setattr(video_seedance.time, "sleep", lambda *_: None)

    def fake_request(method, url, **kwargs):
        if method == "POST":
            return FakeResponse(json_data={"id": "cgt-x"})
        return FakeResponse(json_data={"status": "failed", "error": {"message": "nsfw"}})

    monkeypatch.setattr(video_seedance.requests, "request", fake_request)
    client = SeedanceVideoClient(api_key="k")
    with pytest.raises(RuntimeError, match="failed"):
        client.generate_video("x")


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("SEEDANCE_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    client = SeedanceVideoClient(api_key=None)
    with pytest.raises(RuntimeError, match="not set"):
        client.generate_video("x")


def test_engine_unified_entry(monkeypatch, patched, tmp_path):
    from app.engine import EngineConfig, generate_seedance_video

    cfg = EngineConfig(
        llm_api_key="unused",
        llm_base_url="unused",
        llm_model="unused",
        seedance_api_key="k",
        seedance_base_url="https://ark.example/api/v3",
    )
    result = generate_seedance_video(cfg, "hello", save_path=str(tmp_path / "o.mp4"))
    assert result.video_url == "https://ark.example/v.mp4"


def test_seedance_configured_timeout_reaches_submit_poll_and_download(monkeypatch, tmp_path):
    from app.engine import EngineConfig, generate_seedance_video

    monkeypatch.setattr(video_seedance.time, "sleep", lambda *_: None)
    calls = []
    statuses = iter(["succeeded"])

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, "timeout": kwargs.get("timeout")})
        if method == "POST":
            return FakeResponse(json_data={"id": "cgt-timeout"})
        if method == "GET" and "/tasks/cgt-timeout" in url:
            next(statuses)
            return FakeResponse(json_data={"status": "succeeded", "content": {"video_url": "https://ark.example/v.mp4"}})
        if method == "GET" and url == "https://ark.example/v.mp4":
            return FakeResponse(content=b"MP4")
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(video_seedance.requests, "request", fake_request)
    cfg = EngineConfig(
        llm_api_key="unused",
        llm_base_url="unused",
        llm_model="unused",
        seedance_api_key="k",
        seedance_base_url="https://ark.example/api/v3",
        seedance_request_timeout_seconds=7.5,
        seedance_poll_interval_seconds=0.1,
        seedance_timeout_seconds=12,
    )

    generate_seedance_video(cfg, "hello", save_path=str(tmp_path / "out.mp4"))

    assert [call["timeout"] for call in calls] == [7.5, 7.5, 7.5]
