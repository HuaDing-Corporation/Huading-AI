from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import UsageRecord

_CREDIT_KEYS = {
    "credits",
    "credit",
    "used_credits",
    "total_credits",
    "usage_credits",
    "task_credits",
    "cost",
    "usage_cost",
}
_COST_CENTS_KEYS = {"cost_cents", "cny_cost_cents", "cost_cent"}


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
    return _int_or_none(result.get("cost_cents")) or 0


def apimart_cost_cents_from_reserved_usage(
    db: Session,
    *,
    tenant_id: str,
    video_task_id: str,
) -> int:
    record = db.scalar(
        select(UsageRecord).where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.video_task_id == video_task_id,
            UsageRecord.status == "reserved",
        )
    )
    if record is None:
        return 0
    return apimart_cost_cents_from_credits(record.credits)


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
