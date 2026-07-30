from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.core.config import settings
from app.services.apimart_costs import apimart_cost_cents_from_credits

APIMART_TOKEN_PRICING_SOURCE_URL = "https://apib.ai/zh/pricing"
APIMART_TOKEN_PRICING_VERIFIED_ON = "2026-07-30"
_TOKENS_PER_MILLION = Decimal("1000000")


class APIMartTokenPricingError(ValueError):
    def __init__(self, message: str, *, error_type: str) -> None:
        self.error_type = error_type
        super().__init__(message)


@dataclass(frozen=True)
class _TokenRateTier:
    name: str
    max_input_tokens: int | None
    input_credits_per_m: Decimal
    cached_input_credits_per_m: Decimal
    cache_write_credits_per_m: Decimal
    output_credits_per_m: Decimal


@dataclass(frozen=True)
class APIMartTokenUsageCost:
    model: str
    tier: str
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int | None
    cache_write_tokens: int | None
    input_credits_per_m: Decimal
    cached_input_credits_per_m: Decimal
    cache_write_credits_per_m: Decimal
    output_credits_per_m: Decimal
    credits: Decimal
    cost_usd: Decimal
    cost_cents: int
    cost_source: str
    cache_tokens_reported: bool
    cache_write_tokens_reported: bool
    cost_estimate_uncertain: bool


# APIMart effective rates after its 0.8 price factor, represented as
# provider Credits per 1M tokens. Official list prices are intentionally absent.
_TOKEN_RATE_TIERS_BY_MODEL: dict[str, tuple[_TokenRateTier, ...]] = {
    "gpt-5.6-luna": (
        _TokenRateTier(
            name="up_to_272k",
            max_input_tokens=272_000,
            input_credits_per_m=Decimal("8"),
            cached_input_credits_per_m=Decimal("0.8"),
            cache_write_credits_per_m=Decimal("10"),
            output_credits_per_m=Decimal("48"),
        ),
        _TokenRateTier(
            name="above_272k",
            max_input_tokens=None,
            input_credits_per_m=Decimal("16"),
            cached_input_credits_per_m=Decimal("1.6"),
            cache_write_credits_per_m=Decimal("20"),
            output_credits_per_m=Decimal("72"),
        ),
    ),
    "gpt-5.6-terra": (
        _TokenRateTier(
            name="up_to_272k",
            max_input_tokens=272_000,
            input_credits_per_m=Decimal("20"),
            cached_input_credits_per_m=Decimal("2"),
            cache_write_credits_per_m=Decimal("25"),
            output_credits_per_m=Decimal("120"),
        ),
        _TokenRateTier(
            name="above_272k",
            max_input_tokens=None,
            input_credits_per_m=Decimal("40"),
            cached_input_credits_per_m=Decimal("4"),
            cache_write_credits_per_m=Decimal("50"),
            output_credits_per_m=Decimal("180"),
        ),
    ),
    "gpt-5.6-sol": (
        _TokenRateTier(
            name="up_to_272k",
            max_input_tokens=272_000,
            input_credits_per_m=Decimal("40"),
            cached_input_credits_per_m=Decimal("4"),
            cache_write_credits_per_m=Decimal("50"),
            output_credits_per_m=Decimal("240"),
        ),
        _TokenRateTier(
            name="above_272k",
            max_input_tokens=None,
            input_credits_per_m=Decimal("80"),
            cached_input_credits_per_m=Decimal("8"),
            cache_write_credits_per_m=Decimal("100"),
            output_credits_per_m=Decimal("360"),
        ),
    ),
}


def apimart_token_usage_cost(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    authoritative_credits: Decimal | int | float | str | None = None,
) -> APIMartTokenUsageCost:
    normalized_model = str(model or "").strip().lower()
    safe_prompt_tokens = _nonnegative_int(prompt_tokens)
    safe_completion_tokens = _nonnegative_int(completion_tokens)
    tier = _rate_tier(normalized_model, safe_prompt_tokens)
    cached_tokens = _optional_nonnegative_int(cached_prompt_tokens)
    write_tokens = _optional_nonnegative_int(cache_write_tokens)
    reported_special_tokens = (cached_tokens or 0) + (write_tokens or 0)
    if reported_special_tokens > safe_prompt_tokens:
        raise APIMartTokenPricingError(
            "APIMart cached and cache-write tokens exceed prompt tokens.",
            error_type="invalid_usage_metadata",
        )

    provider_credits = _optional_decimal(authoritative_credits)
    if provider_credits is not None:
        if provider_credits < 0:
            raise APIMartTokenPricingError(
                "APIMart provider credits cannot be negative.",
                error_type="invalid_usage_metadata",
            )
        credits = provider_credits
        cost_source = "provider_credits"
        uncertain = False
    else:
        regular_input_tokens = safe_prompt_tokens - reported_special_tokens
        credits = (
            Decimal(regular_input_tokens) * tier.input_credits_per_m
            + Decimal(cached_tokens or 0) * tier.cached_input_credits_per_m
            + Decimal(write_tokens or 0) * tier.cache_write_credits_per_m
            + Decimal(safe_completion_tokens) * tier.output_credits_per_m
        ) / _TOKENS_PER_MILLION
        cost_source = "token_formula"
        uncertain = safe_prompt_tokens > 0 and (
            cached_tokens is None or write_tokens is None
        )

    cost_usd = (
        credits * Decimal(str(settings.engine_apimart_credit_usd))
    ).quantize(Decimal("0.00000001"))
    return APIMartTokenUsageCost(
        model=normalized_model,
        tier=tier.name,
        prompt_tokens=safe_prompt_tokens,
        completion_tokens=safe_completion_tokens,
        cached_prompt_tokens=cached_tokens,
        cache_write_tokens=write_tokens,
        input_credits_per_m=tier.input_credits_per_m,
        cached_input_credits_per_m=tier.cached_input_credits_per_m,
        cache_write_credits_per_m=tier.cache_write_credits_per_m,
        output_credits_per_m=tier.output_credits_per_m,
        credits=credits,
        cost_usd=cost_usd,
        cost_cents=apimart_cost_cents_from_credits(credits),
        cost_source=cost_source,
        cache_tokens_reported=cached_tokens is not None,
        cache_write_tokens_reported=write_tokens is not None,
        cost_estimate_uncertain=uncertain,
    )


def _rate_tier(model: str, prompt_tokens: int) -> _TokenRateTier:
    tiers = _TOKEN_RATE_TIERS_BY_MODEL.get(model)
    if tiers is None:
        raise APIMartTokenPricingError(
            f"APIMart token pricing is not configured for model '{model}'.",
            error_type="cost_model_unconfigured",
        )
    for tier in tiers:
        if tier.max_input_tokens is None or prompt_tokens <= tier.max_input_tokens:
            return tier
    raise APIMartTokenPricingError(
        f"APIMart token pricing tier is not configured for model '{model}' "
        f"at {prompt_tokens} input tokens.",
        error_type="cost_tier_unconfigured",
    )


def _nonnegative_int(value: int | None) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_nonnegative_int(value: int | None) -> int | None:
    if value in (None, ""):
        return None
    return _nonnegative_int(value)


def _optional_decimal(
    value: Decimal | int | float | str | None,
) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise APIMartTokenPricingError(
            "APIMart provider credits are invalid.",
            error_type="invalid_usage_metadata",
        ) from exc
