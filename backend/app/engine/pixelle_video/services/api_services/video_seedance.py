# Modifications Copyright (C) 2026 Huading (Apache-2.0 §4(b)):
#   - Hardened the Doubao-Seedance (Volcengine Ark) video client: text-to-video
#     and image-to-video (first-frame / reference image, by local path or URL),
#     top-level Ark parameters (resolution/ratio/duration/seed/watermark/
#     generate_audio), full status handling (queued/running/succeeded/failed/
#     expired/cancelled), and transient-error retries. Reads SEEDANCE_*/ARK_* env.
# Derived from Pixelle-Video (AIDC-AI, Apache-2.0). See engine LICENSE/NOTICE.
"""
Doubao-Seedance 2.0 video client (Volcengine Ark).

Async task flow per the Ark docs:
  POST {base}/contents/generations/tasks  -> { "id": ... }
  GET  {base}/contents/generations/tasks/{id} -> { "status", "content": {"video_url"} }

Request body (parameters are TOP-LEVEL fields, not prompt suffixes):
  {
    "model": "doubao-seedance-2-0-260128",
    "content": [
      {"type": "text", "text": "..."},
      {"type": "image_url", "image_url": {"url": "https://... or data:..."},
       "role": "first_frame"}            # image-to-video only
    ],
    "resolution": "720p", "ratio": "16:9", "duration": 5, ...
  }
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
_DEFAULT_MODEL = "doubao-seedance-2-0-260128"
# Statuses that mean "keep polling" vs terminal failure.
_PENDING = {"queued", "running", "pending", "processing"}
_FAILED = {"failed", "expired", "cancelled", "canceled"}
_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass
class SeedanceResult:
    task_id: str
    video_url: str
    save_path: Optional[str] = None


class SeedanceVideoClient:
    """Text-to-video and image-to-video via Volcengine Ark (Doubao-Seedance)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        local_proxy: Optional[str] = None,
        timeout: int = 120,
        poll_interval: int = 5,
        max_poll_seconds: int = 600,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or os.getenv("SEEDANCE_API_KEY") or os.getenv("ARK_API_KEY")
        self.base_url = (
            base_url
            or os.getenv("SEEDANCE_BASE_URL")
            or os.getenv("ARK_BASE_URL")
            or _DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = model or os.getenv("SEEDANCE_MODEL") or _DEFAULT_MODEL
        self.local_proxy = local_proxy
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_poll_seconds = max_poll_seconds
        self.max_retries = max_retries

        if not self.api_key:
            logger.warning("SeedanceVideoClient: SEEDANCE_API_KEY / ARK_API_KEY not set")

    # ---------------- public API ----------------

    def generate_video(
        self,
        prompt: str = "",
        *,
        image: Optional[str] = None,
        image_path: Optional[str] = None,
        image_role: str = "first_frame",
        save_path: Optional[str] = None,
        model: Optional[str] = None,
        duration: int = 5,
        resolution: str = "720p",
        ratio: str = "16:9",
        seed: Optional[int] = None,
        watermark: Optional[bool] = None,
        generate_audio: Optional[bool] = None,
        **extra: Any,
    ) -> SeedanceResult:
        """Run a Seedance task end to end.

        Text only -> text-to-video. With ``image``/``image_path`` -> image-to-video
        (``image_role`` first_frame / last_frame / reference_image).
        Returns the task id + remote video URL; downloads to ``save_path`` if given.
        """
        if not self.api_key:
            raise RuntimeError("SEEDANCE_API_KEY (or ARK_API_KEY) is not set.")
        if not prompt and not (image or image_path):
            raise ValueError("Provide a prompt (t2v) and/or an image (i2v).")

        task_id = self._submit_task(
            prompt=prompt,
            image=image,
            image_path=image_path,
            image_role=image_role,
            model=model or self.model,
            duration=duration,
            resolution=resolution,
            ratio=ratio,
            seed=seed,
            watermark=watermark,
            generate_audio=generate_audio,
            extra=extra,
        )
        video_url = self._poll_until_done(task_id)
        if save_path:
            self._download_video(video_url, save_path)
        return SeedanceResult(task_id=task_id, video_url=video_url, save_path=save_path)

    # ---------------- internals ----------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _proxies(self) -> Optional[dict]:
        if not self.local_proxy:
            return None
        return {"http": self.local_proxy, "https": self.local_proxy}

    @staticmethod
    def _image_url_value(image: Optional[str], image_path: Optional[str]) -> str:
        if image:
            return image  # already an http(s) URL or data: URI
        assert image_path is not None
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"input image not found: {image_path}")
        ext = os.path.splitext(image_path)[1].lower()
        mime = _MIME_BY_EXT.get(ext, "image/jpeg")
        with open(image_path, "rb") as fh:
            encoded = base64.b64encode(fh.read()).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    def _submit_task(
        self,
        *,
        prompt: str,
        image: Optional[str],
        image_path: Optional[str],
        image_role: str,
        model: str,
        duration: int,
        resolution: str,
        ratio: str,
        seed: Optional[int],
        watermark: Optional[bool],
        generate_audio: Optional[bool],
        extra: dict,
    ) -> str:
        content: list[dict] = []
        if prompt:
            content.append({"type": "text", "text": prompt})
        if image or image_path:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._image_url_value(image, image_path)},
                    "role": image_role,
                }
            )

        payload: dict[str, Any] = {
            "model": model,
            "content": content,
            "duration": duration,
            "resolution": resolution,
            "ratio": ratio,
        }
        if seed is not None:
            payload["seed"] = seed
        if watermark is not None:
            payload["watermark"] = watermark
        if generate_audio is not None:
            payload["generate_audio"] = generate_audio
        payload.update({k: v for k, v in extra.items() if v is not None})

        mode = "i2v" if (image or image_path) else "t2v"
        logger.info("Seedance submit: mode=%s model=%s duration=%ss", mode, model, duration)
        resp = self._request_with_retry(
            "POST", f"{self.base_url}/contents/generations/tasks", json=payload, timeout=self.timeout
        )
        if not resp.ok:
            logger.error("Seedance submit failed (%s): %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        task_id = (resp.json() or {}).get("id")
        if not task_id:
            raise RuntimeError(f"Seedance: no task id in response: {resp.text[:300]}")
        return task_id

    def _poll_until_done(self, task_id: str) -> str:
        url = f"{self.base_url}/contents/generations/tasks/{task_id}"
        deadline = time.monotonic() + self.max_poll_seconds
        while time.monotonic() < deadline:
            resp = self._request_with_retry("GET", url, timeout=30)
            resp.raise_for_status()
            data = resp.json() or {}
            status = (data.get("status") or "").lower()

            if status == "succeeded":
                video_url = (data.get("content") or {}).get("video_url") or data.get("video_url")
                if not video_url:
                    raise RuntimeError(f"Seedance succeeded but no video_url: {data}")
                return video_url
            if status in _FAILED:
                err = (data.get("error") or {}).get("message") or data.get("status_msg") or status
                raise RuntimeError(f"Seedance task {status}: {err}")
            if status and status not in _PENDING:
                logger.debug("Seedance: unknown status %r, continuing to poll", status)

            logger.debug("Seedance polling task=%s status=%s", task_id, status or "?")
            time.sleep(self.poll_interval)

        raise TimeoutError(f"Seedance timed out after {self.max_poll_seconds}s (task={task_id})")

    def _download_video(self, url: str, save_path: str) -> None:
        parent = os.path.dirname(save_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        resp = self._request_with_retry("GET", url, timeout=180, stream=True, auth=False)
        resp.raise_for_status()
        with open(save_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
        logger.info("Seedance: saved video to %s", save_path)

    def _request_with_retry(
        self, method: str, url: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """Retry transient failures (network errors, 429, 5xx) with linear backoff."""
        headers = self._headers() if auth else None
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.request(
                    method, url, headers=headers, proxies=self._proxies(), **kwargs
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < self.max_retries:
                        logger.warning(
                            "Seedance %s %s -> %s, retry %d/%d",
                            method, url, resp.status_code, attempt, self.max_retries,
                        )
                        time.sleep(attempt * 2)
                        continue
                return resp
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    logger.warning("Seedance %s %s network error, retry %d/%d: %s",
                                   method, url, attempt, self.max_retries, exc)
                    time.sleep(attempt * 2)
                    continue
                raise
        if last_exc:  # pragma: no cover - defensive
            raise last_exc
        raise RuntimeError("unreachable")
