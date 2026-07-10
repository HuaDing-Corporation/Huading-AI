from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import register_provider
from app.providers.url_guard import (
    ensure_https_url_allowed,
    object_storage_public_hosts,
    parse_host_suffixes,
)

_REQ_KEY = "jimeng_realman_avatar_picture_omni_v15"
_ENDPOINT = "https://visual.volcengineapi.com"
_VERSION = "2022-08-31"
_CHANGE_LIPS_VERSION = "2024-06-06"
_SERVICE = "cv"
_RETRYABLE_CODES = {50429, 50430, 50500, 50501}
_NON_RETRYABLE_CODES = {50215, 50411, 50511, 50412, 50512, 50413, 50514}
_PENDING_STATUSES = {"processing", "in_queue", "generating"}
_CHANGE_LIPS_SHARED_OPTIONAL_FIELDS = {
    "align_audio_reverse",
    "templ_start_seconds",
}
_CHANGE_LIPS_BASIC_OPTIONAL_FIELDS = {
    "open_sr",
    "separate_vocal",
    "open_scenedet",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _normalize_change_lips_tier(value: Any) -> str:
    tier = str(value or settings.engine_omnihuman_change_lips_default_tier or "basic").lower()
    return "basic" if tier == "basic" else "lite"


def _change_lips_result_url(data: Mapping[str, Any]) -> str:
    resp_data = data.get("resp_data")
    decoded: Any = None
    if isinstance(resp_data, str) and resp_data.strip():
        try:
            decoded = json.loads(resp_data)
        except json.JSONDecodeError:
            decoded = None
    elif isinstance(resp_data, Mapping):
        decoded = resp_data
    if isinstance(decoded, Mapping):
        url = decoded.get("url")
        if url:
            return str(url)
    return str(data.get("video_url") or "")


def _friendly_change_lips_error(message: str) -> str:
    raw = str(message or "")
    if "concurrent limit" in raw.lower():
        return "改口型服务繁忙，请稍后重试。"
    if "ECVideoDecodeError" in raw:
        return "视频文件无法解码，请上传 MP4/H.264/AAC 源视频。"
    if "ECVideoSizeLimited" in raw:
        return "视频文件过大，请压缩后重新上传。"
    if "ECVideoTimeTooLong" in raw:
        return "视频时长超出限制，请上传 10 秒以内的数字人源视频。"
    if any(token in raw for token in ("face", "Face", "人脸", "ECFace", "detect")):
        return "未能识别到清晰单人正脸，请更换数字人源视频。"
    return raw or "OmniHuman change-lips request failed."


def _response_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = payload.get("Result")
    return dict(result) if isinstance(result, Mapping) else dict(payload)


def _response_metadata_error(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    metadata = payload.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return None
    error = metadata.get("Error")
    return error if isinstance(error, Mapping) else None


def _provider_code(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class OmniHumanProviderError(RuntimeError):
    def __init__(self, message: str, *, provider_code: int | None = None) -> None:
        super().__init__(message)
        self.provider_code = provider_code


class OmniHumanProvider:
    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        region: str = "cn-north-1",
        http_client: Any | None = None,
        request_timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = 600.0,
        max_retries: int = 3,
        allowed_hosts: set[str] | None = None,
        result_host_suffixes: set[str] | None = None,
        change_lips_lite_req_key: str = "realman_change_lips",
        change_lips_basic_req_key: str = "realman_change_lips_basic_chimera",
        change_lips_region: str = "cn-beijing",
    ) -> None:
        if not access_key or not secret_key:
            raise OmniHumanProviderError("OmniHuman access key and secret key are required.")
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.request_timeout_seconds = request_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.allowed_hosts = set(allowed_hosts or {"visual.volcengineapi.com"})
        self.result_host_suffixes = set(result_host_suffixes or set())
        self.change_lips_lite_req_key = change_lips_lite_req_key
        self.change_lips_basic_req_key = change_lips_basic_req_key
        self.change_lips_region = change_lips_region
        if http_client is None:
            import requests

            http_client = requests.Session()
        self.http = http_client

    async def generate_avatar(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await asyncio.to_thread(self.generate_avatar_sync, payload)

    async def generate_change_lips(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await asyncio.to_thread(self.generate_change_lips_sync, payload)

    def generate_avatar_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        image_url = str(payload.get("image_url") or "")
        audio_url = str(payload.get("audio_url") or "")
        progress_callback = payload.get("progress_callback")
        on_progress: Callable[[dict[str, Any]], None] | None = (
            progress_callback if callable(progress_callback) else None
        )
        try:
            ensure_https_url_allowed(image_url, allowed_hosts=self.allowed_hosts)
            ensure_https_url_allowed(audio_url, allowed_hosts=self.allowed_hosts)
        except RuntimeError as exc:
            raise OmniHumanProviderError(str(exc)) from exc

        submit_body: dict[str, Any] = {
            "req_key": _REQ_KEY,
            "image_url": image_url,
            "audio_url": audio_url,
            "output_resolution": payload.get("output_resolution") or 1080,
            "pe_fast_mode": bool(payload.get("pe_fast_mode", False)),
            "seed": payload.get("seed", -1),
        }
        if payload.get("prompt"):
            submit_body["prompt"] = str(payload["prompt"])[:300]

        submitted = self._post_json("CVSubmitTask", submit_body)
        task_id = (submitted.get("data") or {}).get("task_id")
        if not task_id:
            raise OmniHumanProviderError("OmniHuman submit response did not include task_id.")

        deadline = time.monotonic() + self.timeout_seconds
        started_at = time.monotonic()
        poll_count = 0
        req_json = json.dumps({"aigc_meta": payload.get("aigc_meta") or {}}, separators=(",", ":"))
        while time.monotonic() < deadline:
            poll_count += 1
            result = self._post_json(
                "CVGetResult",
                {"req_key": _REQ_KEY, "task_id": task_id, "req_json": req_json},
            )
            data = result.get("data") or {}
            task_status = str(data.get("status") or "")
            if task_status == "done":
                video_url = data.get("video_url")
                if not video_url:
                    raise OmniHumanProviderError(
                        "OmniHuman done response did not include video_url."
                    )
                try:
                    ensure_https_url_allowed(
                        str(video_url),
                        allowed_hosts=self.allowed_hosts,
                        allowed_host_suffixes=self.result_host_suffixes,
                    )
                except RuntimeError as exc:
                    raise OmniHumanProviderError(str(exc)) from exc
                return {
                    "task_id": task_id,
                    "video_url": video_url,
                    "aigc_meta_tagged": bool(data.get("aigc_meta_tagged")),
                }
            if task_status not in _PENDING_STATUSES:
                raise OmniHumanProviderError(f"OmniHuman task ended with status {task_status}.")
            if on_progress is not None:
                on_progress(
                    {
                        "task_id": task_id,
                        "status": task_status,
                        "poll_count": poll_count,
                        "elapsed_seconds": max(0.0, time.monotonic() - started_at),
                        "timeout_seconds": self.timeout_seconds,
                    }
                )
            time.sleep(self.poll_interval_seconds)
        raise OmniHumanProviderError("OmniHuman task timed out.")

    def generate_change_lips_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        video_url = str(payload.get("video_url") or "")
        audio_url = str(payload.get("audio_url") or "")
        tier = _normalize_change_lips_tier(payload.get("tier"))
        req_key = (
            self.change_lips_basic_req_key if tier == "basic" else self.change_lips_lite_req_key
        )
        progress_callback = payload.get("progress_callback")
        on_progress: Callable[[dict[str, Any]], None] | None = (
            progress_callback if callable(progress_callback) else None
        )
        try:
            ensure_https_url_allowed(video_url, allowed_hosts=self.allowed_hosts)
            ensure_https_url_allowed(audio_url, allowed_hosts=self.allowed_hosts)
        except RuntimeError as exc:
            raise OmniHumanProviderError(str(exc)) from exc

        submit_body: dict[str, Any] = {
            "req_key": req_key,
            "url": video_url,
            "pure_audio_url": audio_url,
            "align_audio": bool(payload.get("align_audio", True)),
        }
        optional_fields = set(_CHANGE_LIPS_SHARED_OPTIONAL_FIELDS)
        if tier == "basic":
            optional_fields.update(_CHANGE_LIPS_BASIC_OPTIONAL_FIELDS)
        for key in optional_fields:
            value = payload.get(key)
            if value is not None:
                submit_body[key] = value

        submitted = self._post_json(
            "RealmanChangeLipsSubmitTask",
            submit_body,
            version=_CHANGE_LIPS_VERSION,
            region=self.change_lips_region,
        )
        task_id = (submitted.get("data") or {}).get("task_id")
        if not task_id:
            raise OmniHumanProviderError("OmniHuman change-lips response did not include task_id.")

        deadline = time.monotonic() + self.timeout_seconds
        started_at = time.monotonic()
        poll_count = 0
        while time.monotonic() < deadline:
            poll_count += 1
            result = self._post_json(
                "RealmanChangeLipsGetResult",
                {"req_key": req_key, "task_id": task_id},
                version=_CHANGE_LIPS_VERSION,
                region=self.change_lips_region,
            )
            data = result.get("data") or {}
            task_status = str(data.get("status") or "")
            if task_status == "done":
                result_url = _change_lips_result_url(data)
                if not result_url:
                    raise OmniHumanProviderError(
                        "OmniHuman change-lips done response did not include video URL."
                    )
                try:
                    ensure_https_url_allowed(
                        result_url,
                        allowed_hosts=self.allowed_hosts,
                        allowed_host_suffixes=self.result_host_suffixes,
                    )
                except RuntimeError as exc:
                    raise OmniHumanProviderError(str(exc)) from exc
                return {"task_id": task_id, "video_url": result_url, "tier": tier}
            if task_status not in _PENDING_STATUSES:
                message = data.get("message") or data.get("msg") or task_status
                raise OmniHumanProviderError(
                    _friendly_change_lips_error(str(message)),
                )
            if on_progress is not None:
                on_progress(
                    {
                        "task_id": task_id,
                        "status": task_status,
                        "poll_count": poll_count,
                        "elapsed_seconds": max(0.0, time.monotonic() - started_at),
                        "timeout_seconds": self.timeout_seconds,
                        "tier": tier,
                    }
                )
            time.sleep(self.poll_interval_seconds)
        raise OmniHumanProviderError("OmniHuman change-lips task timed out.")

    def _post_json(
        self,
        action: str,
        body: dict[str, Any],
        *,
        version: str = _VERSION,
        region: str | None = None,
    ) -> dict[str, Any]:
        params = {"Action": action, "Version": version}
        last_error: OmniHumanProviderError | None = None
        for attempt in range(self.max_retries + 1):
            body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers = self._signed_headers(params, body_bytes, region=region)
            response = self.http.post(
                _ENDPOINT,
                params=params,
                data=body_bytes,
                headers=headers,
                timeout=self.request_timeout_seconds,
            )
            response_payload = response.json()
            data = _response_result(response_payload)
            metadata_error = _response_metadata_error(response_payload)
            code = _provider_code(data.get("code") or data.get("status"))
            if metadata_error is None and code == 10000:
                return data
            if metadata_error is not None:
                metadata_code = _provider_code(metadata_error.get("Code"))
                code = metadata_code if metadata_code is not None else code
                if code == 10000:
                    code = None
                message = str(
                    metadata_error.get("Message")
                    or metadata_error.get("message")
                    or metadata_error
                )
            else:
                message = str(data.get("message") or data.get("msg") or data)
            error = OmniHumanProviderError(
                _friendly_change_lips_error(message) if "ChangeLips" in action else message,
                provider_code=code,
            )
            if code in _NON_RETRYABLE_CODES or code not in _RETRYABLE_CODES:
                raise error
            last_error = error
            if attempt < self.max_retries:
                time.sleep(min(2**attempt, 8))
        raise last_error or OmniHumanProviderError("OmniHuman request failed.")

    def _signed_headers(
        self,
        params: dict[str, str],
        body_bytes: bytes,
        *,
        region: str | None = None,
    ) -> dict[str, str]:
        x_date = _utcnow().strftime("%Y%m%dT%H%M%SZ")
        short_date = x_date[:8]
        signing_region = region or self.region
        payload_hash = hashlib.sha256(body_bytes).hexdigest()
        canonical_query = "&".join(
            f"{quote(key, safe='')}={quote(value, safe='')}"
            for key, value in sorted(params.items())
        )
        signed_headers = "content-type;host;x-content-sha256;x-date"
        canonical_headers = (
            "content-type:application/json\n"
            "host:visual.volcengineapi.com\n"
            f"x-content-sha256:{payload_hash}\n"
            f"x-date:{x_date}\n"
        )
        canonical_request = "\n".join(
            [
                "POST",
                "/",
                canonical_query,
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        credential_scope = f"{short_date}/{signing_region}/{_SERVICE}/request"
        string_to_sign = "\n".join(
            [
                "HMAC-SHA256",
                x_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        k_date = _hmac_sha256(self.secret_key.encode("utf-8"), short_date)
        k_region = _hmac_sha256(k_date, signing_region)
        k_service = _hmac_sha256(k_region, _SERVICE)
        k_signing = _hmac_sha256(k_service, "request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            f"HMAC-SHA256 Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return {
            "Content-Type": "application/json",
            "X-Date": x_date,
            "X-Content-Sha256": payload_hash,
            "Authorization": authorization,
        }


def _omnihuman_factory(config: ProviderConfig) -> OmniHumanProvider:
    values = config.config or {}
    allowed_hosts = {"visual.volcengineapi.com"} | object_storage_public_hosts(
        settings.engine_s3_public_endpoint,
        settings.storage_endpoint_url,
        bucket=settings.engine_s3_bucket,
        addressing_style=settings.engine_s3_addressing_style,
    )
    extra_hosts = values.get("allowed_hosts")
    if isinstance(extra_hosts, list):
        allowed_hosts.update(str(host) for host in extra_hosts)
    result_host_suffixes = parse_host_suffixes(settings.engine_omnihuman_result_host_suffixes)
    if "result_host_suffixes" in values:
        result_host_suffixes = parse_host_suffixes(values.get("result_host_suffixes"))
    return OmniHumanProvider(
        access_key=str(values.get("access_key") or settings.engine_omnihuman_access_key),
        secret_key=str(values.get("secret_key") or settings.engine_omnihuman_secret_key),
        region=str(values.get("region") or settings.engine_omnihuman_region),
        request_timeout_seconds=float(
            values.get("request_timeout_seconds")
            or settings.engine_omnihuman_request_timeout_seconds
        ),
        poll_interval_seconds=float(
            values.get("poll_interval_seconds") or settings.engine_omnihuman_poll_interval_seconds
        ),
        timeout_seconds=float(
            values.get("timeout_seconds") or settings.engine_omnihuman_timeout_seconds
        ),
        allowed_hosts=allowed_hosts,
        result_host_suffixes=result_host_suffixes,
        change_lips_lite_req_key=str(
            values.get("change_lips_lite_req_key")
            or settings.engine_omnihuman_change_lips_lite_req_key
        ),
        change_lips_basic_req_key=str(
            values.get("change_lips_basic_req_key")
            or settings.engine_omnihuman_change_lips_basic_req_key
        ),
        change_lips_region=str(
            values.get("change_lips_region") or settings.engine_omnihuman_change_lips_region
        ),
    )


register_provider("avatar", "omnihuman", _omnihuman_factory)
