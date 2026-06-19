from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import register_provider
from app.providers.url_guard import ensure_https_url_allowed, object_storage_public_hosts

_REQ_KEY = "jimeng_realman_avatar_picture_omni_v15"
_ENDPOINT = "https://visual.volcengineapi.com"
_VERSION = "2022-08-31"
_SERVICE = "cv"
_RETRYABLE_CODES = {50429, 50430, 50500, 50501}
_NON_RETRYABLE_CODES = {50215, 50411, 50511, 50412, 50512, 50413, 50514}
_PENDING_STATUSES = {"processing", "in_queue", "generating"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


class OmniHumanProviderError(RuntimeError):
    pass


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
        if http_client is None:
            import requests

            http_client = requests.Session()
        self.http = http_client

    async def generate_avatar(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await asyncio.to_thread(self.generate_avatar_sync, payload)

    def generate_avatar_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        image_url = str(payload.get("image_url") or "")
        audio_url = str(payload.get("audio_url") or "")
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
        req_json = json.dumps({"aigc_meta": payload.get("aigc_meta") or {}}, separators=(",", ":"))
        while time.monotonic() < deadline:
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
            time.sleep(self.poll_interval_seconds)
        raise OmniHumanProviderError("OmniHuman task timed out.")

    def _post_json(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        params = {"Action": action, "Version": _VERSION}
        last_error: OmniHumanProviderError | None = None
        for attempt in range(self.max_retries + 1):
            body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers = self._signed_headers(params, body_bytes)
            response = self.http.post(
                _ENDPOINT,
                params=params,
                data=body_bytes,
                headers=headers,
                timeout=self.request_timeout_seconds,
            )
            data = response.json()
            code = int(data.get("code") or 0)
            if code == 10000:
                return data
            message = str(data.get("message") or data.get("msg") or data)
            error = OmniHumanProviderError(message)
            if code in _NON_RETRYABLE_CODES or code not in _RETRYABLE_CODES:
                raise error
            last_error = error
            if attempt < self.max_retries:
                time.sleep(min(2**attempt, 8))
        raise last_error or OmniHumanProviderError("OmniHuman request failed.")

    def _signed_headers(self, params: dict[str, str], body_bytes: bytes) -> dict[str, str]:
        x_date = _utcnow().strftime("%Y%m%dT%H%M%SZ")
        short_date = x_date[:8]
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
        credential_scope = f"{short_date}/{self.region}/{_SERVICE}/request"
        string_to_sign = "\n".join(
            [
                "HMAC-SHA256",
                x_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        k_date = _hmac_sha256(self.secret_key.encode("utf-8"), short_date)
        k_region = _hmac_sha256(k_date, self.region)
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


def _parse_host_suffixes(value: object) -> set[str]:
    if isinstance(value, str):
        return {item.strip().lower().lstrip(".") for item in value.split(",") if item.strip()}
    if isinstance(value, list | tuple | set):
        return {str(item).strip().lower().lstrip(".") for item in value if str(item).strip()}
    return set()


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
    result_host_suffixes = _parse_host_suffixes(settings.engine_omnihuman_result_host_suffixes)
    if "result_host_suffixes" in values:
        result_host_suffixes = _parse_host_suffixes(values.get("result_host_suffixes"))
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
    )


register_provider("avatar", "omnihuman", _omnihuman_factory)
