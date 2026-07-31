from decimal import Decimal

import pytest


def test_apimart_cost_cents_uses_online_calibration_points(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_cost_cents_from_credits

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))

    assert apimart_cost_cents_from_credits(Decimal("0.06")) == 4
    assert apimart_cost_cents_from_credits(Decimal("3.3")) == 231


@pytest.mark.parametrize(
    ("provider_credits", "expected_cost_cents"),
    [
        (Decimal("0.17902"), 13),
        (Decimal("0.50374"), 35),
        (Decimal("1.05248"), 74),
    ],
)
def test_apimart_cost_cents_matches_reverse_prompt_video_bills(
    monkeypatch,
    provider_credits: Decimal,
    expected_cost_cents: int,
) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_cost_cents_from_credits

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))

    assert apimart_cost_cents_from_credits(provider_credits) == expected_cost_cents


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
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))

    assert apimart_cost_cents_from_credits(Decimal("7.10")) == 497


def test_apimart_usage_metadata_treats_cost_as_usd_not_credits(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_usage_metadata

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))

    metadata = apimart_usage_metadata(
        {"status": "completed", "result": {"usage": {"cost": "0.33", "credits": "3.3"}}}
    )

    assert metadata["credits"] == Decimal("3.3")
    assert metadata["cost_cents"] == 231


def test_apimart_usage_metadata_does_not_double_discount_usd_cost(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_usage_metadata

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))

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
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))

    assert apimart_cost_cents_from_price_table(model="gpt-image-2") == 6
    assert (
        apimart_cost_cents_from_price_table(
            model="doubao-seedance-2.0",
            resolution="480p",
            duration_sec=5,
        )
        == 231
    )
    assert (
        apimart_cost_cents_from_price_table(
            model="doubao-seedance-2.0",
            resolution="720p",
            duration_sec=5,
        )
        == 497
    )


@pytest.mark.parametrize(
    ("resolution", "expected_cost_cents"),
    [("1k", 6), ("2k", 10), ("4k", 15)],
)
def test_apimart_gpt_image_fallback_uses_resolution_price(
    monkeypatch,
    resolution: str,
    expected_cost_cents: int,
) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_cost_cents_from_price_table

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))

    assert (
        apimart_cost_cents_from_price_table(
            model="gpt-image-2",
            resolution=resolution,
        )
        == expected_cost_cents
    )


def test_apimart_result_fallback_does_not_apply_to_openai_gpt_image(monkeypatch) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_cost_cents_from_result

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))

    assert apimart_cost_cents_from_result({"provider": "openai", "model": "gpt-image-2"}) == 0


def test_apimart_image_authoritative_credits_override_resolution_fallback(
    monkeypatch,
) -> None:
    from app.services import apimart_costs
    from app.services.apimart_costs import apimart_cost_cents_from_result

    monkeypatch.setattr(apimart_costs.settings, "engine_apimart_credit_usd", Decimal("0.10"))
    monkeypatch.setattr(apimart_costs.settings, "engine_usd_cny_rate", Decimal("7.0"))

    assert (
        apimart_cost_cents_from_result(
            {
                "provider": "apimart",
                "model": "gpt-image-2",
                "resolution": "4k",
                "credits": Decimal("0.085"),
            }
        )
        == 6
    )
    assert (
        apimart_cost_cents_from_result(
            {
                "provider": "apimart",
                "model": "gpt-image-2",
                "resolution": "4k",
            }
        )
        == 15
    )
