from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import register_provider

_DEFAULT_BASE_URL = "https://api.apimart.ai/v1"
_DEFAULT_MODEL = "gpt-image-2"
_DEFAULT_SIZE = "1024x1024"
_DEFAULT_RESOLUTION = "1k"
_DEFAULT_QUALITY = "medium"
# Known non-terminal statuses. Polling intentionally does not whitelist against this
# set; any non-completed, non-failed status is treated as still processing.
_PROCESSING_STATUSES = {
    "pending",
    "submitted",
    "queued",
    "in_progress",
    "processing",
    "running",
}
_COMPLETED_STATUSES = {"completed", "succeeded", "success"}
_FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}


class APIMartImageProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


class APIMartImageProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        request_timeout: float = 60.0,
        poll_initial_delay: float = 10.0,
        poll_interval: float = 4.0,
        max_poll_seconds: float = 180.0,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key and session is None:
            raise APIMartImageProviderError("APIMart API key is required.")
        self.api_key = api_key
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.model = model or _DEFAULT_MODEL
        self.request_timeout = request_timeout
        self.poll_initial_delay = poll_initial_delay
        self.poll_interval = poll_interval
        self.max_poll_seconds = max_poll_seconds
        self.session = session or requests.Session()
        self._sleep = sleep_fn
        self._time = time_fn

    async def generate_image(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._generate_image_sync, payload)

    def _generate_image_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request_body, normalized = self._request_body(payload)
        task_id = self._submit(request_body)
        image_url = self._poll_until_complete(task_id)
        image_bytes, mime_type = self._download_image(image_url)
        return {
            "image_bytes": image_bytes,
            "mime_type": mime_type,
            "provider": "apimart",
            "model": self.model,
            "size": normalized["size"],
            "resolution": normalized["resolution"],
            "quality": normalized["quality"],
            "mode": "edit" if normalized["image_urls"] else "generate",
            "task_id": task_id,
        }

    def _request_body(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise APIMartImageProviderError("Image prompt is required.")

        n = int(payload.get("n") or 1)
        if n != 1:
            raise APIMartImageProviderError("Only n=1 is supported for photo generation.")

        size, resolution = _normalize_size_and_resolution(
            str(payload.get("size") or payload.get("image_size") or _DEFAULT_SIZE),
            payload.get("resolution"),
        )
        quality = str(payload.get("quality") or payload.get("image_quality") or _DEFAULT_QUALITY)
        image_urls = _image_urls(payload)
        if payload.get("input_image_path") and not image_urls:
            raise APIMartImageProviderError(
                "APIMart image editing requires public input_image_url/image_urls."
            )

        body: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "resolution": resolution,
            "quality": quality,
            "n": n,
        }
        if image_urls:
            body["image_urls"] = image_urls
        background = str(payload.get("background") or "").strip().lower()
        if background in {"auto", "opaque"}:
            body["background"] = background
        elif background == "transparent":
            body["background"] = "auto"
        mask_url = str(payload.get("mask_url") or "").strip()
        if mask_url:
            body["mask_url"] = mask_url
        return body, {
            "size": size,
            "resolution": resolution,
            "quality": quality,
            "image_urls": image_urls,
        }

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _submit(self, request_body: dict[str, Any]) -> str:
        response = self.session.post(
            f"{self.base_url}/images/generations",
            headers=self._headers(),
            json=request_body,
            timeout=self.request_timeout,
        )
        payload = _response_payload(response)
        _raise_for_response(response, payload, "APIMart image submit failed")
        data = payload.get("data") if isinstance(payload, dict) else None
        task: Mapping[str, Any] | None = None
        if isinstance(data, list) and data and isinstance(data[0], Mapping):
            task = data[0]
        elif isinstance(data, Mapping):
            task = data
        elif isinstance(payload, Mapping):
            task = payload
        task_id = str((task or {}).get("task_id") or (task or {}).get("id") or "").strip()
        if not task_id:
            raise APIMartImageProviderError("APIMart image submit response contained no task_id.")
        return task_id

    def _poll_until_complete(self, task_id: str) -> str:
        deadline = self._time() + self.max_poll_seconds
        self._sleep(self.poll_initial_delay)
        while True:
            if self._time() > deadline:
                raise APIMartImageProviderError(
                    f"APIMart image task {task_id} timed out.",
                    error_type="timeout",
                )
            response = self.session.get(
                f"{self.base_url}/tasks/{task_id}",
                headers=self._headers(),
                timeout=self.request_timeout,
            )
            payload = _response_payload(response)
            _raise_for_response(response, payload, "APIMart image task poll failed")
            data = payload.get("data") if isinstance(payload, dict) else payload
            if isinstance(data, list):
                data = data[0] if data else {}
            if not isinstance(data, Mapping):
                raise APIMartImageProviderError("APIMart task response contained invalid data.")

            status = str(data.get("status") or "").strip().lower()
            if status in _COMPLETED_STATUSES:
                return _extract_result_image_url(data)
            if status in _FAILED_STATUSES:
                raise APIMartImageProviderError(
                    _payload_message(data, "APIMart image task failed."),
                    error_type="task_failed",
                )
            self._sleep(self.poll_interval)

    def _download_image(self, image_url: str) -> tuple[bytes, str]:
        response = self.session.get(
            image_url,
            headers={},
            timeout=self.request_timeout,
        )
        payload: dict[str, Any] = {}
        if getattr(response, "status_code", 200) >= 400:
            payload = _response_payload(response)
        _raise_for_response(response, payload, "APIMart image download failed")
        content = bytes(getattr(response, "content", b"") or b"")
        if not content:
            raise APIMartImageProviderError("APIMart image download returned empty content.")
        content_type = str(getattr(response, "headers", {}).get("content-type") or "image/png")
        return content, content_type.split(";", 1)[0].strip() or "image/png"


def _normalize_size_and_resolution(size: str, resolution: Any) -> tuple[str, str]:
    explicit_resolution = str(resolution or "").strip().lower()
    if ":" in size and "x" not in size.lower():
        return size.strip(), explicit_resolution or _DEFAULT_RESOLUTION

    parts = size.lower().split("x", 1)
    if len(parts) != 2:
        return size.strip() or _DEFAULT_SIZE, explicit_resolution or _DEFAULT_RESOLUTION
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError:
        return size.strip() or _DEFAULT_SIZE, explicit_resolution or _DEFAULT_RESOLUTION

    divisor = math.gcd(width, height) or 1
    ratio = f"{width // divisor}:{height // divisor}"
    max_side = max(width, height)
    inferred_resolution = "1k" if max_side <= 1536 else "2k" if max_side <= 2048 else "4k"
    return ratio, explicit_resolution or inferred_resolution


def _image_urls(payload: Mapping[str, Any]) -> list[str]:
    raw_urls = payload.get("image_urls")
    if raw_urls is None and payload.get("input_image_url"):
        raw_urls = [payload["input_image_url"]]
    if raw_urls is None:
        return []
    if isinstance(raw_urls, str):
        raw_urls = [raw_urls]
    if not isinstance(raw_urls, list | tuple):
        raise APIMartImageProviderError("image_urls must be a list of public URLs.")
    urls = [str(item).strip() for item in raw_urls if str(item).strip()]
    if len(urls) > 6:
        raise APIMartImageProviderError("APIMart image_urls supports at most 6 images.")
    return urls


def _response_payload(response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _raise_for_response(response, payload: Mapping[str, Any], fallback: str) -> None:
    status_code = int(getattr(response, "status_code", 200) or 200)
    api_code = payload.get("code")
    try:
        numeric_api_code = int(api_code) if api_code is not None else 200
    except (TypeError, ValueError):
        numeric_api_code = 200
    if status_code < 400 and numeric_api_code in {0, 200}:
        return
    raise APIMartImageProviderError(
        _payload_message(payload, fallback),
        status_code=status_code if status_code >= 400 else numeric_api_code,
        error_type=str(payload.get("type") or payload.get("error") or ""),
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
        nested = _payload_message(data, fallback)
        if nested != fallback:
            return nested
    return fallback


def _extract_result_image_url(data: Mapping[str, Any]) -> str:
    result = data.get("result")
    if not isinstance(result, Mapping):
        raise APIMartImageProviderError("APIMart completed task contained no result.")
    images = result.get("images")
    if not isinstance(images, list) or not images:
        raise APIMartImageProviderError("APIMart completed task contained no result images.")
    first = images[0]
    if not isinstance(first, Mapping):
        raise APIMartImageProviderError("APIMart result image payload was invalid.")
    raw_url = first.get("url")
    if isinstance(raw_url, list):
        raw_url = raw_url[0] if raw_url else ""
    image_url = str(raw_url or "").strip()
    if not image_url:
        raise APIMartImageProviderError("APIMart result image URL was empty.")
    return image_url


def _config_value(values: Mapping[str, Any], key: str, default: Any) -> Any:
    value = values.get(key)
    return default if value in (None, "") else value


def _apimart_image_factory(config: ProviderConfig) -> APIMartImageProvider:
    values = config.config or {}
    return APIMartImageProvider(
        api_key=str(_config_value(values, "api_key", settings.engine_apimart_api_key)),
        base_url=str(_config_value(values, "base_url", settings.engine_apimart_base_url)),
        model=str(_config_value(values, "model", settings.engine_apimart_image_model)),
        request_timeout=float(
            _config_value(
                values,
                "request_timeout",
                settings.engine_apimart_request_timeout_seconds,
            )
        ),
        poll_initial_delay=float(
            _config_value(
                values,
                "poll_initial_delay",
                settings.engine_apimart_poll_initial_delay_seconds,
            )
        ),
        poll_interval=float(
            _config_value(
                values,
                "poll_interval",
                settings.engine_apimart_poll_interval_seconds,
            )
        ),
        max_poll_seconds=float(
            _config_value(values, "timeout", settings.engine_apimart_timeout_seconds)
        ),
    )


register_provider("image", "apimart", _apimart_image_factory)
