from decimal import Decimal

import pytest


def test_luna_low_tier_uses_apimart_discounted_token_rates(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_token_pricing import apimart_token_usage_cost

    monkeypatch.setattr(
        apimart_costs.settings,
        "engine_apimart_credit_usd",
        Decimal("0.10"),
    )
    monkeypatch.setattr(
        apimart_costs.settings,
        "engine_usd_cny_rate",
        Decimal("7.0"),
    )

    cost = apimart_token_usage_cost(
        model="gpt-5.6-luna",
        prompt_tokens=665,
        completion_tokens=200,
        cached_prompt_tokens=0,
        cache_write_tokens=0,
    )

    assert cost.tier == "up_to_272k"
    assert cost.input_credits_per_m == Decimal("8")
    assert cost.output_credits_per_m == Decimal("48")
    assert cost.credits == Decimal("0.01492")
    assert cost.cost_cents == 1


def test_luna_switches_rate_tier_only_above_272k_input_tokens() -> None:
    from app.services.apimart_token_pricing import apimart_token_usage_cost

    low = apimart_token_usage_cost(
        model="gpt-5.6-luna",
        prompt_tokens=272_000,
        completion_tokens=1_000,
        cached_prompt_tokens=0,
        cache_write_tokens=0,
    )
    high = apimart_token_usage_cost(
        model="gpt-5.6-luna",
        prompt_tokens=272_001,
        completion_tokens=1_000,
        cached_prompt_tokens=0,
        cache_write_tokens=0,
    )

    assert low.tier == "up_to_272k"
    assert low.credits == Decimal("2.224")
    assert high.tier == "above_272k"
    assert high.input_credits_per_m == Decimal("16")
    assert high.output_credits_per_m == Decimal("72")
    assert high.credits == Decimal("4.424016")


@pytest.mark.parametrize(
    (
        "model",
        "prompt_tokens",
        "expected_input",
        "expected_cached",
        "expected_write",
        "expected_output",
    ),
    [
        ("gpt-5.6-terra", 1, "20", "2", "25", "120"),
        ("gpt-5.6-terra", 272_001, "40", "4", "50", "180"),
        ("gpt-5.6-sol", 1, "40", "4", "50", "240"),
        ("gpt-5.6-sol", 272_001, "80", "8", "100", "360"),
    ],
)
def test_gpt56_models_keep_separate_apimart_rate_tiers(
    model: str,
    prompt_tokens: int,
    expected_input: str,
    expected_cached: str,
    expected_write: str,
    expected_output: str,
) -> None:
    from app.services.apimart_token_pricing import apimart_token_usage_cost

    cost = apimart_token_usage_cost(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=0,
        cached_prompt_tokens=0,
        cache_write_tokens=0,
    )

    assert cost.input_credits_per_m == Decimal(expected_input)
    assert cost.cached_input_credits_per_m == Decimal(expected_cached)
    assert cost.cache_write_credits_per_m == Decimal(expected_write)
    assert cost.output_credits_per_m == Decimal(expected_output)


def test_cache_read_and_write_tokens_use_their_own_rates() -> None:
    from app.services.apimart_token_pricing import apimart_token_usage_cost

    cost = apimart_token_usage_cost(
        model="gpt-5.6-luna",
        prompt_tokens=1_000,
        completion_tokens=500,
        cached_prompt_tokens=400,
        cache_write_tokens=100,
    )

    assert cost.credits == Decimal("0.02932")
    assert cost.cache_tokens_reported is True
    assert cost.cache_write_tokens_reported is True
    assert cost.cost_estimate_uncertain is False


def test_missing_cache_metadata_is_explicitly_uncertain() -> None:
    from app.services.apimart_token_pricing import apimart_token_usage_cost

    cost = apimart_token_usage_cost(
        model="gpt-5.6-luna",
        prompt_tokens=1_000,
        completion_tokens=500,
    )

    assert cost.credits == Decimal("0.032")
    assert cost.cache_tokens_reported is False
    assert cost.cache_write_tokens_reported is False
    assert cost.cost_estimate_uncertain is True


def test_authoritative_credits_override_token_estimate() -> None:
    from app.services.apimart_token_pricing import apimart_token_usage_cost

    cost = apimart_token_usage_cost(
        model="gpt-5.6-luna",
        prompt_tokens=1_000,
        completion_tokens=500,
        authoritative_credits=Decimal("1.25"),
    )

    assert cost.credits == Decimal("1.25")
    assert cost.cost_cents == 88
    assert cost.cost_source == "provider_credits"
    assert cost.cost_estimate_uncertain is False


def test_unknown_model_never_inherits_another_models_rate() -> None:
    from app.services.apimart_token_pricing import (
        APIMartTokenPricingError,
        apimart_token_usage_cost,
    )

    with pytest.raises(APIMartTokenPricingError) as exc_info:
        apimart_token_usage_cost(
            model="gpt-5.7-unpriced",
            prompt_tokens=1_000,
            completion_tokens=500,
        )

    assert exc_info.value.error_type == "cost_model_unconfigured"


def test_overlapping_cache_usage_is_rejected() -> None:
    from app.services.apimart_token_pricing import (
        APIMartTokenPricingError,
        apimart_token_usage_cost,
    )

    with pytest.raises(APIMartTokenPricingError) as exc_info:
        apimart_token_usage_cost(
            model="gpt-5.6-luna",
            prompt_tokens=100,
            completion_tokens=0,
            cached_prompt_tokens=80,
            cache_write_tokens=30,
        )

    assert exc_info.value.error_type == "invalid_usage_metadata"
