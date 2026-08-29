from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.providers.llm.deepseek import DeepSeekProvider


def test_deepseek_attachment_rejects_non_integer_supplier_telemetry() -> None:
    from decimal import Decimal

    from app.db.models import UsageRecord
    from app.services.provider_costs import attach_deepseek_usage

    record = UsageRecord(
        tenant_id="tenant-1",
        capability="llm",
        provider="quoted-provider",
        model="quoted-model",
        unit="call",
        quantity=Decimal("1"),
        credits=Decimal("1"),
        cost_cents=0,
        status="reserved",
    )

    attach_deepseek_usage(
        record,
        result={
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "usage": {"prompt_tokens": True, "completion_tokens": 1.5, "total_tokens": 2},
        },
    )

    assert record.provider == "quoted-provider"
    assert record.model == "quoted-model"
    assert record.provider_usage is None
    assert record.cost_cents == 0


def _script_submission_headers(
    client: TestClient, auth_headers: dict[str, str], payload: dict
) -> dict[str, str]:
    quote = client.post("/api/v1/scripts/estimate", json=payload, headers=auth_headers)
    assert quote.status_code == 200, quote.text
    return {
        **auth_headers,
        "Idempotency-Key": str(uuid4()),
        "X-Huading-Quote": quote.json()["data"]["quote_token"],
    }


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


@pytest.mark.asyncio
async def test_deepseek_provider_returns_token_usage_for_cost_reconcile() -> None:
    class _Message:
        content = "Generated title"

    class _Choice:
        message = _Message()

    class _Usage:
        prompt_tokens = 1200
        completion_tokens = 300
        total_tokens = 1500
        prompt_cache_hit_tokens = 400
        prompt_cache_miss_tokens = 800

    class _Response:
        choices = [_Choice()]
        usage = _Usage()

    class _Completions:
        async def create(self, **_kwargs):
            return _Response()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    provider = DeepSeekProvider(api_key="k", base_url="https://deepseek.test", model="m")
    provider.client = _Client()

    result = await provider.generate_text({"topic": "cashmere coat"})

    assert result["provider"] == "deepseek"
    assert result["model"] == "m"
    assert result["usage"] == {
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "total_tokens": 1500,
        "prompt_cache_hit_tokens": 400,
        "prompt_cache_miss_tokens": 800,
    }


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

    client = TestClient(app)
    payload = {"topic": "cashmere coat"}
    resp = client.post(
        "/api/v1/scripts/generate",
        json=payload,
        headers=_script_submission_headers(client, auth_context["headers"], payload),
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["script"] == "DeepSeek script"


def test_scripts_generate_rejects_unknown_length_tier(auth_context) -> None:
    from app.main import app

    resp = TestClient(app).post(
        "/api/v1/scripts/generate",
        json={"topic": "cashmere coat", "length_tier": "extra-long"},
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": "placeholder",
        },
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert resp.json()["error"]["details"][0]["type"] == "literal_error"


def test_scripts_generate_short_length_tier_reaches_prompt(monkeypatch, auth_context) -> None:
    from app.api.v1.routes import scripts as scripts_route
    from app.main import app

    payloads: list[dict] = []

    class _FakeDeepSeek:
        async def generate_text(self, payload: dict):
            payloads.append(payload)
            return {"text": "Short sales script"}

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "m")
    monkeypatch.setattr(
        scripts_route,
        "resolve",
        lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
    )

    client = TestClient(app)
    payload = {
        "topic": "高腰阔腿裤",
        "video_mode": "seedance_i2v",
        "duration_sec": 10,
        "length_tier": "short",
    }
    resp = client.post(
        "/api/v1/scripts/generate",
        json=payload,
        headers=_script_submission_headers(client, auth_context["headers"], payload),
    )

    assert resp.status_code == 200
    assert payloads[0]["target_chars_min"] == 40
    assert payloads[0]["target_chars_max"] == 50
    assert "40-50字" in payloads[0]["user_prompt"]


def test_scripts_generate_long_length_tier_reaches_prompt(monkeypatch, auth_context) -> None:
    from app.api.v1.routes import scripts as scripts_route
    from app.main import app

    payloads: list[dict] = []

    class _FakeDeepSeek:
        async def generate_text(self, payload: dict):
            payloads.append(payload)
            return {"text": "Long sales script"}

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "m")
    monkeypatch.setattr(
        scripts_route,
        "resolve",
        lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
    )

    client = TestClient(app)
    payload = {
        "topic": "高腰阔腿裤",
        "video_mode": "seedance_i2v",
        "duration_sec": 10,
        "length_tier": "long",
    }
    resp = client.post(
        "/api/v1/scripts/generate",
        json=payload,
        headers=_script_submission_headers(client, auth_context["headers"], payload),
    )

    assert resp.status_code == 200
    assert payloads[0]["target_chars_min"] == 60
    assert payloads[0]["target_chars_max"] == 70
    assert "60-70字" in payloads[0]["user_prompt"]


def test_scripts_generate_records_deepseek_token_cost(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from decimal import Decimal

    from sqlalchemy import select

    from app.api.v1.routes import scripts as scripts_route
    from app.db.models import UsageRecord
    from app.main import app
    from app.services import provider_costs

    class _FakeDeepSeek:
        async def generate_text(self, payload: dict):
            assert payload["topic"] == "cashmere coat"
            return {
                "text": "DeepSeek script",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "usage": {
                    "prompt_tokens": 100_000,
                    "completion_tokens": 50_000,
                    "total_tokens": 150_000,
                },
            }

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "deepseek-v4-flash")
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_deepseek_cny_per_1k_input",
        Decimal("0.001008"),
        raising=False,
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_deepseek_cny_per_1k_output",
        Decimal("0.002016"),
        raising=False,
    )
    monkeypatch.setattr(
        scripts_route,
        "resolve",
        lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        raising=False,
    )

    client = TestClient(app)
    payload = {"topic": "cashmere coat"}
    resp = client.post(
        "/api/v1/scripts/generate",
        json=payload,
        headers=_script_submission_headers(client, auth_context["headers"], payload),
    )

    assert resp.status_code == 200
    with auth_db() as db:
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.provider == "deepseek",
                UsageRecord.unit == "call",
            )
        )
        assert usage is not None
        assert usage.model == "deepseek-v4-flash"
        assert usage.quantity == Decimal("1.000")
        assert usage.credits == Decimal("1.000000")
        assert usage.cost_cents == 20
        assert usage.status == "settled"
        assert usage.provider_usage == {
            "input_tokens": 100_000,
            "output_tokens": 50_000,
            "total_tokens": 150_000,
        }


def test_scripts_generate_seedance_i2v_uses_ecommerce_payload_and_cleans(
    monkeypatch, auth_context
) -> None:
    from app.api.v1.routes import scripts as scripts_route
    from app.main import app

    payloads: list[dict] = []

    class _FakeDeepSeek:
        async def generate_text(self, payload: dict):
            payloads.append(payload)
            return {
                "text": (
                    "【数字人主播脚本】\n"
                    "（微笑，自然站姿，手持或展示裤子）\n"
                    "**姐妹们，这条裤子显瘦又舒服，现在下单更划算。**"
                )
            }

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "m")
    monkeypatch.setattr(
        scripts_route,
        "resolve",
        lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        raising=False,
    )

    client = TestClient(app)
    payload = {
        "topic": "高腰阔腿裤，显瘦，通勤休闲都能穿",
        "video_mode": "seedance_i2v",
        "duration_sec": 10,
    }
    resp = client.post(
        "/api/v1/scripts/generate",
        json=payload,
        headers=_script_submission_headers(client, auth_context["headers"], payload),
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["script"] == "姐妹们，这条裤子显瘦又舒服，现在下单更划算。"
    payload = payloads[0]
    assert payload["video_mode"] == "seedance_i2v"
    assert payload["target_duration_sec"] == 10
    assert payload["target_chars_min"] == 50
    assert payload["target_chars_max"] == 60
    assert "system_prompt" in payload
    assert "user_prompt" in payload
