from decimal import Decimal


def test_apimart_cost_cents_uses_online_calibration_points(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_cost_cents_from_credits

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.20"))

    assert apimart_cost_cents_from_credits(Decimal("0.06")) == 4
    assert apimart_cost_cents_from_credits(Decimal("3.3")) == 238


def test_apimart_cost_cents_from_credits_uses_configured_rates(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_cost_cents_from_credits

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.20"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.50"))

    assert apimart_cost_cents_from_credits(Decimal("3.33")) == 500


def test_apimart_cost_cents_rounds_to_cny_cents(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_cost_cents_from_credits

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.20"))

    assert apimart_cost_cents_from_credits(Decimal("7.10")) == 511


def test_apimart_usage_metadata_treats_cost_as_usd_not_credits(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_usage_metadata

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.20"))

    metadata = apimart_usage_metadata(
        {"status": "completed", "result": {"usage": {"cost": "0.33", "credits": "3.3"}}}
    )

    assert metadata["credits"] == Decimal("3.3")
    assert metadata["cost_cents"] == 238


def test_apimart_usage_metadata_does_not_double_discount_usd_cost(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_usage_metadata

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.20"))

    metadata = apimart_usage_metadata(
        {"status": "completed", "result": {"usage": {"cost": "0.33"}}}
    )

    assert "credits" not in metadata
    assert "cost_cents" not in metadata


def test_apimart_price_table_fallback_uses_provider_credits_not_tenant_credits(
    monkeypatch,
) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_cost_cents_from_price_table

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.20"))

    assert apimart_cost_cents_from_price_table(model="gpt-image-2") == 4
    assert (
        apimart_cost_cents_from_price_table(
            model="doubao-seedance-2.0",
            resolution="480p",
            duration_sec=5,
        )
        == 238
    )
    assert (
        apimart_cost_cents_from_price_table(
            model="doubao-seedance-2.0",
            resolution="720p",
            duration_sec=5,
        )
        == 511
    )


def test_apimart_result_fallback_does_not_apply_to_openai_gpt_image(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_cost_cents_from_result

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.20"))

    assert apimart_cost_cents_from_result({"provider": "openai", "model": "gpt-image-2"}) == 0
