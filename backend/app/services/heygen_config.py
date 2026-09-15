from decimal import Decimal

from app.providers.avatar.heygen import HeyGenError, HeyGenNotConfigured


class HeyGenCostNotConfigured(HeyGenError):
    code = "HEYGEN_COST_NOT_CONFIGURED"


def configured_cost_snapshot(config, *, model):
    if not config.heygen_api_key.get_secret_value().strip():
        raise HeyGenNotConfigured("HeyGen service is not configured.")
    fields = {
        "avatar_iv": "engine_heygen_avatar_iv_cny_per_second",
        "lipsync_precision": "engine_heygen_precision_cny_per_second",
    }
    if model not in fields:
        raise HeyGenError("Unknown frozen avatar model.")
    value = getattr(config, fields[model])
    if value is None or not Decimal(value).is_finite() or Decimal(value) <= 0:
        raise HeyGenCostNotConfigured("HeyGen cost is not configured.")
    return {
        "source": "configured_estimate",
        "currency": "CNY",
        "cny_per_second": str(value),
        "provider": "heygen",
        "model": model,
    }
