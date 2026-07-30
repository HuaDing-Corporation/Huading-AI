from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.core.config import settings

_CREDIT_KEYS = {
    "credits",
    "credit",
    "used_credits",
    "usage_credits",
    "task_credits",
    "credits_cost",
}
_COST_CENTS_KEYS = {"cost_cents", "cny_cost_cents", "cost_cent"}
_IMAGE_CREDITS_BY_MODEL_PREFIX = {
    "gpt-image": {
        "1k": Decimal("0.085"),
        "2k": Decimal("0.14"),
        "4k": Decimal("0.21"),
    },
}
_VIDEO_CREDITS_PER_5_SECONDS_BY_RESOLUTION = {
    "480p": Decimal("3.3"),
    "720p": Decimal("7.1"),
    "1080p": Decimal("17.72"),
}


def apimart_cost_cents_from_credits(credits: Decimal | int | float | str | None) -> int:
    credit_amount = _decimal_or_none(credits)
    if credit_amount is None or credit_amount <= 0:
        return 0
    cost = (
        credit_amount
        * Decimal(str(settings.engine_apimart_credit_usd))
        * Decimal(str(settings.engine_usd_cny_rate))
        * Decimal("100")
    )
    return max(0, int(cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def apimart_usage_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    credits = _decimal_or_none(_first_nested_value(payload, _CREDIT_KEYS))
    cost_cents = _int_or_none(_first_nested_value(payload, _COST_CENTS_KEYS))
    if cost_cents is None and credits is not None:
        cost_cents = apimart_cost_cents_from_credits(credits)

    metadata: dict[str, Any] = {}
    if credits is not None:
        metadata["credits"] = credits
    if cost_cents is not None:
        metadata["cost_cents"] = cost_cents
    return metadata


def apimart_cost_cents_from_result(result: Mapping[str, Any] | None) -> int:
    if not result:
        return 0
    cost_cents = _int_or_none(result.get("cost_cents"))
    if cost_cents is not None and cost_cents > 0:
        return cost_cents

    credits = _decimal_or_none(result.get("credits"))
    if credits is not None:
        return apimart_cost_cents_from_credits(credits)

    provider = str(result.get("provider") or "").strip().lower()
    if provider and provider != "apimart":
        return 0

    return apimart_cost_cents_from_price_table(
        model=str(result.get("model") or ""),
        resolution=result.get("resolution"),
        duration_sec=result.get("duration_sec", result.get("duration")),
    )


def apimart_cost_cents_from_price_table(
    *,
    model: str,
    resolution: Any | None = None,
    duration_sec: Any | None = None,
) -> int:
    credits = apimart_price_table_credits(
        model=model,
        resolution=resolution,
        duration_sec=duration_sec,
    )
    return apimart_cost_cents_from_credits(credits)


def apimart_price_table_credits(
    *,
    model: str,
    resolution: Any | None = None,
    duration_sec: Any | None = None,
) -> Decimal | None:
    normalized_model = model.strip().lower()
    for prefix, credits_by_resolution in _IMAGE_CREDITS_BY_MODEL_PREFIX.items():
        if normalized_model.startswith(prefix):
            normalized_resolution = str(resolution or "1k").strip().lower()
            return credits_by_resolution.get(normalized_resolution)

    if "seedance" not in normalized_model:
        return None

    normalized_resolution = str(resolution or "480p").strip().lower()
    credits_per_5_seconds = _VIDEO_CREDITS_PER_5_SECONDS_BY_RESOLUTION.get(
        normalized_resolution
    )
    if credits_per_5_seconds is None:
        return None

    try:
        duration = int(duration_sec or 5)
    except (TypeError, ValueError):
        duration = 5
    if duration <= 0:
        return Decimal("0")
    billing_windows = (duration + 4) // 5
    return credits_per_5_seconds * Decimal(billing_windows)


def _first_nested_value(value: Any, keys: set[str]) -> Any | None:
    for current in _walk(value):
        if not isinstance(current, Mapping):
            continue
        for key, item in current.items():
            if str(key).strip().lower() in keys:
                return item
    return None


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _walk(item)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None
