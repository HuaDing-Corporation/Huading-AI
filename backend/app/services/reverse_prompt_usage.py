from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from decimal import Decimal, InvalidOperation
from typing import Any

_USAGE_KEYS = (
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "credits",
    "cost_cents",
    "cost_source",
)
_usage_capture: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "reverse_prompt_usage_capture",
    default=None,
)


def begin_reverse_prompt_usage_capture() -> tuple[
    Token[list[dict[str, Any]] | None],
    list[dict[str, Any]],
]:
    usages: list[dict[str, Any]] = []
    return _usage_capture.set(usages), usages


def finish_reverse_prompt_usage_capture(
    token: Token[list[dict[str, Any]] | None],
) -> None:
    _usage_capture.reset(token)


def reverse_prompt_usage_checkpoint() -> int | None:
    usages = _usage_capture.get()
    return len(usages) if usages is not None else None


def reverse_prompt_usage_since(
    checkpoint: int | None,
) -> list[dict[str, Any]]:
    usages = _usage_capture.get()
    if usages is None or checkpoint is None:
        return []
    return [dict(item) for item in usages[checkpoint:]]


def capture_reverse_prompt_usage(result: Mapping[str, Any]) -> bool:
    usages = _usage_capture.get()
    if usages is None:
        return False
    usage = {
        key: result.get(key)
        for key in _USAGE_KEYS
        if result.get(key) is not None
    }
    if not _has_billable_usage(usage):
        return False
    usages.append(usage)
    return True


def _has_billable_usage(usage: Mapping[str, Any]) -> bool:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost_cents"):
        try:
            if int(usage.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    try:
        return Decimal(str(usage.get("credits") or "0")) > 0
    except (InvalidOperation, TypeError, ValueError):
        return False
