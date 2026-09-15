from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.providers import url_guard
from app.providers.base import register_provider

_API_URL = "https://api.heygen.com/v3"
_ENDPOINTS = {"avatar_iv": "videos", "lipsync_precision": "lipsyncs"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_KEY = re.compile(r"[A-Za-z0-9_:.-]{1,255}\Z")


class HeyGenError(RuntimeError):
    code = "HEYGEN_FAILED"


class HeyGenPending(HeyGenError):
    code = "HEYGEN_PENDING"


class HeyGenTerminalFailure(HeyGenError):
    """Authoritative rejection/terminal failure, never transport uncertainty."""


class HeyGenReviewRequired(HeyGenPending):
    code = "HEYGEN_REVIEW_REQUIRED"


class HeyGenNotConfigured(HeyGenError):
    code = "HEYGEN_NOT_CONFIGURED"


def request_fingerprint(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _timestamp(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(str(value))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise HeyGenReviewRequired("Invalid durable submission timestamp.") from exc


class HeyGenAvatarProvider:
    """Bounded HTTP only; durable identity and callbacks are owned by the worker."""

    def __init__(
        self,
        *,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
        input_hosts: set[str] | None = None,
        result_hosts: set[str] | None = None,
        request_timeout_seconds: float = 60,
        poll_timeout_seconds: float = 600,
        poll_interval_seconds: float = 5,
        download_max_bytes: int = 200 * 1024 * 1024,
        proxy_url: str | None = None,
    ) -> None:
        if not api_key:
            raise HeyGenNotConfigured("HeyGen service is not configured.")
        self._api_key = api_key
        self.http = http_client
        self.input_hosts = input_hosts or set()
        self.result_hosts = (
            {"files.heygen.ai", "files2.heygen.ai"} if result_hosts is None else result_hosts
        )
        self.request_timeout_seconds = request_timeout_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.download_max_bytes = download_max_bytes
        self.proxy_url = proxy_url

    @asynccontextmanager
    async def _client(self):
        if self.http is not None:
            yield self.http
        else:
            async with httpx.AsyncClient(
                timeout=self.request_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                proxy=self.proxy_url,
            ) as client:
                yield client

    def _safe_url(self, value: object, *, result: bool = False) -> str:
        try:
            url = str(value or "")
            parsed = urlparse(url)
            if (
                parsed.username
                or parsed.password
                or parsed.port not in {None, 443}
                or parsed.fragment
            ):
                raise ValueError
            url_guard.ensure_https_url_allowed(
                url, allowed_hosts=self.result_hosts if result else self.input_hosts
            )
            url_guard.ensure_public_https_url(url)
            return url
        except (ValueError, RuntimeError) as exc:
            # URLs can contain temporary credentials: never include them in errors.
            raise HeyGenError("HeyGen media URL is not allowed.") from exc

    def build_request(self, payload: Mapping[str, Any], *, model: str) -> dict[str, Any]:
        audio_url = self._safe_url(payload.get("audio_url"))
        if model == "avatar_iv":
            if payload.get("video_url"):
                raise HeyGenError("Exactly one avatar source is required.")
            ratio = str(payload.get("aspect_ratio") or "9:16")
            if ratio not in {"16:9", "9:16", "4:5", "5:4", "1:1", "auto"}:
                raise HeyGenError("Unsupported HeyGen aspect ratio.")
            return {
                "type": "image",
                "image": {"type": "url", "url": self._safe_url(payload.get("image_url"))},
                "audio_url": audio_url,
                "engine": {"type": "avatar_iv"},
                "resolution": "1080p",
                "aspect_ratio": ratio,
                "fit": "contain",
            }
        if model != "lipsync_precision" or payload.get("image_url"):
            raise HeyGenError("Unsupported HeyGen avatar source/model.")
        return {
            "video": {"type": "url", "url": self._safe_url(payload.get("video_url"))},
            "audio": {"type": "url", "url": audio_url},
            "mode": "precision",
            "keep_the_same_format": True,
            # Worker pre-aligns the source to the exact external audio duration.
            "enable_dynamic_duration": False,
            "enable_speech_enhancement": False,
        }

    def _validate_body(self, body: Mapping[str, Any], *, model: str) -> None:
        try:
            if model == "avatar_iv":
                source = body["image"]
                payload = {
                    "image_url": source["url"],
                    "audio_url": body["audio_url"],
                    "aspect_ratio": body["aspect_ratio"],
                }
                if source["type"] != "url":
                    raise ValueError
            else:
                source, audio = body["video"], body["audio"]
                if source["type"] != "url" or audio["type"] != "url":
                    raise ValueError
                payload = {"video_url": source["url"], "audio_url": audio["url"]}
            if dict(body) != self.build_request(payload, model=model):
                raise ValueError
        except (KeyError, TypeError, ValueError, HeyGenError) as exc:
            raise HeyGenReviewRequired("Durable HeyGen request does not match its model.") from exc

    async def generate_avatar(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._generate(payload, model="avatar_iv")

    async def generate_change_lips(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._generate(payload, model="lipsync_precision")

    async def _request(self, client, method: str, path: str, payload, *, body=None):
        callback = payload.get("before_request")
        if not callable(callback):
            raise HeyGenReviewRequired("Missing durable ownership check.")
        callback()
        headers = {"x-api-key": self._api_key}
        if method == "POST":
            headers["Idempotency-Key"] = payload["idempotency_key"]
        try:
            response = await client.request(
                method,
                f"{_API_URL}/{path}",
                headers=headers,
                json=body,
                follow_redirects=False,
                timeout=self.request_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise HeyGenPending("HeyGen request outcome is not yet known.") from exc
        if response.is_redirect:
            raise HeyGenReviewRequired("HeyGen API redirect was blocked.")
        if response.status_code in {408, 409, 429} or response.status_code >= 500:
            raise HeyGenPending("HeyGen request is still pending verification.")
        if response.is_error:
            if method == "POST" and not payload.get("post_attempted_before", False):
                raise HeyGenTerminalFailure("HeyGen rejected the generation request.")
            raise HeyGenReviewRequired("HeyGen job cannot yet be verified.")
        try:
            data = response.json()["data"]
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, KeyError, TypeError) as exc:
            raise HeyGenPending("HeyGen returned an unrecognized response.") from exc

    async def _generate(self, payload: Mapping[str, Any], *, model: str) -> dict[str, Any]:
        endpoint = _ENDPOINTS[model]
        job_id = payload.get("provider_job_id")
        async with self._client() as client:
            if not job_id:
                body = payload.get("request_body")
                now = datetime.now(UTC)
                submitted_at = _timestamp(payload.get("submitted_at"))
                if submitted_at > now or now >= submitted_at + timedelta(hours=24):
                    raise HeyGenReviewRequired("HeyGen idempotency window requires manual review.")
                if now >= _timestamp(payload.get("input_expires_at")):
                    raise HeyGenReviewRequired("HeyGen input URLs expired; submission is held.")
                if not isinstance(body, dict) or request_fingerprint(body) != payload.get(
                    "request_fingerprint"
                ):
                    raise HeyGenReviewRequired("HeyGen request fingerprint changed.")
                self._validate_body(body, model=model)
                if not _KEY.fullmatch(str(payload.get("idempotency_key") or "")):
                    raise HeyGenReviewRequired("HeyGen idempotency key is invalid.")
                on_submitted = payload.get("on_submitted")
                if not callable(on_submitted):
                    raise HeyGenReviewRequired("Missing durable job checkpoint callback.")
                data = await self._request(client, "POST", endpoint, payload, body=body)
                job_id = data.get("video_id" if model == "avatar_iv" else "lipsync_id")
                if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
                    raise HeyGenPending("HeyGen accepted request identity is not yet available.")
                on_submitted(job_id)
            if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
                raise HeyGenReviewRequired("Invalid persisted HeyGen job identity.")
            deadline = time.monotonic() + self.poll_timeout_seconds
            poll_count = 0
            while time.monotonic() < deadline:
                poll_count += 1
                progress = payload.get("progress_callback")
                if callable(progress):
                    progress({"poll_count": poll_count})
                data = await self._request(client, "GET", f"{endpoint}/{job_id}", payload)
                if data.get("id") != job_id:
                    raise HeyGenReviewRequired("HeyGen returned a different job identity.")
                status = data.get("status")
                if status == "completed":
                    try:
                        url = self._safe_url(data.get("video_url"), result=True)
                    except HeyGenError as exc:
                        raise HeyGenReviewRequired("HeyGen output URL was blocked.") from exc
                    return {
                        "provider": "heygen",
                        "model": model,
                        "provider_job_id": job_id,
                        "video_url": url,
                        "duration": data.get("duration"),
                    }
                if status == "failed":
                    raise HeyGenTerminalFailure("HeyGen confirmed generation failed.")
                if status not in {"waiting", "pending", "processing", "running"}:
                    raise HeyGenReviewRequired("HeyGen returned an unknown job state.")
                await asyncio.sleep(self.poll_interval_seconds)
        raise HeyGenPending("HeyGen generation is still in progress.")

    async def download_video(self, url: str) -> bytes:
        url = self._safe_url(url, result=True)
        try:
            async with self._client() as client, asyncio.timeout(self.request_timeout_seconds):
                async with client.stream(
                    "GET", url, follow_redirects=False, timeout=self.request_timeout_seconds
                ) as response:
                    if response.is_redirect or response.is_error:
                        raise HeyGenPending("HeyGen output download is unavailable.")
                    length = response.headers.get("content-length")
                    if length and int(length) > self.download_max_bytes:
                        raise HeyGenReviewRequired("HeyGen output exceeds download size limit.")
                    content = bytearray()
                    async for chunk in response.aiter_bytes(64 * 1024):
                        if len(content) + len(chunk) > self.download_max_bytes:
                            raise HeyGenReviewRequired("HeyGen output exceeds download size limit.")
                        content.extend(chunk)
                    if not content:
                        raise HeyGenPending("HeyGen output download was empty.")
                    return bytes(content)
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            raise HeyGenPending("HeyGen output download did not complete.") from exc


def heygen_factory(config=None):
    # This explicit frozen route does not replace the legacy platform default.
    return HeyGenAvatarProvider(
        api_key=settings.heygen_api_key.get_secret_value(),
        input_hosts=url_guard.object_storage_public_hosts(
            settings.engine_s3_public_endpoint,
            settings.storage_endpoint_url,
            bucket=settings.engine_s3_bucket,
            addressing_style=settings.engine_s3_addressing_style,
        ),
        request_timeout_seconds=settings.engine_heygen_request_timeout_seconds,
        poll_timeout_seconds=settings.engine_heygen_poll_timeout_seconds,
        poll_interval_seconds=settings.engine_heygen_poll_interval_seconds,
        download_max_bytes=settings.engine_heygen_download_max_bytes,
        proxy_url=settings.engine_heygen_proxy_url,
    )


register_provider("avatar", "heygen", heygen_factory)
