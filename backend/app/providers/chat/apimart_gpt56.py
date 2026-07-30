from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import requests

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import ProviderResolutionError, register_provider
from app.services.apimart_costs import apimart_usage_metadata
from app.services.apimart_token_pricing import apimart_cache_token_usage

_DEFAULT_BASE_URL = "https://api.apimart.ai/v1"
_ALLOWED_MODELS = {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"}


class APIMartGPT56ChatError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        usage_result: Mapping[str, Any] | None = None,
    ) -> None:
        self.usage_result = dict(usage_result or {})
        super().__init__(message)


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
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=self.request_timeout,
        )
        response_payload = _response_payload(response)
        usage = _usage(response_payload)
        _raise_for_response(response, response_payload, usage=usage)
        return {
            "content": _message_content(response_payload),
            "model": model,
            **usage,
        }


def _response_payload(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise APIMartGPT56ChatError("APIMart chat returned invalid JSON.") from exc
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
    usage: Mapping[str, Any],
) -> None:
    status_code = int(getattr(response, "status_code", 200) or 200)
    api_code = payload.get("code")
    try:
        numeric_api_code = int(api_code) if api_code is not None else 200
    except (TypeError, ValueError):
        numeric_api_code = 200
    if status_code < 400 and numeric_api_code in {0, 200}:
        return
    raise APIMartGPT56ChatError(
        _payload_message(payload, "APIMart chat request failed."),
        usage_result=usage,
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
    raw_usage = _completion_payload(payload).get("usage")
    if not isinstance(raw_usage, Mapping):
        raw_usage = {}
    prompt_tokens = _nonnegative_int(raw_usage.get("prompt_tokens"))
    completion_tokens = _nonnegative_int(raw_usage.get("completion_tokens"))
    total_tokens = _nonnegative_int(raw_usage.get("total_tokens")) or (
        prompt_tokens + completion_tokens
    )
    result: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    cache_usage = apimart_cache_token_usage(raw_usage)
    if cache_usage.cached_prompt_tokens is not None:
        result["cached_prompt_tokens"] = cache_usage.cached_prompt_tokens
    if cache_usage.cache_write_tokens is not None:
        result["cache_write_tokens"] = cache_usage.cache_write_tokens
    result.update(apimart_usage_metadata(payload))
    return result


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _positive_int(value: Any, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise APIMartGPT56ChatError(f"{field} must be a positive integer.") from exc
    if parsed <= 0:
        raise APIMartGPT56ChatError(f"{field} must be a positive integer.")
    return parsed


def _config_value(values: Mapping[str, Any], key: str, default: Any) -> Any:
    value = values.get(key)
    return default if value in (None, "") else value


def _apimart_gpt56_factory(config: ProviderConfig) -> APIMartGPT56ChatProvider:
    values = config.config or {}
    api_key = settings.engine_apimart_api_key.strip()
    if not api_key:
        raise ProviderResolutionError("APIMart chat API key is not configured.")
    return APIMartGPT56ChatProvider(
        api_key=api_key,
        base_url=str(_config_value(values, "base_url", settings.engine_apimart_base_url)),
        request_timeout=float(
            _config_value(
                values,
                "request_timeout",
                settings.engine_apimart_request_timeout_seconds,
            )
        ),
    )


register_provider("chat", "apimart-gpt56", _apimart_gpt56_factory)
