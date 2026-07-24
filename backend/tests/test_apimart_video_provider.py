from decimal import Decimal
from pathlib import Path

import pytest

from app.core.image_aspect_ratio import VIDEO_GEN_ASPECT_RATIOS
from app.db.models import ProviderConfig
from app.providers.base import (
    VideoProviderCapabilities,
    VideoProviderCapabilitiesError,
    video_provider_size,
)
from app.providers.video.apimart import APIMartVideoProvider, APIMartVideoProviderError


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict | None = None,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = headers or {}
        self.text = text

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeSession:
    def __init__(
        self,
        *,
        post_response: _FakeResponse,
        task_responses: list[_FakeResponse],
        download_response: _FakeResponse,
    ) -> None:
        self.post_response = post_response
        self.task_responses = task_responses
        self.download_response = download_response
        self.post_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResponse:
        self.post_calls.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        return self.post_response

    def get(
        self,
        url: str,
        *,
        headers: dict | None = None,
        timeout: float,
    ) -> _FakeResponse:
        self.get_calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        if url.startswith("https://getapib.org/"):
            return self.download_response
        return self.task_responses.pop(0)


def test_video_gen_product_aspect_ratios_fit_apimart_capabilities() -> None:
    provider = APIMartVideoProvider(api_key="test-apimart-key")

    provider_sizes = {
        video_provider_size(provider, aspect_ratio)
        for aspect_ratio in VIDEO_GEN_ASPECT_RATIOS
    }

    assert provider_sizes <= provider.capabilities.supported_sizes
    assert video_provider_size(provider, "auto") == "adaptive"


@pytest.mark.parametrize(
    "size",
    sorted(APIMartVideoProvider.capabilities.supported_sizes),
)
def test_apimart_video_provider_preserves_all_supported_sizes(size: str) -> None:
    provider = APIMartVideoProvider(api_key="test-apimart-key")

    body, normalized = provider._request_body({"prompt": "product reveal", "size": size})

    assert body["size"] == size
    assert normalized["size"] == size


@pytest.mark.parametrize(
    "capabilities",
    [
        None,
        VideoProviderCapabilities(
            supported_sizes={"adaptive"},  # type: ignore[arg-type]
            automatic_size="adaptive",
        ),
        VideoProviderCapabilities(
            supported_sizes=frozenset({"9:16"}),
            automatic_size="adaptive",
        ),
    ],
    ids=["missing", "malformed-size-container", "automatic-size-not-supported"],
)
def test_video_provider_capabilities_fail_closed_when_invalid(
    capabilities: VideoProviderCapabilities | None,
) -> None:
    class _Provider:
        pass

    provider = _Provider()
    if capabilities is not None:
        provider.capabilities = capabilities

    with pytest.raises(VideoProviderCapabilitiesError):
        video_provider_size(provider, "auto")


@pytest.mark.asyncio
async def test_apimart_video_provider_submits_polls_downloads_and_maps_i2v_payload() -> None:
    sleep_calls: list[float] = []
    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_vid_a"}]}
        ),
        task_responses=[
            _FakeResponse(payload={"code": 200, "data": [{"status": "processing"}]}),
            _FakeResponse(
                payload={
                    "code": 200,
                    "data": {
                        "status": "completed",
                        "result": {
                            "videos": [
                                {
                                    "url": ["https://getapib.org/video/apimart_vid_a.mp4"],
                                    "expires_at": 123,
                                }
                            ],
                            "usage": {"credits": "3.3"},
                        },
                    },
                }
            ),
        ],
        download_response=_FakeResponse(
            content=b"apimart-mp4",
            headers={"content-type": "video/mp4; charset=binary"},
        ),
    )
    provider = APIMartVideoProvider(
        api_key="test-apimart-key",
        base_url="https://api.apimart.ai/v1",
        model="doubao-seedance-2.0",
        session=session,
        sleep_fn=sleep_calls.append,
        request_timeout=12.5,
        poll_initial_delay=30,
        poll_interval=10,
        max_poll_seconds=900,
    )

    result = await provider.generate_video(
        {
            "prompt": "cinematic product reveal",
            "duration_sec": 20,
            "resolution": "4k",
            "fps": 24,
            "image_urls": ["https://storage.test/huading-videos/ref-a.png?sig=ok"],
            "negative_prompt": "blurry, warped product",
            "generate_audio": True,
        }
    )

    assert result["video_bytes"] == b"apimart-mp4"
    assert result["mime_type"] == "video/mp4"
    assert result["provider"] == "apimart"
    assert result["model"] == "doubao-seedance-2.0"
    assert result["task_id"] == "apimart_vid_a"
    assert result["duration"] == 15
    assert result["resolution"] == "720p"
    assert result["size"] == "adaptive"
    assert result["credits"] == Decimal("3.3")
    assert result["cost_cents"] == 238
    assert sleep_calls == [30, 10]
    assert session.post_calls == [
        {
            "url": "https://api.apimart.ai/v1/videos/generations",
            "headers": {"Authorization": "Bearer test-apimart-key"},
            "json": {
                "model": "doubao-seedance-2.0",
                "prompt": "cinematic product reveal",
                "duration": 15,
                "size": "adaptive",
                "resolution": "720p",
                "generate_audio": True,
                "image_urls": ["https://storage.test/huading-videos/ref-a.png?sig=ok"],
                "negative_prompt": "blurry, warped product",
            },
            "timeout": 12.5,
        }
    ]
    assert session.get_calls[-1] == {
        "url": "https://getapib.org/video/apimart_vid_a.mp4",
        "headers": {},
        "timeout": 12.5,
    }


@pytest.mark.asyncio
async def test_apimart_video_provider_uses_t2v_defaults_without_image_urls() -> None:
    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_vid_b"}]}
        ),
        task_responses=[
            _FakeResponse(
                payload={
                    "code": 200,
                    "data": {
                        "status": "completed",
                        "result": {
                            "videos": [
                                {"url": ["https://getapib.org/video/apimart_vid_b.mp4"]}
                            ]
                        },
                    },
                }
            )
        ],
        download_response=_FakeResponse(content=b"t2v-mp4"),
    )
    provider = APIMartVideoProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=lambda _seconds: None,
    )

    result = await provider.generate_video({"prompt": "text only reveal", "duration": 3})

    assert result["video_bytes"] == b"t2v-mp4"
    assert session.post_calls[0]["json"] == {
        "model": "doubao-seedance-2.0",
        "prompt": "text only reveal",
        "duration": 4,
        "size": "9:16",
        "resolution": "720p",
        "generate_audio": False,
    }


@pytest.mark.asyncio
async def test_apimart_video_provider_continues_through_pending_and_unknown_statuses() -> None:
    sleep_calls: list[float] = []
    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_vid_x"}]}
        ),
        task_responses=[
            _FakeResponse(payload={"code": 200, "data": {"status": "pending"}}),
            _FakeResponse(payload={"code": 200, "data": {"status": "in_queue"}}),
            _FakeResponse(
                payload={
                    "code": 200,
                    "data": {
                        "status": "completed",
                        "result": {
                            "videos": [
                                {"url": ["https://getapib.org/video/apimart_vid_x.mp4"]}
                            ]
                        },
                    },
                }
            ),
        ],
        download_response=_FakeResponse(content=b"pending-then-video"),
    )
    provider = APIMartVideoProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=sleep_calls.append,
    )

    result = await provider.generate_video({"prompt": "queue tolerant video"})

    assert result["video_bytes"] == b"pending-then-video"
    assert [call["url"] for call in session.get_calls[:-1]] == [
        "https://api.apimart.ai/v1/tasks/apimart_vid_x",
        "https://api.apimart.ai/v1/tasks/apimart_vid_x",
        "https://api.apimart.ai/v1/tasks/apimart_vid_x",
    ]
    assert sleep_calls == [30.0, 10.0, 10.0]


@pytest.mark.asyncio
async def test_apimart_video_provider_surfaces_balance_errors_without_polling() -> None:
    session = _FakeSession(
        post_response=_FakeResponse(
            status_code=402,
            payload={"code": 402, "message": "insufficient balance"},
            text="insufficient balance",
        ),
        task_responses=[],
        download_response=_FakeResponse(content=b""),
    )
    provider = APIMartVideoProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=lambda _seconds: None,
    )

    with pytest.raises(APIMartVideoProviderError, match="insufficient balance") as exc_info:
        await provider.generate_video({"prompt": "studio video"})

    assert exc_info.value.status_code == 402
    assert session.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload", "message"),
    [
        (429, {"code": 429, "message": "rate limited"}, "rate limited"),
        (
            400,
            {"error": {"message": "model not allowed", "type": "apimart_error"}},
            "model not allowed",
        ),
    ],
)
async def test_apimart_video_provider_surfaces_submit_errors_without_polling(
    status_code: int, payload: dict, message: str
) -> None:
    session = _FakeSession(
        post_response=_FakeResponse(status_code=status_code, payload=payload),
        task_responses=[],
        download_response=_FakeResponse(content=b""),
    )
    provider = APIMartVideoProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=lambda _seconds: None,
    )

    with pytest.raises(APIMartVideoProviderError, match=message):
        await provider.generate_video({"prompt": "studio video"})

    assert session.get_calls == []


@pytest.mark.asyncio
async def test_apimart_video_provider_rejects_failed_task_state() -> None:
    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_vid_c"}]}
        ),
        task_responses=[
            _FakeResponse(
                payload={
                    "code": 200,
                    "data": {"status": "failed", "error": {"message": "model not allowed"}},
                }
            )
        ],
        download_response=_FakeResponse(content=b""),
    )
    provider = APIMartVideoProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=lambda _seconds: None,
    )

    with pytest.raises(APIMartVideoProviderError, match="model not allowed"):
        await provider.generate_video({"prompt": "studio video"})


@pytest.mark.asyncio
async def test_apimart_video_provider_times_out_in_processing_state() -> None:
    clock = {"now": 0.0}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_vid_d"}]}
        ),
        task_responses=[
            _FakeResponse(payload={"code": 200, "data": {"status": "processing"}}),
            _FakeResponse(payload={"code": 200, "data": {"status": "pending"}}),
        ],
        download_response=_FakeResponse(content=b""),
    )
    provider = APIMartVideoProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=sleep,
        time_fn=lambda: clock["now"],
        poll_initial_delay=30,
        poll_interval=10,
        max_poll_seconds=35,
    )

    with pytest.raises(APIMartVideoProviderError, match="timed out"):
        await provider.generate_video({"prompt": "studio video"})


@pytest.mark.asyncio
async def test_apimart_video_provider_rejects_empty_download() -> None:
    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_vid_e"}]}
        ),
        task_responses=[
            _FakeResponse(
                payload={
                    "code": 200,
                    "data": {
                        "status": "completed",
                        "result": {
                            "videos": [
                                {"url": ["https://getapib.org/video/apimart_vid_e.mp4"]}
                            ]
                        },
                    },
                }
            )
        ],
        download_response=_FakeResponse(content=b""),
    )
    provider = APIMartVideoProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=lambda _seconds: None,
    )

    with pytest.raises(APIMartVideoProviderError, match="empty content"):
        await provider.generate_video({"prompt": "studio video"})


def test_apimart_video_factory_and_default_provider_migration_are_declared() -> None:
    from app.providers.video.apimart import _apimart_video_factory

    config = ProviderConfig(
        tenant_id=None,
        capability="video",
        provider="apimart",
        config={"api_key": "config-key", "model": "doubao-seedance-2.0-custom"},
    )

    provider = _apimart_video_factory(config)

    assert isinstance(provider, APIMartVideoProvider)
    assert provider.model == "doubao-seedance-2.0-custom"
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260702_0013_video_apimart_provider.py"
    )
    assert migration_path.exists()
    migration = migration_path.read_text(encoding="utf-8")
    assert "provider='apimart'" in migration
    assert "provider='seedance-mini'" in migration
