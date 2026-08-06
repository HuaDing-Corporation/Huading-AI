from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Any

import requests

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import ProviderResolutionError, register_provider
from app.services.apimart_costs import (
    apimart_usage_metadata,
    apimart_usage_metadata_contract_valid,
)
from app.services.apimart_token_pricing import apimart_cache_token_usage

_DEFAULT_BASE_URL = "https://api.apimart.ai/v1"
_ALLOWED_MODELS = {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"}
_REQUEST_TIMEOUT_STALE_SAFETY_FACTOR = 2
_RAW_COST_KEYS = {
    "credits",
    "credit",
    "used_credits",
    "usage_credits",
    "task_credits",
    "credits_cost",
    "cost_cents",
    "cny_cost_cents",
    "cost_cent",
}


class APIMartGPT56ChatError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        usage_result: Mapping[str, Any] | None = None,
        request_may_have_been_accepted: bool | None = None,
        raw_usage_present: bool | None = None,
        raw_cost_present: bool | None = None,
        usage_contract_valid: bool | None = None,
        cost_contract_valid: bool | None = None,
    ) -> None:
        self.usage_result = dict(usage_result or {})
        self.raw_usage_present = (
            any(
                key in self.usage_result
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            )
            if raw_usage_present is None
            else bool(raw_usage_present)
        )
        self.raw_cost_present = (
            any(key in self.usage_result for key in ("credits", "cost_cents"))
            if raw_cost_present is None
            else bool(raw_cost_present)
        )
        inferred_contract_valid = self.usage_result.get("_usage_contract_valid", True) is not False
        self.usage_contract_valid = (
            self.raw_usage_present and inferred_contract_valid
            if usage_contract_valid is None
            else bool(usage_contract_valid)
        )
        self.cost_contract_valid = (
            self.raw_cost_present and inferred_contract_valid
            if cost_contract_valid is None
            else bool(cost_contract_valid)
        )
        self.has_cost_evidence = _has_positive_cost_evidence(self.usage_result)
        self.request_may_have_been_accepted = (
            self.has_cost_evidence
            if request_may_have_been_accepted is None
            else bool(request_may_have_been_accepted)
        )
        super().__init__(message)


@dataclass(frozen=True)
class _UsageEvidence:
    usage_result: dict[str, Any]
    raw_usage_present: bool
    raw_cost_present: bool
    usage_contract_valid: bool
    cost_contract_valid: bool

    @property
    def has_cost_evidence(self) -> bool:
        return _has_positive_cost_evidence(self.usage_result)


class APIMartGPT56ChatProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        request_timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key and session is None:
            raise APIMartGPT56ChatError("APIMart API key is required.")
        self.api_key = api_key
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.request_timeout = request_timeout
        self.session = session or requests.Session()

    async def chat(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await asyncio.to_thread(self.chat_sync, payload)

    def chat_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        model = str(payload.get("model") or "").strip()
        if model not in _ALLOWED_MODELS:
            raise APIMartGPT56ChatError("Unsupported APIMart GPT-5.6 chat model.")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise APIMartGPT56ChatError("Chat messages are required.")

        request_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        max_completion_tokens = payload.get("max_completion_tokens")
        if max_completion_tokens is not None:
            request_body["max_completion_tokens"] = _positive_int(
                max_completion_tokens,
                field="max_completion_tokens",
            )
        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
                timeout=self.request_timeout,
                allow_redirects=False,
            )
        except requests.ConnectTimeout as exc:
            raise APIMartGPT56ChatError(
                "APIMart chat connection timed out.",
                request_may_have_been_accepted=False,
            ) from exc
        except (requests.ReadTimeout, requests.ConnectionError) as exc:
            raise APIMartGPT56ChatError(
                "APIMart chat request outcome is unknown after a network failure.",
                request_may_have_been_accepted=True,
            ) from exc
        except requests.RequestException as exc:
            raise APIMartGPT56ChatError(
                "APIMart chat request outcome is unknown after a request failure.",
                request_may_have_been_accepted=True,
            ) from exc
        response_payload = _response_payload(response)
        usage_evidence = _usage_evidence(response_payload)
        _raise_for_response(response, response_payload, usage_evidence=usage_evidence)
        try:
            content = _message_content(response_payload)
        except APIMartGPT56ChatError as exc:
            raise _error_with_usage_evidence(
                str(exc),
                usage_evidence,
                # A successful HTTP/API status is itself evidence that the
                # upstream accepted the request. Missing content or usage must
                # therefore guard against replay even when no cost metadata was
                # returned with the structurally invalid response.
                request_may_have_been_accepted=True,
            ) from exc
        return {
            "content": content,
            "model": model,
            **usage_evidence.usage_result,
        }


def _response_payload(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise APIMartGPT56ChatError(
            "APIMart chat returned invalid JSON.",
            request_may_have_been_accepted=True,
        ) from exc
    return payload if isinstance(payload, dict) else {}


def _completion_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    data = payload.get("data")
    if isinstance(data, Mapping) and ("choices" in data or "usage" in data):
        return data
    return payload


def _raise_for_response(
    response: Any,
    payload: Mapping[str, Any],
    *,
    usage_evidence: _UsageEvidence,
) -> None:
    try:
        status_code = int(getattr(response, "status_code", 200) or 200)
    except (TypeError, ValueError, OverflowError):
        # The request was dispatched, but the response cannot establish a safe
        # retry outcome when even its transport status is malformed.
        status_code = 500
    api_code = payload.get("code")
    if api_code is None:
        api_code_is_success = True
    else:
        try:
            api_code_is_success = int(api_code) in {0, 200}
        except (TypeError, ValueError, OverflowError):
            api_code_is_success = False
    if status_code < 400 and api_code_is_success:
        return
    raise _error_with_usage_evidence(
        _payload_message(payload, "APIMart chat request failed."),
        usage_evidence,
        request_may_have_been_accepted=(
            usage_evidence.has_cost_evidence or status_code == 408 or status_code >= 500
        ),
    )


def _error_with_usage_evidence(
    message: str,
    usage_evidence: _UsageEvidence,
    *,
    request_may_have_been_accepted: bool,
) -> APIMartGPT56ChatError:
    return APIMartGPT56ChatError(
        message,
        usage_result=usage_evidence.usage_result,
        request_may_have_been_accepted=request_may_have_been_accepted,
        raw_usage_present=usage_evidence.raw_usage_present,
        raw_cost_present=usage_evidence.raw_cost_present,
        usage_contract_valid=usage_evidence.usage_contract_valid,
        cost_contract_valid=usage_evidence.cost_contract_valid,
    )


def _payload_message(payload: Mapping[str, Any], fallback: str) -> str:
    for key in ("message", "msg", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, Mapping):
            nested = _payload_message(value, fallback)
            if nested != fallback:
                return nested
    data = payload.get("data")
    if isinstance(data, Mapping):
        return _payload_message(data, fallback)
    return fallback


def _message_content(payload: Mapping[str, Any]) -> str:
    completion = _completion_payload(payload)
    choices = completion.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise APIMartGPT56ChatError("APIMart chat response contained no choices.")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise APIMartGPT56ChatError("APIMart chat response contained no message.")
    for key in ("content", "reasoning_content"):
        content = message.get(key)
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            combined = "".join(
                str(item.get("text") or "") for item in content if isinstance(item, Mapping)
            ).strip()
            if combined:
                return combined
    raise APIMartGPT56ChatError("APIMart chat response contained no message content.")


def _usage(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _usage_evidence(payload).usage_result


def _usage_evidence(payload: Mapping[str, Any]) -> _UsageEvidence:
    completion = _completion_payload(payload)
    raw_usage_present = "usage" in completion
    raw_usage = completion.get("usage")
    usage_is_mapping = isinstance(raw_usage, Mapping)
    if not usage_is_mapping:
        raw_usage = {}
    prompt_tokens = _nonnegative_int(raw_usage.get("prompt_tokens"))
    completion_tokens = _nonnegative_int(raw_usage.get("completion_tokens"))
    reported_total_tokens = _nonnegative_int(raw_usage.get("total_tokens"))
    total_tokens = reported_total_tokens or (prompt_tokens + completion_tokens)
    result: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if not raw_usage_present:
        result["_usage_contract_missing"] = True
    cost_contract_valid = apimart_usage_metadata_contract_valid(payload)
    cost_metadata: dict[str, Any] = {}
    if cost_contract_valid:
        try:
            cost_metadata = apimart_usage_metadata(payload)
        except DecimalException:
            # A finite, nonnegative provider value can still exceed Decimal's
            # arithmetic range. Treat it as untrusted usage instead of turning
            # the completed provider call into a replayable provider failure.
            cost_contract_valid = False
    cache_usage = apimart_cache_token_usage(raw_usage)
    usage_contract_valid = (
        usage_is_mapping
        and cache_usage.contract_valid
        and all(
            _is_nonnegative_integer(raw_usage.get(key))
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
    )
    if usage_contract_valid:
        usage_contract_valid = (
            reported_total_tokens == prompt_tokens + completion_tokens
            and (cache_usage.cached_prompt_tokens or 0)
            + (cache_usage.cache_write_tokens or 0)
            <= prompt_tokens
        )
    if not usage_contract_valid or not cost_contract_valid:
        # Preserve safe normalized counters for failure observability while telling
        # the service not to trust them for delivery, settlement, or overdraft.
        result["_usage_contract_valid"] = False
    if cache_usage.cached_prompt_tokens is not None:
        result["cached_prompt_tokens"] = cache_usage.cached_prompt_tokens
    if cache_usage.cache_write_tokens is not None:
        result["cache_write_tokens"] = cache_usage.cache_write_tokens
    result.update(cost_metadata)
    return _UsageEvidence(
        usage_result=result,
        raw_usage_present=raw_usage_present,
        raw_cost_present=_has_nested_key(payload, _RAW_COST_KEYS),
        usage_contract_valid=usage_contract_valid,
        cost_contract_valid=cost_contract_valid,
    )


def _has_nested_key(value: Any, keys: set[str]) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).strip().lower() in keys or _has_nested_key(item, keys):
                return True
    elif isinstance(value, list | tuple):
        return any(_has_nested_key(item, keys) for item in value)
    return False


def _has_positive_cost_evidence(usage_result: Mapping[str, Any]) -> bool:
    if any(
        _nonnegative_int(usage_result.get(key)) > 0
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_prompt_tokens",
            "cache_write_tokens",
        )
    ):
        return True
    for key in ("credits", "cost_cents"):
        value = usage_result.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = Decimal(str(value))
        except (DecimalException, ValueError):
            continue
        if parsed.is_finite() and parsed > 0:
            return True
    return False


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _is_nonnegative_integer(value: Any) -> bool:
    if isinstance(value, bool) or value in (None, ""):
        return False
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, float):
        return math.isfinite(value) and value >= 0 and value.is_integer()
    if isinstance(value, str):
        try:
            return int(value.strip()) >= 0
        except (TypeError, ValueError, OverflowError):
            return False
    return False


def _positive_int(value: Any, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise APIMartGPT56ChatError(f"{field} must be a positive integer.") from exc
    if parsed <= 0:
        raise APIMartGPT56ChatError(f"{field} must be a positive integer.")
    return parsed


def _config_value(values: Mapping[str, Any], key: str, default: Any) -> Any:
    value = values.get(key)
    return default if value in (None, "") else value


def _safe_chat_request_timeout(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderResolutionError("APIMart chat request_timeout must be numeric.") from exc
    stale_seconds = settings.engine_aibrain_reservation_stale_minutes * 60
    if not math.isfinite(timeout) or timeout <= 0:
        raise ProviderResolutionError("APIMart chat request_timeout must be positive and finite.")
    if timeout * _REQUEST_TIMEOUT_STALE_SAFETY_FACTOR >= stale_seconds:
        raise ProviderResolutionError(
            "APIMart chat request_timeout is too long for the AIBRAIN reservation recovery window."
        )
    return timeout


def _apimart_gpt56_factory(config: ProviderConfig) -> APIMartGPT56ChatProvider:
    values = config.config or {}
    api_key = settings.engine_apimart_api_key.strip()
    if not api_key:
        raise ProviderResolutionError("APIMart chat API key is not configured.")
    return APIMartGPT56ChatProvider(
        api_key=api_key,
        base_url=str(_config_value(values, "base_url", settings.engine_apimart_base_url)),
        request_timeout=_safe_chat_request_timeout(
            _config_value(
                values,
                "request_timeout",
                settings.engine_apimart_request_timeout_seconds,
            )
        ),
    )


register_provider("chat", "apimart-gpt56", _apimart_gpt56_factory)
