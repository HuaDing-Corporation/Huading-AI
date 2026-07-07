from decimal import Decimal


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
