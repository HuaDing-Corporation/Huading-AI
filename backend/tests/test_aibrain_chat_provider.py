from decimal import Decimal
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import ProviderResolutionError
from app.providers.chat.apimart_gpt56 import (
    APIMartGPT56ChatError,
    APIMartGPT56ChatProvider,
    _apimart_gpt56_factory,
)


class _FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.response


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_returns_content_and_usage_non_streaming() -> None:
    session = _FakeSession(
        _FakeResponse(
            {
                "choices": [{"message": {"content": "A complete answer."}}],
                "usage": {
                    "prompt_tokens": 321,
                    "completion_tokens": 123,
                    "total_tokens": 444,
                },
            }
        )
    )
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        base_url="https://api.apimart.ai/v1",
        request_timeout=12.5,
        session=session,
    )

    result = await provider.chat(
        {
            "model": "gpt-5.6-luna",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_completion_tokens": 512,
        }
    )

    assert result == {
        "content": "A complete answer.",
        "model": "gpt-5.6-luna",
        "prompt_tokens": 321,
        "completion_tokens": 123,
        "total_tokens": 444,
    }
    assert session.calls == [
        {
            "url": "https://api.apimart.ai/v1/chat/completions",
            "headers": {
                "Authorization": "Bearer unit-test-key",
                "Content-Type": "application/json",
            },
            "json": {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_completion_tokens": 512,
                "stream": False,
            },
            "timeout": 12.5,
        }
    ]


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_preserves_cache_usage_and_provider_credits() -> None:
    session = _FakeSession(
        _FakeResponse(
            {
                "choices": [{"message": {"content": "Cached answer."}}],
                "usage": {
                    "prompt_tokens": 1_000,
                    "completion_tokens": 500,
                    "total_tokens": 1_500,
                    "prompt_tokens_details": {
                        "cached_tokens": 400,
                        "cache_write_tokens": 100,
                    },
                },
                "credits": "0.5",
            }
        )
    )
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=session,
    )

    result = await provider.chat(
        {
            "model": "gpt-5.6-luna",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert result["cached_prompt_tokens"] == 400
    assert result["cache_write_tokens"] == 100
    assert result["credits"] == Decimal("0.5")
    assert result["cost_cents"] == 35


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_rejects_models_outside_the_fixed_allowlist() -> None:
    session = _FakeSession(_FakeResponse({}))
    provider = APIMartGPT56ChatProvider(api_key="unit-test-key", session=session)

    with pytest.raises(APIMartGPT56ChatError, match="Unsupported"):
        await provider.chat(
            {
                "model": "gpt-5.6-unknown",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert session.calls == []


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_preserves_usage_from_an_error_response() -> None:
    session = _FakeSession(
        _FakeResponse(
            {
                "error": {"message": "account rejected"},
                "usage": {
                    "prompt_tokens": 70,
                    "completion_tokens": 5,
                    "total_tokens": 75,
                },
            },
            status_code=402,
        )
    )
    provider = APIMartGPT56ChatProvider(api_key="unit-test-key", session=session)

    with pytest.raises(APIMartGPT56ChatError) as error:
        await provider.chat(
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.usage_result == {
        "prompt_tokens": 70,
        "completion_tokens": 5,
        "total_tokens": 75,
    }


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_uses_reasoning_content_as_a_fallback() -> None:
    session = _FakeSession(
        _FakeResponse(
            {
                "choices": [{"message": {"content": "", "reasoning_content": "Fallback answer"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 3,
                    "total_tokens": 13,
                },
            }
        )
    )
    provider = APIMartGPT56ChatProvider(api_key="unit-test-key", session=session)

    result = await provider.chat(
        {
            "model": "gpt-5.6-terra",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert result["content"] == "Fallback answer"


def test_apimart_gpt56_factory_never_accepts_an_api_key_from_database_config(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "engine_apimart_api_key", "")
    config = ProviderConfig(
        capability="chat",
        provider="apimart-gpt56",
        config={"api_key": "database-only-value"},
        is_active=True,
    )

    with pytest.raises(ProviderResolutionError, match="not configured"):
        _apimart_gpt56_factory(config)


def test_aibrain_rate_defaults_are_declared_in_runtime_and_env_examples() -> None:
    assert settings.engine_aibrain_reservation_stale_minutes == 30
    assert (
        settings.engine_aibrain_low_input_credits_per_1k,
        settings.engine_aibrain_low_output_credits_per_1k,
        settings.engine_aibrain_mid_input_credits_per_1k,
        settings.engine_aibrain_mid_output_credits_per_1k,
        settings.engine_aibrain_high_input_credits_per_1k,
        settings.engine_aibrain_high_output_credits_per_1k,
    ) == (
        Decimal("1.73"),
        Decimal("10.37"),
        Decimal("4.32"),
        Decimal("25.92"),
        Decimal("8.64"),
        Decimal("51.84"),
    )
    repository_root = Path(__file__).resolve().parents[2]
    expected_lines = {
        "ENGINE_AIBRAIN_LOW_INPUT_CREDITS_PER_1K=1.73",
        "ENGINE_AIBRAIN_LOW_OUTPUT_CREDITS_PER_1K=10.37",
        "ENGINE_AIBRAIN_MID_INPUT_CREDITS_PER_1K=4.32",
        "ENGINE_AIBRAIN_MID_OUTPUT_CREDITS_PER_1K=25.92",
        "ENGINE_AIBRAIN_HIGH_INPUT_CREDITS_PER_1K=8.64",
        "ENGINE_AIBRAIN_HIGH_OUTPUT_CREDITS_PER_1K=51.84",
        "ENGINE_AIBRAIN_RESERVATION_STALE_MINUTES=30",
    }
    for path in (
        repository_root / "backend" / ".env.example",
        repository_root / "infra" / ".env.example",
        repository_root / "infra" / ".env.prod.example",
    ):
        lines = set(path.read_text(encoding="utf-8").splitlines())
        assert expected_lines <= lines
