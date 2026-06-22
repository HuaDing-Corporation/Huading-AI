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


@pytest.mark.asyncio
async def test_deepseek_provider_uses_custom_script_prompts() -> None:
    calls = {}

    class _Message:
        content = "Custom script"

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

    result = await provider.generate_text(
        {
            "topic": "ceramic bowl",
            "system_prompt": "Write ecommerce selling scripts.",
            "user_prompt": "Create a 30s script with a call to action.",
        }
    )

    assert result["text"] == "Custom script"
    assert calls["messages"][0]["content"] == "Write ecommerce selling scripts."
    assert calls["messages"][1]["content"] == "Create a 30s script with a call to action."


def test_scripts_generate_route_uses_provider_registry(monkeypatch, auth_context) -> None:
    from app.api.v1.routes import scripts as scripts_route
    from app.main import app

    class _FakeDeepSeek:
        async def generate_text(self, payload: dict):
            assert payload["topic"] == "cashmere coat"
            return {"text": "DeepSeek script"}

    def fake_resolve(db, *, tenant_id: str, capability: str):
        assert tenant_id == auth_context["tenant_id"]
        assert capability == "llm"
        return _FakeDeepSeek()

    def fail_direct_provider(**kwargs):
        raise AssertionError("scripts route must resolve the LLM provider via registry")

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "m")
    monkeypatch.setattr(scripts_route, "resolve", fake_resolve, raising=False)
    monkeypatch.setattr(scripts_route, "DeepSeekProvider", fail_direct_provider, raising=False)

    resp = TestClient(app).post(
        "/api/v1/scripts/generate",
        json={"topic": "cashmere coat"},
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["script"] == "DeepSeek script"
