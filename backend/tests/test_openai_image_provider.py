import base64
from types import SimpleNamespace

import pytest

from app.providers.image import openai as openai_provider
from app.providers.image.openai import OpenAIImageProvider

_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class _FakeOpenAI:
    instances: list["_FakeOpenAI"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        _FakeOpenAI.instances.append(self)


class _FakeDefaultHttpxClient:
    instances: list["_FakeDefaultHttpxClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        _FakeDefaultHttpxClient.instances.append(self)


class _FakeImages:
    def __init__(self) -> None:
        self.generate_calls: list[dict] = []
        self.edit_calls: list[dict] = []

    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        encoded = base64.b64encode(b"generated-png").decode("ascii")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=encoded)])

    def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        encoded = base64.b64encode(b"edited-png").decode("ascii")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=encoded)])


class _FakeClient:
    def __init__(self) -> None:
        self.images = _FakeImages()


def test_openai_image_provider_uses_sdk_default_http_client_for_proxy(
    monkeypatch,
) -> None:
    _FakeOpenAI.instances.clear()
    _FakeDefaultHttpxClient.instances.clear()
    monkeypatch.setattr(openai_provider, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        openai_provider, "DefaultHttpxClient", _FakeDefaultHttpxClient, raising=False
    )

    OpenAIImageProvider(
        api_key="test-key",
        base_url="https://openai-proxy.test/v1",
        local_proxy="http://127.0.0.1:7897",
        timeout=12.5,
    )

    assert len(_FakeDefaultHttpxClient.instances) == 1
    assert _FakeDefaultHttpxClient.instances[0].kwargs == {
        "proxy": "http://127.0.0.1:7897",
        "timeout": 12.5,
    }
    assert len(_FakeOpenAI.instances) == 1
    assert _FakeOpenAI.instances[0].kwargs["http_client"] is _FakeDefaultHttpxClient.instances[0]
    assert _FakeOpenAI.instances[0].kwargs["base_url"] == "https://openai-proxy.test/v1"


def test_openai_image_provider_omits_http_client_without_proxy(monkeypatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeDefaultHttpxClient.instances.clear()
    monkeypatch.setattr(openai_provider, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        openai_provider, "DefaultHttpxClient", _FakeDefaultHttpxClient, raising=False
    )

    OpenAIImageProvider(
        api_key="test-key",
        base_url="",
        local_proxy="",
        timeout=12.5,
    )

    assert _FakeDefaultHttpxClient.instances == []
    assert len(_FakeOpenAI.instances) == 1
    assert "http_client" not in _FakeOpenAI.instances[0].kwargs
    assert _FakeOpenAI.instances[0].kwargs["base_url"] == _DEFAULT_OPENAI_BASE_URL


@pytest.mark.asyncio
async def test_openai_image_provider_generates_text_to_image() -> None:
    client = _FakeClient()
    provider = OpenAIImageProvider(
        api_key="test-key",
        model="gpt-image-2",
        client=client,
    )

    result = await provider.generate_image(
        {
            "prompt": "studio product photo of a ceramic mug",
            "size": "1536x1024",
            "quality": "high",
        }
    )

    assert result["image_bytes"] == b"generated-png"
    assert result["mime_type"] == "image/png"
    assert result["model"] == "gpt-image-2"
    assert client.images.edit_calls == []
    assert client.images.generate_calls == [
        {
            "model": "gpt-image-2",
            "prompt": "studio product photo of a ceramic mug",
            "size": "1536x1024",
            "quality": "high",
            "n": 1,
        }
    ]


@pytest.mark.asyncio
async def test_openai_image_provider_edits_without_unsupported_input_fidelity(
    tmp_path,
) -> None:
    image_path = tmp_path / "input.png"
    image_path.write_bytes(b"source-png")
    client = _FakeClient()
    provider = OpenAIImageProvider(
        api_key="test-key",
        model="gpt-image-2",
        client=client,
    )

    result = await provider.generate_image(
        {
            "prompt": "replace background with soft studio lighting",
            "input_image_path": str(image_path),
            "size": "1024x1536",
            "quality": "medium",
        }
    )

    assert result["image_bytes"] == b"edited-png"
    assert client.images.generate_calls == []
    assert len(client.images.edit_calls) == 1
    call = client.images.edit_calls[0]
    assert call["model"] == "gpt-image-2"
    assert call["prompt"] == "replace background with soft studio lighting"
    assert call["size"] == "1024x1536"
    assert call["quality"] == "medium"
    assert call["n"] == 1
    assert "input_fidelity" not in call
    assert "response_format" not in call
    image_files = call["image"]
    assert len(image_files) == 1
    assert image_files[0].name == str(image_path)
