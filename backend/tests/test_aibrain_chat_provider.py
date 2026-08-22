from decimal import Decimal
from pathlib import Path

import pytest
import requests

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import ProviderResolutionError
from app.providers.chat.apimart_gpt56 import (
    APIMartGPT56ChatError,
    APIMartGPT56ChatProvider,
    _apimart_gpt56_factory,
)
from app.services import aibrain
from app.services.apimart_token_pricing import (
    APIMartTokenPricingError,
    apimart_token_rate,
    apimart_token_usage_cost,
)


class _FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class _InvalidJSONResponse:
    status_code = 200

    def json(self) -> dict:
        raise ValueError("invalid JSON")


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(
        self,
        url: str,
        *,
        headers: dict,
        json: dict,
        timeout: float,
        allow_redirects: bool,
    ):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        return self.response


class _RaisingSession:
    def __init__(self, error: requests.RequestException) -> None:
        self.error = error

    def post(self, *args, **kwargs):
        raise self.error


@pytest.mark.parametrize(
    "usage_result",
    [
        {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        {"credits": Decimal("0.5")},
        {"cost_cents": 35},
    ],
)
def test_apimart_gpt56_chat_error_infers_cost_evidence_for_existing_callers(
    usage_result: dict[str, object],
) -> None:
    error = APIMartGPT56ChatError("provider failed", usage_result=usage_result)

    assert error.has_cost_evidence is True
    assert error.request_may_have_been_accepted is True


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
            "allow_redirects": False,
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
async def test_apimart_gpt56_chat_uses_ttl_cache_creation_breakdown() -> None:
    session = _FakeSession(
        _FakeResponse(
            {
                "choices": [{"message": {"content": "TTL cached answer."}}],
                "usage": {
                    "prompt_tokens": 1_000,
                    "completion_tokens": 50,
                    "total_tokens": 1_050,
                    "prompt_tokens_details": {
                        "cached_tokens": 0,
                        "cache_write_tokens": 0,
                    },
                    "claude_cache_creation_5_m_tokens": 600,
                    "claude_cache_creation_1_h_tokens": 400,
                },
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

    assert result["cached_prompt_tokens"] == 0
    assert result["cache_write_tokens"] == 1_000


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_rejects_models_outside_the_fixed_allowlist() -> None:
    session = _FakeSession(_FakeResponse({}))
    provider = APIMartGPT56ChatProvider(api_key="unit-test-key", session=session)

    with pytest.raises(APIMartGPT56ChatError, match="Unsupported") as error:
        await provider.chat(
            {
                "model": "gpt-5.6-unknown",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert session.calls == []
    assert error.value.request_may_have_been_accepted is False
    assert error.value.has_cost_evidence is False


@pytest.mark.parametrize("max_completion_tokens", [float("inf"), float("-inf")])
@pytest.mark.asyncio
async def test_apimart_gpt56_chat_rejects_infinite_max_completion_tokens_before_dispatch(
    max_completion_tokens: float,
) -> None:
    session = _FakeSession(_FakeResponse({}))
    provider = APIMartGPT56ChatProvider(api_key="unit-test-key", session=session)

    with pytest.raises(APIMartGPT56ChatError, match="positive integer") as error:
        await provider.chat(
            {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_completion_tokens": max_completion_tokens,
            }
        )

    assert session.calls == []
    assert error.value.request_may_have_been_accepted is False


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_marks_connect_timeout_as_not_accepted() -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_RaisingSession(requests.ConnectTimeout("connect timed out")),
    )

    with pytest.raises(APIMartGPT56ChatError, match="connect") as error:
        await provider.chat(
            {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.request_may_have_been_accepted is False
    assert error.value.has_cost_evidence is False
    assert error.value.raw_usage_present is False
    assert error.value.raw_cost_present is False


@pytest.mark.parametrize(
    "request_error",
    [
        requests.ReadTimeout("read timed out"),
        requests.ConnectionError("connection dropped after send"),
    ],
)
@pytest.mark.asyncio
async def test_apimart_gpt56_chat_marks_ambiguous_network_failure_as_maybe_accepted(
    request_error: requests.RequestException,
) -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_RaisingSession(request_error),
    )

    with pytest.raises(APIMartGPT56ChatError) as error:
        await provider.chat(
            {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.request_may_have_been_accepted is True
    assert error.value.has_cost_evidence is False
    assert error.value.raw_usage_present is False
    assert error.value.raw_cost_present is False


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_marks_generic_request_failure_as_maybe_accepted() -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_RaisingSession(requests.RequestException("request failed")),
    )

    with pytest.raises(APIMartGPT56ChatError) as error:
        await provider.chat(
            {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.request_may_have_been_accepted is True
    assert error.value.has_cost_evidence is False


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_marks_invalid_json_as_maybe_accepted() -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(_InvalidJSONResponse()),
    )

    with pytest.raises(APIMartGPT56ChatError, match="invalid JSON") as error:
        await provider.chat(
            {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.request_may_have_been_accepted is True
    assert error.value.has_cost_evidence is False
    assert error.value.raw_usage_present is False
    assert error.value.raw_cost_present is False


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
    assert error.value.raw_usage_present is True
    assert error.value.usage_contract_valid is True
    assert error.value.has_cost_evidence is True
    assert error.value.request_may_have_been_accepted is True


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_marks_http_error_with_provider_cost_as_accepted() -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {
                    "error": {"message": "upstream failed after billing"},
                    "usage": {
                        "prompt_tokens": 70,
                        "completion_tokens": 5,
                        "total_tokens": 75,
                    },
                    "credits": "0.5",
                    "cost_cents": 35,
                },
                status_code=502,
            )
        ),
    )

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
        "credits": Decimal("0.5"),
        "cost_cents": 35,
    }
    assert error.value.raw_usage_present is True
    assert error.value.raw_cost_present is True
    assert error.value.usage_contract_valid is True
    assert error.value.cost_contract_valid is True
    assert error.value.has_cost_evidence is True
    assert error.value.request_may_have_been_accepted is True


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_preserves_cost_only_error_evidence() -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {
                    "error": {"message": "billed without token usage"},
                    "cost_cents": 35,
                },
                status_code=402,
            )
        ),
    )

    with pytest.raises(APIMartGPT56ChatError) as error:
        await provider.chat(
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.usage_result == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "_usage_contract_missing": True,
        "_usage_contract_valid": False,
        "cost_cents": 35,
    }
    assert error.value.raw_usage_present is False
    assert error.value.raw_cost_present is True
    assert error.value.usage_contract_valid is False
    assert error.value.cost_contract_valid is True
    assert error.value.has_cost_evidence is True
    assert error.value.request_may_have_been_accepted is True


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_does_not_treat_empty_usage_mapping_as_cost_evidence() -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {"error": {"message": "request rejected"}},
                status_code=400,
            )
        ),
    )

    with pytest.raises(APIMartGPT56ChatError) as error:
        await provider.chat(
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.usage_result == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "_usage_contract_missing": True,
        "_usage_contract_valid": False,
    }
    assert bool(error.value.usage_result) is True
    assert error.value.raw_usage_present is False
    assert error.value.raw_cost_present is False
    assert error.value.has_cost_evidence is False
    assert error.value.request_may_have_been_accepted is False


@pytest.mark.parametrize("malformed_usage", [None, {}, "not-an-object"])
@pytest.mark.asyncio
async def test_apimart_gpt56_chat_distinguishes_malformed_from_missing_raw_usage(
    malformed_usage: object,
) -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {
                    "error": {"message": "malformed usage"},
                    "usage": malformed_usage,
                },
                status_code=400,
            )
        ),
    )

    with pytest.raises(APIMartGPT56ChatError) as error:
        await provider.chat(
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.raw_usage_present is True
    assert "_usage_contract_missing" not in error.value.usage_result
    assert error.value.usage_contract_valid is False
    assert error.value.has_cost_evidence is False
    assert error.value.request_may_have_been_accepted is False


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_preserves_malformed_raw_cost_presence() -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {
                    "error": {"message": "malformed provider cost"},
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                    "credits": "not-a-number",
                },
                status_code=400,
            )
        ),
    )

    with pytest.raises(APIMartGPT56ChatError) as error:
        await provider.chat(
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.raw_usage_present is True
    assert error.value.usage_contract_valid is True
    assert error.value.raw_cost_present is True
    assert error.value.cost_contract_valid is False
    assert error.value.has_cost_evidence is False


@pytest.mark.parametrize("status_code", [408, 500, 503])
@pytest.mark.asyncio
async def test_apimart_gpt56_chat_marks_ambiguous_http_failure_as_maybe_accepted(
    status_code: int,
) -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {"error": {"message": "ambiguous upstream failure"}},
                status_code=status_code,
            )
        ),
    )

    with pytest.raises(APIMartGPT56ChatError) as error:
        await provider.chat(
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.has_cost_evidence is False
    assert error.value.request_may_have_been_accepted is True
    assert error.value.usage_result["_usage_contract_missing"] is True


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_treats_nonnumeric_api_code_as_an_error() -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {
                    "code": "UPSTREAM_ERROR",
                    "message": "provider unavailable",
                    "usage": {
                        "prompt_tokens": 70,
                        "completion_tokens": 5,
                        "total_tokens": 75,
                    },
                }
            )
        ),
    )

    with pytest.raises(APIMartGPT56ChatError, match="provider unavailable") as error:
        await provider.chat(
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.has_cost_evidence is True
    assert error.value.request_may_have_been_accepted is True


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_preserves_usage_when_2xx_response_has_no_content() -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {
                    "choices": [{"message": {"content": ""}}],
                    "usage": {
                        "prompt_tokens": 70,
                        "completion_tokens": 5,
                        "total_tokens": 75,
                    },
                }
            )
        ),
    )

    with pytest.raises(APIMartGPT56ChatError, match="no message content") as error:
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
    assert error.value.raw_usage_present is True
    assert error.value.usage_contract_valid is True
    assert error.value.has_cost_evidence is True
    assert error.value.request_may_have_been_accepted is True


@pytest.mark.asyncio
async def test_apimart_gpt56_chat_guards_replay_for_2xx_empty_content() -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(_FakeResponse({"choices": [{"message": {"content": ""}}]})),
    )

    with pytest.raises(APIMartGPT56ChatError, match="no message content") as error:
        await provider.chat(
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert bool(error.value.usage_result) is True
    assert error.value.usage_result["_usage_contract_missing"] is True
    assert error.value.raw_usage_present is False
    assert error.value.raw_cost_present is False
    assert error.value.has_cost_evidence is False
    assert error.value.request_may_have_been_accepted is True


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": "bad", "completion_tokens": 1, "total_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 0},
        {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3},
        {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "prompt_tokens_details": {"cached_tokens": 1},
        },
        {
            "prompt_tokens": 1,
            "completion_tokens": 0,
            "total_tokens": 1,
            "prompt_tokens_details": {
                "cached_tokens": 1,
                "cache_write_tokens": 1,
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_apimart_gpt56_chat_marks_untrusted_usage_contracts(
    usage: dict[str, object],
) -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {
                    "choices": [{"message": {"content": "Untrusted usage"}}],
                    "usage": usage,
                }
            )
        ),
    )

    result = await provider.chat(
        {
            "model": "gpt-5.6-luna",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert result["_usage_contract_valid"] is False
    assert "_usage_contract_missing" not in result


@pytest.mark.parametrize(
    "usage",
    [
        {
            "prompt_tokens": float("inf"),
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        {
            "prompt_tokens": 0,
            "completion_tokens": float("inf"),
            "total_tokens": 0,
        },
        {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": float("inf"),
        },
        {
            "prompt_tokens": 1,
            "completion_tokens": 0,
            "total_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": float("inf")},
        },
    ],
)
@pytest.mark.asyncio
async def test_apimart_gpt56_chat_marks_infinite_usage_on_ambiguous_http_error_untrusted(
    usage: dict[str, object],
) -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {
                    "error": {"message": "upstream failed after dispatch"},
                    "usage": usage,
                },
                status_code=503,
            )
        ),
    )

    with pytest.raises(APIMartGPT56ChatError) as error:
        await provider.chat(
            {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )

    assert error.value.usage_result["_usage_contract_valid"] is False
    assert error.value.usage_contract_valid is False
    assert error.value.request_may_have_been_accepted is True


@pytest.mark.parametrize(
    "credits",
    ["NaN", "Infinity", "-1", "not-a-number", "1e1000000"],
)
@pytest.mark.asyncio
async def test_apimart_gpt56_chat_marks_invalid_provider_cost_metadata(
    credits: str,
) -> None:
    provider = APIMartGPT56ChatProvider(
        api_key="unit-test-key",
        session=_FakeSession(
            _FakeResponse(
                {
                    "choices": [{"message": {"content": "Untrusted provider cost"}}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                    "credits": credits,
                }
            )
        ),
    )

    result = await provider.chat(
        {
            "model": "gpt-5.6-luna",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert result["_usage_contract_valid"] is False


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


@pytest.mark.parametrize("request_timeout", [900, -1, "nan"])
def test_apimart_gpt56_factory_rejects_timeouts_that_can_outlive_recovery(
    monkeypatch,
    request_timeout: object,
) -> None:
    monkeypatch.setattr(settings, "engine_apimart_api_key", "unit-test-key")
    monkeypatch.setattr(settings, "engine_aibrain_reservation_stale_minutes", 30)
    config = ProviderConfig(
        capability="chat",
        provider="apimart-gpt56",
        config={"request_timeout": request_timeout},
        is_active=True,
    )

    with pytest.raises(ProviderResolutionError):
        _apimart_gpt56_factory(config)


def test_aibrain_rate_defaults_are_declared_in_runtime_and_env_examples() -> None:
    assert settings.engine_aibrain_reservation_stale_minutes == 30
    assert settings.engine_aibrain_max_prompt_tokens == 922_000
    assert settings.engine_aibrain_inflight_exposure_multiplier == 2
    assert (
        settings.engine_aibrain_low_input_credits_per_1k,
        settings.engine_aibrain_low_output_credits_per_1k,
        settings.engine_aibrain_mid_input_credits_per_1k,
        settings.engine_aibrain_mid_output_credits_per_1k,
        settings.engine_aibrain_high_input_credits_per_1k,
        settings.engine_aibrain_high_output_credits_per_1k,
        settings.engine_aibrain_low_above_272k_input_credits_per_1k,
        settings.engine_aibrain_low_above_272k_output_credits_per_1k,
        settings.engine_aibrain_mid_above_272k_input_credits_per_1k,
        settings.engine_aibrain_mid_above_272k_output_credits_per_1k,
        settings.engine_aibrain_high_above_272k_input_credits_per_1k,
        settings.engine_aibrain_high_above_272k_output_credits_per_1k,
    ) == (
        Decimal("1.12"),
        Decimal("6.72"),
        Decimal("2.80"),
        Decimal("16.80"),
        Decimal("5.60"),
        Decimal("33.60"),
        Decimal("2.24"),
        Decimal("10.08"),
        Decimal("5.60"),
        Decimal("25.20"),
        Decimal("11.20"),
        Decimal("50.40"),
    )
    repository_root = Path(__file__).resolve().parents[2]
    expected_lines = {
        "ENGINE_AIBRAIN_LOW_INPUT_CREDITS_PER_1K=1.12",
        "ENGINE_AIBRAIN_LOW_OUTPUT_CREDITS_PER_1K=6.72",
        "ENGINE_AIBRAIN_MID_INPUT_CREDITS_PER_1K=2.80",
        "ENGINE_AIBRAIN_MID_OUTPUT_CREDITS_PER_1K=16.80",
        "ENGINE_AIBRAIN_HIGH_INPUT_CREDITS_PER_1K=5.60",
        "ENGINE_AIBRAIN_HIGH_OUTPUT_CREDITS_PER_1K=33.60",
        "ENGINE_AIBRAIN_LOW_ABOVE_272K_INPUT_CREDITS_PER_1K=2.24",
        "ENGINE_AIBRAIN_LOW_ABOVE_272K_OUTPUT_CREDITS_PER_1K=10.08",
        "ENGINE_AIBRAIN_MID_ABOVE_272K_INPUT_CREDITS_PER_1K=5.60",
        "ENGINE_AIBRAIN_MID_ABOVE_272K_OUTPUT_CREDITS_PER_1K=25.20",
        "ENGINE_AIBRAIN_HIGH_ABOVE_272K_INPUT_CREDITS_PER_1K=11.20",
        "ENGINE_AIBRAIN_HIGH_ABOVE_272K_OUTPUT_CREDITS_PER_1K=50.40",
        "ENGINE_AIBRAIN_MAX_PROMPT_TOKENS=922000",
        "ENGINE_AIBRAIN_INFLIGHT_EXPOSURE_MULTIPLIER=2",
        "ENGINE_AIBRAIN_RESERVATION_STALE_MINUTES=30",
    }
    for path in (
        repository_root / "backend" / ".env.example",
        repository_root / "infra" / ".env.example",
        repository_root / "infra" / ".env.prod.example",
    ):
        lines = set(path.read_text(encoding="utf-8").splitlines())
        assert expected_lines <= lines


@pytest.mark.parametrize(
    (
        "tier",
        "prompt_tokens",
        "expected_input",
        "expected_output",
    ),
    [
        ("low", 272_000, "1.12", "6.72"),
        ("low", 272_001, "2.24", "10.08"),
        ("mid", 272_000, "2.80", "16.80"),
        ("mid", 272_001, "5.60", "25.20"),
        ("high", 272_000, "5.60", "33.60"),
        ("high", 272_001, "11.20", "50.40"),
    ],
)
def test_aibrain_twelve_user_sale_rates_match_product_matrix(
    tier: str,
    prompt_tokens: int,
    expected_input: str,
    expected_output: str,
) -> None:
    rates = aibrain.user_token_rates(
        aibrain.tier_pricing(tier),
        prompt_tokens=prompt_tokens,
    )

    assert rates.input_credits_per_1k == Decimal(expected_input)
    assert rates.output_credits_per_1k == Decimal(expected_output)


@pytest.mark.parametrize(
    ("prompt_tokens", "expected_tier", "expected_credits", "expected_cost_cents"),
    [
        (272_000, "up_to_272k", "0.4448", 31),
        (272_001, "above_272k", "0.8848032", 62),
    ],
)
def test_provider_cost_boundary_is_frozen_independently_from_user_sale_rates(
    prompt_tokens: int,
    expected_tier: str,
    expected_credits: str,
    expected_cost_cents: int,
) -> None:
    cost = apimart_token_usage_cost(
        model="gpt-5.6-luna",
        prompt_tokens=prompt_tokens,
        completion_tokens=1_000,
        cached_prompt_tokens=0,
        cache_write_tokens=0,
    )

    assert cost.tier == expected_tier
    assert cost.credits == Decimal(expected_credits)
    assert cost.cost_cents == expected_cost_cents


@pytest.mark.parametrize(
    ("tier", "model", "expected_markup"),
    [
        ("low", "gpt-5.6-luna", "10"),
        ("mid", "gpt-5.6-terra", "2.5"),
        ("high", "gpt-5.6-sol", "2"),
    ],
)
@pytest.mark.parametrize("prompt_tokens", [272_000, 272_001])
def test_aibrain_user_sale_markup_policy_is_separate_from_provider_costs(
    tier: str,
    model: str,
    expected_markup: str,
    prompt_tokens: int,
) -> None:
    user_rate = aibrain.user_token_rates(
        aibrain.tier_pricing(tier),
        prompt_tokens=prompt_tokens,
    )
    provider_rate = apimart_token_rate(model=model, prompt_tokens=prompt_tokens)
    provider_cost_user_credits_per_1k = (
        Decimal(str(settings.engine_apimart_credit_usd))
        * Decimal(str(settings.engine_usd_cny_rate))
        * Decimal("100")
        / Decimal("1000")
    )

    input_markup = user_rate.input_credits_per_1k / (
        provider_rate.input_credits_per_m * provider_cost_user_credits_per_1k
    )
    output_markup = user_rate.output_credits_per_1k / (
        provider_rate.output_credits_per_m * provider_cost_user_credits_per_1k
    )

    assert {input_markup, output_markup} == {Decimal(expected_markup)}


@pytest.mark.parametrize(
    ("model", "expected_maximum_credits", "expected_cost_cents"),
    [
        ("gpt-5.6-luna", "5.5312", 387),
        ("gpt-5.6-terra", "55.312", 3_872),
        ("gpt-5.6-sol", "138.28", 9_680),
    ],
)
def test_aibrain_trusted_provider_cost_bounds_use_calibrated_public_rates(
    model: str,
    expected_maximum_credits: str,
    expected_cost_cents: int,
) -> None:
    maximum = aibrain._maximum_trusted_provider_usage_cost(model)

    assert maximum.credits == Decimal(expected_maximum_credits)
    assert maximum.cost_cents == expected_cost_cents


def test_aibrain_unknown_product_tier_fails_observably() -> None:
    with pytest.raises(ValueError, match="Unknown AIBRAIN tier"):
        aibrain.tier_pricing("future")


def test_user_sale_rate_selection_does_not_read_provider_cost_tiers(
    monkeypatch,
) -> None:
    def fail_if_provider_cost_tier_is_read(**_kwargs):
        raise APIMartTokenPricingError(
            "provider pricing unavailable",
            error_type="cost_tier_unconfigured",
        )

    pricing = aibrain.tier_pricing("low")
    monkeypatch.setattr(
        aibrain,
        "apimart_token_rate",
        fail_if_provider_cost_tier_is_read,
        raising=False,
    )

    standard = aibrain.user_token_rates(pricing, prompt_tokens=272_000)
    extended = aibrain.user_token_rates(pricing, prompt_tokens=272_001)

    assert (standard.input_credits_per_1k, standard.output_credits_per_1k) == (
        Decimal("1.12"),
        Decimal("6.72"),
    )
    assert (extended.input_credits_per_1k, extended.output_credits_per_1k) == (
        Decimal("2.24"),
        Decimal("10.08"),
    )
    assert aibrain._user_credits(pricing, 272_000, 1_000) == Decimal("311.360000")
    assert aibrain._user_credits(pricing, 272_001, 1_000) == Decimal("619.362240")


@pytest.mark.parametrize("prompt_tokens", [-1, True, 1.5])
def test_user_sale_rate_selection_rejects_invalid_prompt_tokens(
    prompt_tokens: object,
) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        aibrain.user_token_rates(
            aibrain.tier_pricing("low"),
            prompt_tokens=prompt_tokens,
        )


@pytest.mark.parametrize(
    ("estimated_prompt_tokens", "expected_reservation"),
    [
        (217_600, Decimal("311.360000")),
        (217_601, Decimal("619.364480")),
    ],
)
def test_aibrain_reservation_uses_the_buffered_prompt_token_rate_tier(
    monkeypatch,
    estimated_prompt_tokens: int,
    expected_reservation: Decimal,
) -> None:
    monkeypatch.setattr(
        aibrain,
        "_estimate_prompt_tokens",
        lambda _messages: estimated_prompt_tokens,
    )

    reservation = aibrain._reservation_credits(
        [{"role": "user", "content": "ignored by the patched estimator"}],
        pricing=aibrain.tier_pricing("low"),
        max_completion_tokens=1_000,
    )

    assert reservation == expected_reservation


def test_aibrain_exposure_limits_use_high_context_user_sale_rates() -> None:
    assert aibrain._max_single_request_exposure_credits() == Decimal("10532.838400")
    assert aibrain._tenant_inflight_exposure_limit() == Decimal("21065.676800")
