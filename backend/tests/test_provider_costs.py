from decimal import Decimal

import pytest


def test_provider_costs_use_provider_specific_bases(monkeypatch) -> None:
    from app.services import provider_costs

    monkeypatch.setattr(
        provider_costs.settings,
        "engine_omnihuman_cny_per_sec",
        Decimal("1.0"),
        raising=False,
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_omnihuman_change_lips_basic_cny_per_sec",
        Decimal("1.05"),
        raising=False,
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_seedtts_cny_per_char",
        Decimal("0.0003"),
        raising=False,
    )
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

    assert provider_costs.omnihuman_cost_cents(18) == 1800
    assert provider_costs.omnihuman_change_lips_cost_cents(18, tier="lite") == 540
    assert provider_costs.omnihuman_change_lips_cost_cents(18, tier="basic") == 1890
    assert provider_costs.seed_tts_cost_cents(100) == 3
    assert provider_costs.deepseek_cost_cents(
        prompt_tokens=100_000,
        completion_tokens=50_000,
    ) == 20


def test_tts_cost_basis_requires_an_explicit_supported_provider(monkeypatch) -> None:
    from app.services import provider_costs

    monkeypatch.setattr(
        provider_costs.settings,
        "engine_seedtts_cny_per_char",
        Decimal("0.0003"),
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_cosyvoice_tts_cny_per_char",
        Decimal("0.00015"),
    )

    assert provider_costs.tts_cost_cents(100, provider="doubao-seed-tts") == 3
    assert provider_costs.tts_cost_cents(100, provider="cosyvoice-tts") == 2
    with pytest.raises(ValueError, match="Unsupported TTS provider cost basis"):
        provider_costs.tts_cost_cents(100, provider="unpriced-tts")


def test_provider_costs_do_not_apply_usd_exchange_to_direct_cny(monkeypatch) -> None:
    from app.services import provider_costs

    monkeypatch.setattr(provider_costs.settings, "engine_usd_cny_rate", Decimal("99.0"))
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_omnihuman_cny_per_sec",
        Decimal("1.0"),
        raising=False,
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_seedtts_cny_per_char",
        Decimal("0.0003"),
        raising=False,
    )
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

    assert provider_costs.omnihuman_cost_cents(18) == 1800
    assert provider_costs.omnihuman_change_lips_cost_cents(18, tier="lite") == 540
    assert provider_costs.seed_tts_cost_cents(100) == 3
    assert provider_costs.deepseek_cost_cents(
        prompt_tokens=100_000,
        completion_tokens=50_000,
    ) == 20


def test_deepseek_usage_total_tokens_only_falls_back_to_input_rate(monkeypatch) -> None:
    from app.services import provider_costs

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

    usage = provider_costs.deepseek_usage_from_result(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "usage": {"total_tokens": 100_000},
        }
    )

    assert usage is not None
    assert usage.prompt_tokens == 100_000
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 100_000
    assert usage.cost_cents == 10


def test_deepseek_usage_preserves_prompt_cache_breakdown() -> None:
    from app.services import provider_costs

    usage = provider_costs.deepseek_usage_from_result(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "usage": {
                "prompt_tokens": 252,
                "completion_tokens": 122,
                "total_tokens": 374,
                "prompt_cache_hit_tokens": 128,
                "prompt_cache_miss_tokens": 124,
            },
        }
    )

    assert usage is not None
    assert usage.prompt_cache_hit_tokens == 128
    assert usage.prompt_cache_miss_tokens == 124
    assert usage.cache_tokens_reported is True


def test_deepseek_usage_prices_cache_hits_at_the_cache_rate() -> None:
    from app.services import provider_costs

    usage = provider_costs.deepseek_usage_from_result(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "usage": {
                "prompt_tokens": 252,
                "completion_tokens": 122,
                "total_tokens": 374,
                "prompt_cache_hit_tokens": 128,
                "prompt_cache_miss_tokens": 124,
            },
        }
    )

    assert usage is not None
    assert usage.cost_cny == Decimal("0.00037352448")
    assert usage.cost_cents == 0


def test_deepseek_partial_cache_breakdown_falls_back_to_all_miss(
    monkeypatch,
) -> None:
    from app.services import provider_costs

    warnings: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        provider_costs.logger,
        "warning",
        lambda event, **details: warnings.append((event, details)),
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_deepseek_cny_per_1k_input",
        Decimal("0.001"),
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_deepseek_cny_per_1k_cache_hit",
        Decimal("0.0001"),
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_deepseek_cny_per_1k_output",
        Decimal("0.002"),
    )

    usage = provider_costs.deepseek_usage_from_result(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "prompt_cache_hit_tokens": 20,
            },
        }
    )

    assert usage is not None
    assert usage.prompt_cache_hit_tokens == 0
    assert usage.prompt_cache_miss_tokens == 100
    assert usage.cache_tokens_reported is False
    assert usage.cost_cny == Decimal("0.00012")
    assert warnings == [
        (
            "deepseek_cache_usage_unavailable",
            {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "prompt_tokens": 100,
            },
        )
    ]


def test_deepseek_unknown_model_never_inherits_another_models_rate() -> None:
    from app.services import provider_costs

    with pytest.raises(provider_costs.DeepSeekCostError) as exc_info:
        provider_costs.deepseek_cost_cny(
            model="deepseek-next",
            prompt_tokens=100,
            completion_tokens=50,
        )

    assert exc_info.value.error_type == "unknown_model"


def test_deepseek_cache_breakdown_must_equal_prompt_tokens() -> None:
    from app.services import provider_costs

    with pytest.raises(provider_costs.DeepSeekCostError) as exc_info:
        provider_costs.deepseek_cost_cny(
            model="deepseek-v4-flash",
            prompt_tokens=252,
            completion_tokens=122,
            prompt_cache_hit_tokens=128,
            prompt_cache_miss_tokens=123,
        )

    assert exc_info.value.error_type == "invalid_cache_usage"


def test_deepseek_aggregate_treats_legacy_usage_without_cache_fields_as_miss(
    monkeypatch,
) -> None:
    from app.services import provider_costs

    monkeypatch.setattr(
        provider_costs.settings,
        "engine_deepseek_cny_per_1k_input",
        Decimal("0.001"),
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_deepseek_cny_per_1k_output",
        Decimal("0.002"),
    )
    legacy_usage = provider_costs.DeepSeekUsageCost(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_cents=0,
    )

    aggregate = provider_costs.aggregate_deepseek_usages([legacy_usage])

    assert aggregate is not None
    assert aggregate.prompt_cache_hit_tokens == 0
    assert aggregate.prompt_cache_miss_tokens == 100
    assert aggregate.cache_tokens_reported is False
    assert aggregate.cost_cny == Decimal("0.0002")


def test_deepseek_aggregate_rejects_mixed_models() -> None:
    from app.services import provider_costs

    first = provider_costs.DeepSeekUsageCost(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_cents=0,
    )
    second = provider_costs.DeepSeekUsageCost(
        provider="deepseek",
        model="deepseek-next",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_cents=0,
    )

    with pytest.raises(provider_costs.DeepSeekCostError) as exc_info:
        provider_costs.aggregate_deepseek_usages([first, second])

    assert exc_info.value.error_type == "mixed_usage"


def test_deepseek_provider_cost_does_not_change_copy_credits(
    auth_context,
    auth_db,
) -> None:
    from app.services import provider_costs, quota

    usage = provider_costs.deepseek_usage_from_result(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "usage": {
                "prompt_tokens": 252,
                "completion_tokens": 122,
                "total_tokens": 374,
                "prompt_cache_hit_tokens": 128,
                "prompt_cache_miss_tokens": 124,
            },
        }
    )
    assert usage is not None

    with auth_db() as db:
        subscription = quota.active_subscription(db, auth_context["tenant_id"])
        used_before = subscription.quota_credits_used
        record = quota.charge_copy_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            provider="deepseek",
            llm_usage=usage,
        )
        db.commit()
        db.refresh(subscription)

        assert record.credits == Decimal("1.00")
        assert record.cost_cents == usage.cost_cents
        assert subscription.quota_credits_used == used_before + 1


@pytest.mark.parametrize(
    ("input_rate", "cache_hit_rate", "output_rate", "expected_cost_cny"),
    (
        (
            "0.001008",
            "0.00002016",
            "0.002016",
            "0.00184375296",
        ),
        (
            "0.001",
            "0.00002",
            "0.002",
            "0.00182912",
        ),
    ),
    ids=("configured-rates", "billing-page-rates"),
)
def test_deepseek_cache_formula_handles_five_paid_calls(
    monkeypatch,
    input_rate: str,
    cache_hit_rate: str,
    output_rate: str,
    expected_cost_cny: str,
) -> None:
    from app.services import provider_costs

    monkeypatch.setattr(
        provider_costs.settings,
        "engine_deepseek_cny_per_1k_input",
        Decimal(input_rate),
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_deepseek_cny_per_1k_cache_hit",
        Decimal(cache_hit_rate),
    )
    monkeypatch.setattr(
        provider_costs.settings,
        "engine_deepseek_cny_per_1k_output",
        Decimal(output_rate),
    )
    calls = (
        (59, 111, 0, 59),
        (89, 115, 0, 89),
        (154, 103, 0, 154),
        (252, 122, 128, 124),
        (384, 120, 128, 256),
    )
    usages = [
        provider_costs.deepseek_usage_from_result(
            {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                    "prompt_cache_hit_tokens": cache_hit_tokens,
                    "prompt_cache_miss_tokens": cache_miss_tokens,
                },
            }
        )
        for (
            prompt_tokens,
            completion_tokens,
            cache_hit_tokens,
            cache_miss_tokens,
        ) in calls
    ]

    aggregate = provider_costs.aggregate_deepseek_usages(
        [usage for usage in usages if usage is not None]
    )

    assert aggregate is not None
    assert aggregate.total_tokens == 1_509
    assert aggregate.prompt_cache_hit_tokens == 256
    assert aggregate.prompt_cache_miss_tokens == 682
    assert aggregate.cost_cny == Decimal(expected_cost_cny)
    assert aggregate.cost_cents == 0
