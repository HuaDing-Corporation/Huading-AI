import base64
from types import SimpleNamespace

import pytest

from app.providers.image.openai import OpenAIImageProvider


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
            "response_format": "b64_json",
        }
    ]


@pytest.mark.asyncio
async def test_openai_image_provider_edits_with_high_input_fidelity(tmp_path) -> None:
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
    assert call["input_fidelity"] == "high"
    assert call["response_format"] == "b64_json"
    image_files = call["image"]
    assert len(image_files) == 1
    assert image_files[0].name == str(image_path)
