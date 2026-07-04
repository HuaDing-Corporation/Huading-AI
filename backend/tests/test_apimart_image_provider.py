from decimal import Decimal
from pathlib import Path

import pytest

from app.db.models import ProviderConfig
from app.providers.image.apimart import APIMartImageProvider, APIMartImageProviderError


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
        if url.startswith("https://upload.apimart.ai/"):
            return self.download_response
        return self.task_responses.pop(0)


@pytest.mark.asyncio
async def test_apimart_provider_submits_polls_downloads_and_maps_urls() -> None:
    sleep_calls: list[float] = []
    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_img_1"}]}
        ),
        task_responses=[
            _FakeResponse(payload={"code": 200, "data": {"status": "submitted"}}),
            _FakeResponse(payload={"code": 200, "data": {"status": "pending"}}),
            _FakeResponse(
                payload={
                    "code": 200,
                    "data": {
                        "status": "completed",
                        "result": {
                            "images": [
                                {
                                    "url": ["https://upload.apimart.ai/apimart_img_1.png"],
                                    "expires_at": 123,
                                }
                            ],
                            "usage": {"cost": "2.50"},
                        },
                    },
                }
            ),
        ],
        download_response=_FakeResponse(
            content=b"apimart-png",
            headers={"content-type": "image/png; charset=binary"},
        ),
    )
    provider = APIMartImageProvider(
        api_key="test-apimart-key",
        base_url="https://api.apimart.ai/v1",
        model="gpt-image-2",
        session=session,
        sleep_fn=sleep_calls.append,
        request_timeout=12.5,
        poll_initial_delay=10,
        poll_interval=4,
        max_poll_seconds=180,
    )

    result = await provider.generate_image(
        {
            "prompt": "studio product photo",
            "size": "1536x1024",
            "quality": "medium",
            "input_image_url": "https://storage.test/tenants/t-1/uploads/product.png?sig=ok",
            "background": "opaque",
            "mask_url": "https://storage.test/tenants/t-1/uploads/mask.png?sig=ok",
        }
    )

    assert result["image_bytes"] == b"apimart-png"
    assert result["mime_type"] == "image/png"
    assert result["provider"] == "apimart"
    assert result["model"] == "gpt-image-2"
    assert result["mode"] == "edit"
    assert result["quality"] == "high"
    assert result["task_id"] == "apimart_img_1"
    assert result["credits"] == Decimal("2.50")
    assert result["cost_cents"] > 0
    assert sleep_calls == [10, 4, 4]
    assert session.post_calls == [
        {
            "url": "https://api.apimart.ai/v1/images/generations",
            "headers": {"Authorization": "Bearer test-apimart-key"},
            "json": {
                "model": "gpt-image-2",
                "prompt": "studio product photo",
                "size": "3:2",
                "resolution": "1k",
                "quality": "high",
                "n": 1,
                "image_urls": [
                    "https://storage.test/tenants/t-1/uploads/product.png?sig=ok"
                ],
            },
            "timeout": 12.5,
        }
    ]
    assert session.get_calls == [
        {
            "url": "https://api.apimart.ai/v1/tasks/apimart_img_1",
            "headers": {"Authorization": "Bearer test-apimart-key"},
            "timeout": 12.5,
        },
        {
            "url": "https://api.apimart.ai/v1/tasks/apimart_img_1",
            "headers": {"Authorization": "Bearer test-apimart-key"},
            "timeout": 12.5,
        },
        {
            "url": "https://api.apimart.ai/v1/tasks/apimart_img_1",
            "headers": {"Authorization": "Bearer test-apimart-key"},
            "timeout": 12.5,
        },
        {
            "url": "https://upload.apimart.ai/apimart_img_1.png",
            "headers": {},
            "timeout": 12.5,
        },
    ]


@pytest.mark.parametrize("input_quality", ["low", "medium", "high", "auto", "unexpected"])
@pytest.mark.asyncio
async def test_apimart_provider_always_sends_high_quality(input_quality: str) -> None:
    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_quality"}]}
        ),
        task_responses=[
            _FakeResponse(
                payload={
                    "code": 200,
                    "data": {
                        "status": "completed",
                        "result": {
                            "images": [
                                {"url": ["https://upload.apimart.ai/apimart_quality.png"]}
                            ]
                        },
                    },
                }
            )
        ],
        download_response=_FakeResponse(content=b"quality-png"),
    )
    provider = APIMartImageProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=lambda _seconds: None,
    )

    result = await provider.generate_image(
        {
            "prompt": "studio product photo",
            "quality": input_quality,
            "background": "opaque",
            "mask_url": "https://storage.test/mask.png?sig=ok",
        }
    )

    post_body = session.post_calls[0]["json"]
    assert post_body["quality"] == "high"
    assert "background" not in post_body
    assert "mask_url" not in post_body
    assert result["quality"] == "high"


@pytest.mark.asyncio
async def test_apimart_provider_treats_unknown_nonterminal_status_as_processing() -> None:
    sleep_calls: list[float] = []
    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_queue"}]}
        ),
        task_responses=[
            _FakeResponse(payload={"code": 200, "data": {"status": "in_queue"}}),
            _FakeResponse(
                payload={
                    "code": 200,
                    "data": {
                        "status": "completed",
                        "result": {
                            "images": [
                                {"url": ["https://upload.apimart.ai/apimart_queue.png"]}
                            ]
                        },
                    },
                }
            ),
        ],
        download_response=_FakeResponse(content=b"queued-png"),
    )
    provider = APIMartImageProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=sleep_calls.append,
        poll_initial_delay=10,
        poll_interval=4,
    )

    result = await provider.generate_image({"prompt": "studio product photo"})

    assert result["image_bytes"] == b"queued-png"
    assert result["task_id"] == "apimart_queue"
    assert sleep_calls == [10, 4]


@pytest.mark.asyncio
async def test_apimart_provider_surfaces_balance_errors_without_polling() -> None:
    session = _FakeSession(
        post_response=_FakeResponse(
            status_code=402,
            payload={"code": 402, "message": "insufficient balance"},
            text="insufficient balance",
        ),
        task_responses=[],
        download_response=_FakeResponse(content=b""),
    )
    provider = APIMartImageProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=lambda _seconds: None,
    )

    with pytest.raises(APIMartImageProviderError, match="insufficient balance") as exc_info:
        await provider.generate_image({"prompt": "studio product photo"})

    assert exc_info.value.status_code == 402
    assert session.get_calls == []


@pytest.mark.asyncio
async def test_apimart_provider_releases_on_failed_task_state() -> None:
    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_fail"}]}
        ),
        task_responses=[
            _FakeResponse(
                payload={
                    "code": 200,
                    "data": {"status": "failed", "error": {"message": "policy rejected"}},
                }
            )
        ],
        download_response=_FakeResponse(content=b""),
    )
    provider = APIMartImageProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=lambda _seconds: None,
    )

    with pytest.raises(APIMartImageProviderError, match="policy rejected"):
        await provider.generate_image({"prompt": "studio product photo"})


@pytest.mark.asyncio
async def test_apimart_provider_times_out_in_processing_state() -> None:
    clock = {"now": 0.0}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    session = _FakeSession(
        post_response=_FakeResponse(
            payload={"code": 200, "data": [{"status": "submitted", "task_id": "apimart_slow"}]}
        ),
        task_responses=[
            _FakeResponse(payload={"code": 200, "data": {"status": "processing"}}),
            _FakeResponse(payload={"code": 200, "data": {"status": "processing"}}),
        ],
        download_response=_FakeResponse(content=b""),
    )
    provider = APIMartImageProvider(
        api_key="test-apimart-key",
        session=session,
        sleep_fn=sleep,
        time_fn=lambda: clock["now"],
        poll_initial_delay=10,
        poll_interval=4,
        max_poll_seconds=12,
    )

    with pytest.raises(APIMartImageProviderError, match="timed out"):
        await provider.generate_image({"prompt": "studio product photo"})


def test_apimart_factory_and_default_provider_migration_are_declared() -> None:
    from app.providers.image.apimart import _apimart_image_factory

    config = ProviderConfig(
        tenant_id=None,
        capability="image",
        provider="apimart",
        config={"api_key": "config-key", "model": "gpt-image-2-official"},
    )

    provider = _apimart_image_factory(config)

    assert isinstance(provider, APIMartImageProvider)
    assert provider.model == "gpt-image-2-official"
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260701_0012_image_apimart_provider.py"
    )
    assert migration_path.exists()
    migration = migration_path.read_text(encoding="utf-8")
    assert "provider='apimart'" in migration
    assert "provider='openai'" in migration
