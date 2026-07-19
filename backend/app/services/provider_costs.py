from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import UsageRecord


@dataclass(frozen=True)
class DeepSeekUsageCost:
    provider: str
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cents: int


_deepseek_usage_capture: ContextVar[list[DeepSeekUsageCost] | None] = ContextVar(
    "deepseek_usage_capture",
    default=None,
)


def _decimal_setting(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value))


def cny_to_cents(amount_cny: Decimal | float | int | str) -> int:
    cents = _decimal_setting(amount_cny) * Decimal("100")
    return int(cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def omnihuman_cost_cents(seconds: int | float | Decimal) -> int:
    safe_seconds = max(0, Decimal(str(seconds or 0)))
    return cny_to_cents(safe_seconds * _decimal_setting(settings.engine_omnihuman_cny_per_sec))


def omnihuman_change_lips_cost_cents(
    seconds: int | float | Decimal,
    *,
    tier: str | None,
) -> int:
    safe_seconds = max(0, Decimal(str(seconds or 0)))
    normalized_tier = str(tier or settings.engine_omnihuman_change_lips_default_tier).lower()
    price = (
        settings.engine_omnihuman_change_lips_basic_cny_per_sec
        if normalized_tier == "basic"
        else settings.engine_omnihuman_change_lips_lite_cny_per_sec
    )
    return cny_to_cents(safe_seconds * _decimal_setting(price))


def seed_tts_cost_cents(characters: int | float | Decimal) -> int:
    safe_chars = max(0, Decimal(str(characters or 0)))
    return cny_to_cents(safe_chars * _decimal_setting(settings.engine_seedtts_cny_per_char))


def deepseek_cost_cents(*, prompt_tokens: int, completion_tokens: int) -> int:
    input_cny = (
        Decimal(max(0, int(prompt_tokens)))
        / Decimal("1000")
        * _decimal_setting(settings.engine_deepseek_cny_per_1k_input)
    )
    output_cny = (
        Decimal(max(0, int(completion_tokens)))
        / Decimal("1000")
        * _decimal_setting(settings.engine_deepseek_cny_per_1k_output)
    )
    return cny_to_cents(input_cny + output_cny)


def apimart_scene_prompt_cost_cents(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int = 0,
) -> int:
    safe_prompt_tokens = max(0, int(prompt_tokens))
    safe_completion_tokens = max(0, int(completion_tokens))
    if safe_prompt_tokens + safe_completion_tokens <= 0:
        safe_prompt_tokens = max(0, int(total_tokens))
    input_usd = (
        Decimal(safe_prompt_tokens)
        / Decimal("1000000")
        * _decimal_setting(settings.engine_apimart_scene_prompt_input_usd_per_m)
    )
    output_usd = (
        Decimal(safe_completion_tokens)
        / Decimal("1000000")
        * _decimal_setting(settings.engine_apimart_scene_prompt_output_usd_per_m)
    )
    return cny_to_cents(
        (input_usd + output_usd) * _decimal_setting(settings.engine_usd_cny_rate)
    )


def _int_from_usage(usage: Any, key: str) -> int:
    if isinstance(usage, dict):
        value = usage.get(key)
    else:
        value = getattr(usage, key, 0)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_nonnegative_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def deepseek_usage_from_result(result: Any) -> DeepSeekUsageCost | None:
    if not isinstance(result, dict):
        return None
    usage = result.get("usage")
    if usage is None:
        return None
    prompt_tokens = _int_from_usage(usage, "prompt_tokens")
    completion_tokens = _int_from_usage(usage, "completion_tokens")
    total_tokens = _int_from_usage(usage, "total_tokens") or (
        prompt_tokens + completion_tokens
    )
    if total_tokens > 0 and prompt_tokens + completion_tokens == 0:
        prompt_tokens = total_tokens
    if total_tokens <= 0:
        return None
    return DeepSeekUsageCost(
        provider=str(result.get("provider") or "deepseek"),
        model=str(result.get("model") or settings.engine_llm_model or "") or None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_cents=deepseek_cost_cents(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def capture_deepseek_response(
    response: Any,
    *,
    model: str | None,
    provider: str = "deepseek",
) -> DeepSeekUsageCost | None:
    usage = deepseek_usage_from_result(
        {
            "provider": provider,
            "model": model,
            "usage": getattr(response, "usage", None),
        }
    )
    capture = _deepseek_usage_capture.get()
    if usage is not None and capture is not None:
        capture.append(usage)
    return usage


def begin_deepseek_usage_capture() -> Token[list[DeepSeekUsageCost] | None]:
    return _deepseek_usage_capture.set([])


def finish_deepseek_usage_capture(
    token: Token[list[DeepSeekUsageCost] | None],
) -> DeepSeekUsageCost | None:
    usages = _deepseek_usage_capture.get() or []
    _deepseek_usage_capture.reset(token)
    return aggregate_deepseek_usages(usages)


def aggregate_deepseek_usages(usages: list[DeepSeekUsageCost]) -> DeepSeekUsageCost | None:
    if not usages:
        return None
    prompt_tokens = sum(item.prompt_tokens for item in usages)
    completion_tokens = sum(item.completion_tokens for item in usages)
    total_tokens = sum(item.total_tokens for item in usages)
    if total_tokens <= 0:
        return None
    provider = usages[0].provider
    model = usages[0].model
    return DeepSeekUsageCost(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_cents=deepseek_cost_cents(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def record_deepseek_usage_cost(
    db: Session,
    *,
    tenant_id: str,
    usage: DeepSeekUsageCost,
    video_task_id: str | None = None,
    credits: Decimal | int | float | str = Decimal("0"),
    subscription_id: str | None = None,
) -> UsageRecord:
    record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        video_task_id=video_task_id,
        capability="llm",
        provider=usage.provider,
        model=usage.model,
        unit="token",
        quantity=Decimal(usage.total_tokens),
        credits=Decimal(str(credits)),
        cost_cents=usage.cost_cents,
        status="settled",
        settled_at=datetime.now(UTC),
    )
    db.add(record)
    return record


def record_deepseek_usage(
    db: Session,
    *,
    tenant_id: str,
    result: Any,
    video_task_id: str | None = None,
    credits: Decimal | int | float | str = Decimal("0"),
    subscription_id: str | None = None,
) -> UsageRecord | None:
    usage = deepseek_usage_from_result(result)
    if usage is None:
        return None
    return record_deepseek_usage_cost(
        db,
        tenant_id=tenant_id,
        usage=usage,
        video_task_id=video_task_id,
        credits=credits,
        subscription_id=subscription_id,
    )


def record_scene_prompt_usage(
    db: Session,
    *,
    tenant_id: str,
    result: Any,
) -> UsageRecord | None:
    if not isinstance(result, dict):
        return None
    prompt_tokens = _int_from_usage(result, "prompt_tokens")
    completion_tokens = _int_from_usage(result, "completion_tokens")
    total_tokens = _int_from_usage(result, "total_tokens")
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    explicit_cost_cents = _optional_nonnegative_int(result.get("cost_cents"))
    cost_cents = (
        explicit_cost_cents
        if explicit_cost_cents is not None
        else apimart_scene_prompt_cost_cents(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
    )
    if total_tokens > 0:
        unit = "token"
        quantity = Decimal(total_tokens)
    elif cost_cents > 0:
        unit = "call"
        quantity = Decimal("1")
    else:
        return None
    record = UsageRecord(
        tenant_id=tenant_id,
        capability="scene_prompt",
        provider=str(result.get("provider") or "apimart"),
        model=str(result.get("model") or settings.engine_apimart_scene_prompt_model),
        unit=unit,
        quantity=quantity,
        credits=Decimal("0"),
        cost_cents=cost_cents,
        status="settled",
        settled_at=datetime.now(UTC),
    )
    db.add(record)
    return record


def record_tts_usage(
    db: Session,
    *,
    tenant_id: str,
    result: Any,
    video_task_id: str | None = None,
) -> UsageRecord | None:
    if not isinstance(result, dict):
        return None
    provider = str(result.get("provider") or "").strip()
    if provider not in {"doubao-seed-tts", "cosyvoice-tts"}:
        return None
    try:
        characters = max(0, int(result.get("characters") or 0))
    except (TypeError, ValueError):
        characters = 0
    if characters <= 0:
        return None
    record = UsageRecord(
        tenant_id=tenant_id,
        video_task_id=video_task_id,
        capability="tts",
        provider=provider,
        model=_tts_model_from_result(result, provider),
        unit="char",
        quantity=Decimal(characters),
        credits=Decimal("0"),
        cost_cents=seed_tts_cost_cents(characters),
        status="settled",
        settled_at=datetime.now(UTC),
    )
    db.add(record)
    return record


def record_seed_tts_usage(
    db: Session,
    *,
    tenant_id: str,
    result: Any,
    video_task_id: str | None = None,
) -> UsageRecord | None:
    return record_tts_usage(
        db,
        tenant_id=tenant_id,
        result=result,
        video_task_id=video_task_id,
    )


def _tts_model_from_result(result: dict[str, Any], provider: str) -> str | None:
    default_model = (
        settings.engine_cosyvoice_voice_clone_target_model
        if provider == "cosyvoice-tts"
        else settings.engine_doubao_tts_resource_id
    )
    return str(result.get("model") or default_model or "") or None
