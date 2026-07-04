from decimal import Decimal


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
