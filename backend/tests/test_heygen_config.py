from decimal import Decimal

import pytest

from app.core.config import Settings


def test_heygen_missing_credentials_and_costs_have_no_usable_defaults():
    config = Settings(_env_file=None, jwt_secret_key="test-only-configuration-value-0000")
    assert not config.heygen_api_key.get_secret_value()
    assert config.engine_heygen_avatar_iv_cny_per_second is None
    assert config.engine_heygen_precision_cny_per_second is None


@pytest.mark.parametrize("rate", ["0", "-1", "NaN", "Infinity"])
def test_heygen_rejects_invalid_configured_cost(rate):
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            jwt_secret_key="test-only-configuration-value-0000",
            engine_heygen_avatar_iv_cny_per_second=rate,
        )


def test_heygen_configured_cost_is_explicit_estimate_not_observed_cost():
    from app.services.heygen_config import configured_cost_snapshot

    config = Settings(
        _env_file=None,
        jwt_secret_key="test-only-configuration-value-0000",
        heygen_api_key="fixture-only",
        engine_heygen_avatar_iv_cny_per_second=Decimal("0.35"),
    )
    result = configured_cost_snapshot(config, model="avatar_iv")
    assert result == {
        "source": "configured_estimate",
        "currency": "CNY",
        "cny_per_second": "0.35",
        "provider": "heygen",
        "model": "avatar_iv",
    }
