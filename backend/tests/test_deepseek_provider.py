import pytest
from fastapi.testclient import TestClient

from app.providers.llm.deepseek import DeepSeekProvider


@pytest.mark.asyncio
async def test_deepseek_provider_uses_openai_compatible_chat_client() -> None:
    calls = {}

    class _Message:
        content = "Generated script"

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    class _Completions:
        async def create(self, **kwargs):
            calls.update(kwargs)
            return _Response()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    provider = DeepSeekProvider(api_key="k", base_url="https://deepseek.test", model="m")
    provider.client = _Client()

    result = await provider.generate_text({"topic": "cashmere coat"})

    assert result["text"] == "Generated script"
    assert calls["model"] == "m"
    assert "cashmere coat" in calls["messages"][-1]["content"]


def test_scripts_generate_route_uses_deepseek_provider(monkeypatch, auth_context) -> None:
    from app.api.v1.routes import scripts as scripts_route
    from app.main import app

    class _FakeDeepSeek:
        def __init__(self, *, api_key: str, base_url: str, model: str) -> None:
            assert api_key == "k"
            assert base_url == "https://deepseek.test"
            assert model == "m"

        async def generate_text(self, payload: dict):
            assert payload["topic"] == "cashmere coat"
            return {"text": "DeepSeek script"}

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "m")
    monkeypatch.setattr(scripts_route, "DeepSeekProvider", _FakeDeepSeek, raising=False)

    resp = TestClient(app).post(
        "/api/v1/scripts/generate",
        json={"topic": "cashmere coat"},
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["script"] == "DeepSeek script"
