from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import register_provider

_DEFAULT_BASE_URL = "https://api.apimart.ai/v1"
_DEFAULT_MODEL = "doubao-seedance-2.0"
_DEFAULT_T2V_SIZE = "9:16"
_DEFAULT_I2V_SIZE = "adaptive"
_DEFAULT_RESOLUTION = "720p"
_VALID_SIZES = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"}
_VALID_RESOLUTIONS = {"480p", "720p", "1080p"}
_COMPLETED_STATUSES = {"completed", "succeeded", "success"}
_FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}


class APIMartVideoProviderError(RuntimeError):
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


class APIMartVideoProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        request_timeout: float = 60.0,
        poll_initial_delay: float = 30.0,
        poll_interval: float = 10.0,
        max_poll_seconds: float = 900.0,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key and session is None:
            raise APIMartVideoProviderError("APIMart API key is required.")
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

    async def generate_video(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._generate_video_sync, payload)

    def _generate_video_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request_body, normalized = self._request_body(payload)
        task_id = self._submit(request_body)
        video_url = self._poll_until_complete(task_id)
        video_bytes, mime_type = self._download_video(video_url)
        return {
            "video_bytes": video_bytes,
            "mime_type": mime_type,
            "provider": "apimart",
            "model": self.model,
            "task_id": task_id,
            "duration": normalized["duration"],
            "resolution": normalized["resolution"],
            "size": normalized["size"],
        }

    def _request_body(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise APIMartVideoProviderError("Video prompt is required.")

        image_urls = _image_urls(payload)
        duration = _duration(payload.get("duration", payload.get("duration_sec")))
        resolution = _resolution(payload.get("resolution"))
        size = _size(payload.get("size"), has_image_urls=bool(image_urls))
        body: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "duration": duration,
            "size": size,
            "resolution": resolution,
            "generate_audio": False,
        }
        seed = payload.get("seed")
        if seed not in (None, ""):
            body["seed"] = int(seed)
        if image_urls:
            body["image_urls"] = image_urls
        return body, {"duration": duration, "resolution": resolution, "size": size}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _submit(self, request_body: dict[str, Any]) -> str:
        response = self.session.post(
            f"{self.base_url}/videos/generations",
            headers=self._headers(),
            json=request_body,
            timeout=self.request_timeout,
        )
        payload = _response_payload(response)
        _raise_for_response(response, payload, "APIMart video submit failed")
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
            raise APIMartVideoProviderError("APIMart video submit response contained no task_id.")
        return task_id

    def _poll_until_complete(self, task_id: str) -> str:
        deadline = self._time() + self.max_poll_seconds
        self._sleep(self.poll_initial_delay)
        while True:
            if self._time() > deadline:
                raise APIMartVideoProviderError(
                    f"APIMart video task {task_id} timed out.",
                    error_type="timeout",
                )
            response = self.session.get(
                f"{self.base_url}/tasks/{task_id}",
                headers=self._headers(),
                timeout=self.request_timeout,
            )
            payload = _response_payload(response)
            _raise_for_response(response, payload, "APIMart video task poll failed")
            data = payload.get("data") if isinstance(payload, dict) else payload
            if isinstance(data, list):
                data = data[0] if data else {}
            if not isinstance(data, Mapping):
                raise APIMartVideoProviderError("APIMart task response contained invalid data.")

            status = str(data.get("status") or "").strip().lower()
            if status in _COMPLETED_STATUSES:
                return _extract_result_video_url(data)
            if status in _FAILED_STATUSES:
                raise APIMartVideoProviderError(
                    _payload_message(data, "APIMart video task failed."),
                    error_type="task_failed",
                )
            self._sleep(self.poll_interval)

    def _download_video(self, video_url: str) -> tuple[bytes, str]:
        response = self.session.get(video_url, headers={}, timeout=self.request_timeout)
        payload: dict[str, Any] = {}
        if getattr(response, "status_code", 200) >= 400:
            payload = _response_payload(response)
        _raise_for_response(response, payload, "APIMart video download failed")
        content = bytes(getattr(response, "content", b"") or b"")
        if not content:
            raise APIMartVideoProviderError("APIMart video download returned empty content.")
        content_type = str(getattr(response, "headers", {}).get("content-type") or "video/mp4")
        return content, content_type.split(";", 1)[0].strip() or "video/mp4"


def _duration(raw_duration: Any) -> int:
    try:
        duration = int(raw_duration or 5)
    except (TypeError, ValueError):
        duration = 5
    return max(4, min(15, duration))


def _resolution(raw_resolution: Any) -> str:
    resolution = str(raw_resolution or "").strip().lower()
    return resolution if resolution in _VALID_RESOLUTIONS else _DEFAULT_RESOLUTION


def _size(raw_size: Any, *, has_image_urls: bool) -> str:
    size = str(raw_size or "").strip()
    if size in _VALID_SIZES:
        return size
    return _DEFAULT_I2V_SIZE if has_image_urls else _DEFAULT_T2V_SIZE


def _image_urls(payload: Mapping[str, Any]) -> list[str]:
    raw_urls = payload.get("image_urls")
    if raw_urls is None:
        return []
    if isinstance(raw_urls, str):
        raw_urls = [raw_urls]
    if not isinstance(raw_urls, list | tuple):
        raise APIMartVideoProviderError("image_urls must be a list of public URLs.")
    return [str(item).strip() for item in raw_urls if str(item).strip()]


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
    raise APIMartVideoProviderError(
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


def _extract_result_video_url(data: Mapping[str, Any]) -> str:
    result = data.get("result")
    if not isinstance(result, Mapping):
        raise APIMartVideoProviderError("APIMart completed task contained no result.")
    videos = result.get("videos")
    if not isinstance(videos, list) or not videos:
        raise APIMartVideoProviderError("APIMart completed task contained no result videos.")
    first = videos[0]
    if not isinstance(first, Mapping):
        raise APIMartVideoProviderError("APIMart result video payload was invalid.")
    raw_url = first.get("url")
    if isinstance(raw_url, list):
        raw_url = raw_url[0] if raw_url else ""
    video_url = str(raw_url or "").strip()
    if not video_url:
        raise APIMartVideoProviderError("APIMart result video URL was empty.")
    return video_url


def _config_value(values: Mapping[str, Any], key: str, default: Any) -> Any:
    value = values.get(key)
    return default if value in (None, "") else value


def _apimart_video_factory(config: ProviderConfig) -> APIMartVideoProvider:
    values = config.config or {}
    return APIMartVideoProvider(
        api_key=str(_config_value(values, "api_key", settings.engine_apimart_api_key)),
        base_url=str(_config_value(values, "base_url", settings.engine_apimart_base_url)),
        model=str(_config_value(values, "model", settings.engine_apimart_video_model)),
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
                settings.engine_apimart_video_poll_initial_delay_seconds,
            )
        ),
        poll_interval=float(
            _config_value(
                values,
                "poll_interval",
                settings.engine_apimart_video_poll_interval_seconds,
            )
        ),
        max_poll_seconds=float(
            _config_value(values, "timeout", settings.engine_apimart_video_timeout_seconds)
        ),
    )


register_provider("video", "apimart", _apimart_video_factory)
