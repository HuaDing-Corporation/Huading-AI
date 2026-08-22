from decimal import Decimal

import pytest


@pytest.mark.parametrize(
    (
        "model",
        "prompt_tokens",
        "official_input",
        "official_cached",
        "official_write",
        "official_output",
    ),
    [
        ("gpt-5.6-luna", 272_000, "2", "0.2", "2.5", "12"),
        ("gpt-5.6-luna", 272_001, "4", "0.4", "5", "18"),
        ("gpt-5.6-terra", 272_000, "20", "2", "25", "120"),
        ("gpt-5.6-terra", 272_001, "40", "4", "50", "180"),
        ("gpt-5.6-sol", 272_000, "50", "5", "62.5", "300"),
        ("gpt-5.6-sol", 272_001, "100", "10", "125", "450"),
    ],
)
def test_gpt56_rates_match_apimart_published_our_price_column(
    model: str,
    prompt_tokens: int,
    official_input: str,
    official_cached: str,
    official_write: str,
    official_output: str,
) -> None:
    from app.services.apimart_token_pricing import (
        APIMART_TOKEN_PRICING_COLUMN_LABEL,
        APIMART_TOKEN_PRICING_SOURCE_URL,
        APIMART_TOKEN_PRICING_VERIFIED_ON,
        apimart_token_rate,
    )

    rate = apimart_token_rate(model=model, prompt_tokens=prompt_tokens)
    discount = Decimal("0.8")

    assert APIMART_TOKEN_PRICING_SOURCE_URL == "https://apib.ai/zh/pricing"
    assert APIMART_TOKEN_PRICING_VERIFIED_ON == "2026-08-06"
    assert APIMART_TOKEN_PRICING_COLUMN_LABEL == "我们的价格"
    assert rate.input_credits_per_m == Decimal(official_input) * discount
    assert rate.cached_input_credits_per_m == Decimal(official_cached) * discount
    assert rate.cache_write_credits_per_m == Decimal(official_write) * discount
    assert rate.output_credits_per_m == Decimal(official_output) * discount


def test_gpt56_models_share_verified_public_usage_limits() -> None:
    from app.services.apimart_token_pricing import (
        APIMART_GPT56_USAGE_LIMITS,
        apimart_token_usage_limits,
    )

    for model in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"):
        assert apimart_token_usage_limits(model=model) is APIMART_GPT56_USAGE_LIMITS

    assert APIMART_GPT56_USAGE_LIMITS.max_prompt_tokens == 922_000
    assert APIMART_GPT56_USAGE_LIMITS.max_completion_tokens == 128_000
    assert APIMART_GPT56_USAGE_LIMITS.max_total_tokens == 1_050_000


@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens", "total_tokens", "expected"),
    [
        (922_000, 128_000, 1_050_000, True),
        (922_001, 0, 922_001, False),
        (0, 128_001, 128_001, False),
        (922_000, 128_000, 1_050_001, False),
        (1, 1, 3, False),
        (-1, 1, 0, False),
    ],
)
def test_gpt56_usage_reports_must_be_self_consistent_and_within_public_limits(
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    expected: bool,
) -> None:
    from app.services.apimart_token_pricing import (
        apimart_token_usage_is_within_limits,
    )

    assert (
        apimart_token_usage_is_within_limits(
            model="gpt-5.6-sol",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        is expected
    )


@pytest.mark.parametrize(
    "raw_usage",
    [
        {"prompt_tokens_details": {"cached_tokens": float("inf")}},
        {"prompt_tokens_details": {"cache_write_tokens": float("inf")}},
        {"claude_cache_creation_5_m_tokens": float("inf")},
        {"cache_creation": {"ephemeral_1h_input_tokens": float("inf")}},
    ],
)
def test_cache_usage_marks_infinite_provider_counters_invalid(
    raw_usage: dict[str, object],
) -> None:
    from app.services.apimart_token_pricing import apimart_cache_token_usage

    cache_usage = apimart_cache_token_usage(raw_usage)

    assert cache_usage.contract_valid is False


@pytest.mark.parametrize(
    ("field", "expected_prompt_tokens", "expected_completion_tokens"),
    [
        ("prompt_tokens", 0, 1),
        ("completion_tokens", 1, 0),
    ],
)
def test_token_pricing_safely_normalizes_infinite_token_counts(
    field: str,
    expected_prompt_tokens: int,
    expected_completion_tokens: int,
) -> None:
    from app.services.apimart_token_pricing import apimart_token_usage_cost

    counts = {"prompt_tokens": 1, "completion_tokens": 1}
    counts[field] = float("inf")

    cost = apimart_token_usage_cost(model="gpt-5.6-luna", **counts)

    assert cost.prompt_tokens == expected_prompt_tokens
    assert cost.completion_tokens == expected_completion_tokens


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
    assert cost.input_credits_per_m == Decimal("1.6")
    assert cost.output_credits_per_m == Decimal("9.6")
    assert cost.credits == Decimal("0.002984")
    assert cost.cost_cents == 0


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
    assert low.credits == Decimal("0.4448")
    assert high.tier == "above_272k"
    assert high.input_credits_per_m == Decimal("3.2")
    assert high.output_credits_per_m == Decimal("14.4")
    assert high.credits == Decimal("0.8848032")


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
        ("gpt-5.6-terra", 1, "16", "1.6", "20", "96"),
        ("gpt-5.6-terra", 272_001, "32", "3.2", "40", "144"),
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


def test_mini_transcribe_uses_its_verified_apimart_token_rates() -> None:
    from app.services.apimart_token_pricing import apimart_token_usage_cost

    cost = apimart_token_usage_cost(
        model="gpt-4o-mini-transcribe",
        prompt_tokens=300,
        completion_tokens=95,
    )

    assert cost.input_credits_per_m == Decimal("10")
    assert cost.cached_input_credits_per_m is None
    assert cost.cache_write_credits_per_m is None
    assert cost.output_credits_per_m == Decimal("40")
    assert cost.credits == Decimal("0.0068")
    assert cost.cost_source == "token_formula"
    assert cost.cost_estimate_uncertain is False


def test_full_transcribe_keeps_its_separate_verified_token_rates() -> None:
    from app.services.apimart_token_pricing import apimart_token_rate

    rate = apimart_token_rate(model="gpt-4o-transcribe")

    assert rate.input_credits_per_m == Decimal("20")
    assert rate.cached_input_credits_per_m is None
    assert rate.cache_write_credits_per_m is None
    assert rate.output_credits_per_m == Decimal("80")


def test_cache_read_and_write_tokens_use_their_own_rates() -> None:
    from app.services.apimart_token_pricing import apimart_token_usage_cost

    cost = apimart_token_usage_cost(
        model="gpt-5.6-luna",
        prompt_tokens=1_000,
        completion_tokens=500,
        cached_prompt_tokens=400,
        cache_write_tokens=100,
    )

    assert cost.credits == Decimal("0.005864")
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

    assert cost.credits == Decimal("0.0064")
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


@pytest.mark.parametrize("credits", ["NaN", "Infinity", "-Infinity"])
def test_authoritative_credits_must_be_finite(credits: str) -> None:
    from app.services.apimart_token_pricing import (
        APIMartTokenPricingError,
        apimart_token_usage_cost,
    )

    with pytest.raises(APIMartTokenPricingError) as exc_info:
        apimart_token_usage_cost(
            model="gpt-5.6-luna",
            prompt_tokens=1,
            completion_tokens=1,
            authoritative_credits=credits,
        )

    assert exc_info.value.error_type == "invalid_usage_metadata"


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


def test_authoritative_credits_do_not_bypass_invalid_cache_usage() -> None:
    from app.services.apimart_token_pricing import (
        APIMartTokenPricingError,
        apimart_token_usage_cost,
    )

    with pytest.raises(APIMartTokenPricingError) as exc_info:
        apimart_token_usage_cost(
            model="gpt-5.6-luna",
            prompt_tokens=10,
            completion_tokens=0,
            cached_prompt_tokens=8,
            cache_write_tokens=8,
            authoritative_credits=Decimal("0.5"),
        )

    assert exc_info.value.error_type == "invalid_usage_metadata"
