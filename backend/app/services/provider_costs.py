from __future__ import annotations

import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import UsageRecord
from app.services.apimart_token_pricing import APIMartTokenPricingError, apimart_token_usage_cost

logger = get_logger(__name__)


class DeepSeekCostError(ValueError):
    def __init__(self, message: str, *, error_type: str) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class DeepSeekUsageCost:
    provider: str
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cents: int
    cost_cny: Decimal = Decimal("0")
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    cache_tokens_reported: bool = False
    provider_cost_usd: Decimal | None = None


_deepseek_usage_capture: ContextVar[list[DeepSeekUsageCost] | None] = ContextVar(
    "deepseek_usage_capture",
    default=None,
)
_DEEPSEEK_PROVIDER = "deepseek"
_DEEPSEEK_DEFAULT_PRICED_MODEL = "deepseek-v4-flash"
_DEEPSEEK_RATE_SETTINGS_BY_MODEL = {
    "deepseek-v4-flash": (
        "engine_deepseek_cny_per_1k_input",
        "engine_deepseek_cny_per_1k_cache_hit",
        "engine_deepseek_cny_per_1k_output",
    ),
}


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
    # Volcengine Doubao Seed-TTS direct CNY rate.
    safe_chars = max(0, Decimal(str(characters or 0)))
    return cny_to_cents(safe_chars * _decimal_setting(settings.engine_seedtts_cny_per_char))


def cosyvoice_tts_cost_cents(characters: int | float | Decimal) -> int:
    # Alibaba Cloud Bailian/DashScope CosyVoice direct CNY rate.
    safe_chars = max(0, Decimal(str(characters or 0)))
    return cny_to_cents(safe_chars * _decimal_setting(settings.engine_cosyvoice_tts_cny_per_char))


def tts_cost_cents(
    characters: int | float | Decimal,
    *,
    provider: str,
) -> int:
    if provider == "doubao-seed-tts":
        return seed_tts_cost_cents(characters)
    if provider == "cosyvoice-tts":
        return cosyvoice_tts_cost_cents(characters)
    raise ValueError(f"Unsupported TTS provider cost basis: {provider!r}")


def deepseek_cost_cny(
    *,
    model: str | None = None,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_cache_hit_tokens: int | None = None,
    prompt_cache_miss_tokens: int | None = None,
) -> Decimal:
    normalized_model = str(model or _DEEPSEEK_DEFAULT_PRICED_MODEL).strip().lower()
    rate_setting_names = _DEEPSEEK_RATE_SETTINGS_BY_MODEL.get(normalized_model)
    if rate_setting_names is None:
        raise DeepSeekCostError(
            f"DeepSeek pricing is not configured for model {normalized_model!r}.",
            error_type="unknown_model",
        )
    input_rate, cache_hit_rate, output_rate = (
        _decimal_setting(getattr(settings, setting_name)) for setting_name in rate_setting_names
    )
    safe_prompt_tokens = max(0, int(prompt_tokens))
    cache_hit_tokens, cache_miss_tokens, _ = _deepseek_cache_breakdown(
        prompt_tokens=safe_prompt_tokens,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
    )
    cache_miss_cny = Decimal(cache_miss_tokens) / Decimal("1000") * input_rate
    cache_hit_cny = Decimal(cache_hit_tokens) / Decimal("1000") * cache_hit_rate
    output_cny = Decimal(max(0, int(completion_tokens))) / Decimal("1000") * output_rate
    return cache_miss_cny + cache_hit_cny + output_cny


def deepseek_cost_cents(
    *,
    model: str | None = None,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_cache_hit_tokens: int | None = None,
    prompt_cache_miss_tokens: int | None = None,
) -> int:
    return cny_to_cents(
        deepseek_cost_cny(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=prompt_cache_miss_tokens,
        )
    )


def apimart_scene_prompt_cost_cents(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int = 0,
    cached_prompt_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    authoritative_credits: Decimal | int | float | str | None = None,
) -> int:
    safe_prompt_tokens = max(0, int(prompt_tokens))
    safe_completion_tokens = max(0, int(completion_tokens))
    if safe_prompt_tokens + safe_completion_tokens <= 0:
        safe_prompt_tokens = max(0, int(total_tokens))
    usage_cost = apimart_token_usage_cost(
        model=model,
        prompt_tokens=safe_prompt_tokens,
        completion_tokens=safe_completion_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
        cache_write_tokens=cache_write_tokens,
        authoritative_credits=authoritative_credits,
    )
    return usage_cost.cost_cents


def _int_from_usage(usage: Any, key: str) -> int:
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    if value is None:
        return 0
    parsed = _strict_nonnegative_int(value)
    if parsed is None:
        raise DeepSeekCostError("Supplier usage is invalid.", error_type="invalid_usage")
    return parsed


def _optional_nonnegative_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return _strict_nonnegative_int(value)


def _strict_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or isinstance(value, float):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    return None


def _deepseek_cache_breakdown(
    *,
    prompt_tokens: int,
    prompt_cache_hit_tokens: int | None,
    prompt_cache_miss_tokens: int | None,
) -> tuple[int, int, bool]:
    cache_hit_tokens = _optional_nonnegative_int(prompt_cache_hit_tokens)
    cache_miss_tokens = _optional_nonnegative_int(prompt_cache_miss_tokens)
    cache_tokens_reported = cache_hit_tokens is not None and cache_miss_tokens is not None
    if not cache_tokens_reported:
        return 0, prompt_tokens, False
    if cache_hit_tokens + cache_miss_tokens == prompt_tokens:
        return cache_hit_tokens, cache_miss_tokens, True
    raise DeepSeekCostError(
        "DeepSeek cache hit and miss tokens must equal prompt tokens.",
        error_type="invalid_cache_usage",
    )


def deepseek_usage_from_result(result: Any) -> DeepSeekUsageCost | None:
    if not isinstance(result, dict):
        return None
    usage = result.get("usage")
    if usage is None:
        return None
    try:
        prompt_tokens = _int_from_usage(usage, "prompt_tokens")
        completion_tokens = _int_from_usage(usage, "completion_tokens")
        total_tokens = _int_from_usage(usage, "total_tokens") or (
            prompt_tokens + completion_tokens
        )
    except DeepSeekCostError:
        return None
    raw_cache_hit_value = (
        usage.get("prompt_cache_hit_tokens")
        if isinstance(usage, dict)
        else getattr(usage, "prompt_cache_hit_tokens", None)
    )
    raw_cache_miss_value = (
        usage.get("prompt_cache_miss_tokens")
        if isinstance(usage, dict)
        else getattr(usage, "prompt_cache_miss_tokens", None)
    )
    raw_prompt_cache_hit_tokens = _optional_nonnegative_int(
        raw_cache_hit_value
    )
    raw_prompt_cache_miss_tokens = _optional_nonnegative_int(
        raw_cache_miss_value
    )
    if (
        (raw_cache_hit_value not in (None, "") and raw_prompt_cache_hit_tokens is None)
        or (raw_cache_miss_value not in (None, "") and raw_prompt_cache_miss_tokens is None)
    ):
        return None
    if total_tokens > 0 and prompt_tokens + completion_tokens == 0:
        prompt_tokens = total_tokens
    if total_tokens <= 0:
        return None
    try:
        prompt_cache_hit_tokens, prompt_cache_miss_tokens, cache_tokens_reported = (
            _deepseek_cache_breakdown(
                prompt_tokens=prompt_tokens,
                prompt_cache_hit_tokens=raw_prompt_cache_hit_tokens,
                prompt_cache_miss_tokens=raw_prompt_cache_miss_tokens,
            )
        )
    except DeepSeekCostError:
        return None
    provider = str(result.get("provider") or _DEEPSEEK_PROVIDER).strip().lower()
    if provider != _DEEPSEEK_PROVIDER:
        raise DeepSeekCostError(
            f"DeepSeek cost capture cannot price provider {provider!r}.",
            error_type="unknown_provider",
        )
    model = str(result.get("model") or settings.engine_llm_model or _DEEPSEEK_DEFAULT_PRICED_MODEL)
    if not cache_tokens_reported:
        logger.warning(
            "deepseek_cache_usage_unavailable",
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
        )
    provider_cost_usd = _provider_cost_usd(result)
    if result.get("provider_cost_usd") not in (None, "") and provider_cost_usd is None:
        return None
    cost_cny = deepseek_cost_cny(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
    )
    return DeepSeekUsageCost(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_cents=cny_to_cents(cost_cny),
        cost_cny=cost_cny,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
        cache_tokens_reported=cache_tokens_reported,
        provider_cost_usd=provider_cost_usd,
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
    cache_breakdowns = [
        _deepseek_cache_breakdown(
            prompt_tokens=item.prompt_tokens,
            prompt_cache_hit_tokens=(
                item.prompt_cache_hit_tokens if item.cache_tokens_reported else None
            ),
            prompt_cache_miss_tokens=(
                item.prompt_cache_miss_tokens if item.cache_tokens_reported else None
            ),
        )
        for item in usages
    ]
    prompt_cache_hit_tokens = sum(item[0] for item in cache_breakdowns)
    prompt_cache_miss_tokens = sum(item[1] for item in cache_breakdowns)
    cache_tokens_reported = all(item[2] for item in cache_breakdowns)
    if total_tokens <= 0:
        return None
    provider = usages[0].provider
    model = usages[0].model
    if any(item.provider != provider or item.model != model for item in usages):
        raise DeepSeekCostError(
            "DeepSeek usage aggregation requires one provider and model.",
            error_type="mixed_usage",
        )
    cost_cny = deepseek_cost_cny(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
    )
    return DeepSeekUsageCost(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_cents=cny_to_cents(cost_cny),
        cost_cny=cost_cny,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
        cache_tokens_reported=cache_tokens_reported,
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


def _provider_cost_usd(result: Any) -> Decimal | None:
    if not isinstance(result, dict):
        return None
    value = result.get("provider_cost_usd")
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def attach_deepseek_usage(record: UsageRecord, *, result: object) -> UsageRecord:
    """Attach sanitized DeepSeek supplier telemetry without changing the allocation."""
    try:
        parsed = deepseek_usage_from_result(result)
    except DeepSeekCostError:
        return record
    if parsed is None:
        return record
    record.provider = parsed.provider
    record.model = parsed.model
    record.provider_usage = {
        "input_tokens": parsed.prompt_tokens,
        "output_tokens": parsed.completion_tokens,
        "total_tokens": parsed.total_tokens,
    }
    record.cost_cents = parsed.cost_cents
    record.provider_cost_usd = parsed.provider_cost_usd
    return record


def _scene_prompt_supplier_fields(result: Any) -> tuple[str, str, dict[str, int], int] | None:
    if not isinstance(result, dict):
        return None
    try:
        prompt_tokens = _int_from_usage(result, "prompt_tokens")
        completion_tokens = _int_from_usage(result, "completion_tokens")
        total_tokens = _int_from_usage(result, "total_tokens") or (
            prompt_tokens + completion_tokens
        )
    except DeepSeekCostError:
        return None
    model = str(result.get("model") or settings.engine_apimart_scene_prompt_model)
    explicit_cost_cents = _optional_nonnegative_int(result.get("cost_cents"))
    if result.get("cost_cents") not in (None, "") and explicit_cost_cents is None:
        return None
    cached_prompt_tokens = _optional_nonnegative_int(result.get("cached_prompt_tokens"))
    cache_write_tokens = _optional_nonnegative_int(result.get("cache_write_tokens"))
    if (
        (result.get("cached_prompt_tokens") not in (None, "") and cached_prompt_tokens is None)
        or (result.get("cache_write_tokens") not in (None, "") and cache_write_tokens is None)
    ):
        return None
    if (
        result.get("provider_cost_usd") not in (None, "")
        and _provider_cost_usd(result) is None
    ):
        return None
    try:
        cost_cents = (
            explicit_cost_cents
            if explicit_cost_cents is not None
            else apimart_scene_prompt_cost_cents(
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cached_prompt_tokens=cached_prompt_tokens,
                cache_write_tokens=cache_write_tokens,
                authoritative_credits=result.get("credits"),
            )
        )
    except (APIMartTokenPricingError, InvalidOperation, TypeError, ValueError):
        return None
    if total_tokens <= 0 and cost_cents <= 0:
        return None
    usage = {
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    for key in ("cached_prompt_tokens", "cache_write_tokens"):
        value = cached_prompt_tokens if key == "cached_prompt_tokens" else cache_write_tokens
        if value is not None:
            usage[key] = value
    return str(result.get("provider") or "apimart"), model, usage, cost_cents


def attach_scene_prompt_usage(record: UsageRecord, *, result: object) -> UsageRecord:
    """Attach Luna/APIMart telemetry to its canonical billing allocation."""
    parsed = _scene_prompt_supplier_fields(result)
    if parsed is None:
        return record
    provider, model, usage, cost_cents = parsed
    record.provider = provider
    record.model = model
    record.provider_usage = usage
    record.cost_cents = cost_cents
    record.provider_cost_usd = _provider_cost_usd(result)
    return record


def record_scene_prompt_usage(
    db: Session,
    *,
    tenant_id: str,
    result: Any,
) -> UsageRecord | None:
    parsed = _scene_prompt_supplier_fields(result)
    if parsed is None:
        return None
    provider, model, _usage, cost_cents = parsed
    total_tokens = _usage["total_tokens"]
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
        provider=provider,
        model=model,
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
        cost_cents=tts_cost_cents(characters, provider=provider),
        status="settled",
        settled_at=datetime.now(UTC),
    )
    db.add(record)
    return record


def attach_tts_usage(record: UsageRecord, *, result: object) -> UsageRecord:
    """Attach sanitized supplier telemetry to an existing character allocation."""
    if not isinstance(result, dict):
        return record
    provider = str(result.get("provider") or "").strip()
    if provider not in {"doubao-seed-tts", "cosyvoice-tts"}:
        return record
    try:
        characters = max(0, int(result.get("characters") or 0))
    except (TypeError, ValueError):
        return record
    if characters <= 0:
        return record
    record.provider = provider
    record.model = _tts_model_from_result(result, provider)
    record.provider_usage = {"characters": characters}
    record.cost_cents = tts_cost_cents(characters, provider=provider)
    record.provider_cost_usd = _provider_cost_usd(result)
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
