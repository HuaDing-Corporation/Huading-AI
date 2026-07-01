from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openai import DefaultHttpxClient, OpenAI

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import register_provider

_DEFAULT_MODEL = "gpt-image-2"
_DEFAULT_SIZE = "1024x1024"
_DEFAULT_QUALITY = "medium"
_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIImageProviderError(RuntimeError):
    pass


class OpenAIImageProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        model: str = _DEFAULT_MODEL,
        local_proxy: str | None = None,
        timeout: float = 120.0,
        client: OpenAI | None = None,
    ) -> None:
        if not api_key and client is None:
            raise OpenAIImageProviderError("OpenAI API key is required.")
        self.model = model or _DEFAULT_MODEL
        self.timeout = timeout
        self.client = client or self._build_client(
            api_key=api_key,
            base_url=base_url,
            local_proxy=local_proxy,
            timeout=timeout,
        )

    def _build_client(
        self,
        *,
        api_key: str,
        base_url: str | None,
        local_proxy: str | None,
        timeout: float,
    ) -> OpenAI:
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        kwargs["base_url"] = base_url or _DEFAULT_BASE_URL
        if local_proxy:
            kwargs["http_client"] = DefaultHttpxClient(proxy=local_proxy, timeout=timeout)
        return OpenAI(**kwargs)

    async def generate_image(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._generate_image_sync, payload)

    def _generate_image_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise OpenAIImageProviderError("Image prompt is required.")
        size = str(payload.get("size") or payload.get("image_size") or _DEFAULT_SIZE)
        quality = str(
            payload.get("quality") or payload.get("image_quality") or _DEFAULT_QUALITY
        )
        n = int(payload.get("n") or 1)
        if n != 1:
            raise OpenAIImageProviderError("Only n=1 is supported for photo MVP.")

        input_image_path = payload.get("input_image_path")
        if input_image_path:
            response = self._edit_image(
                prompt=prompt,
                image_path=Path(str(input_image_path)),
                size=size,
                quality=quality,
            )
            mode = "edit"
        else:
            response = self.client.images.generate(
                model=self.model,
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
            )
            mode = "generate"

        image_bytes = self._decode_b64_response(response)
        return {
            "image_bytes": image_bytes,
            "mime_type": "image/png",
            "provider": "openai",
            "model": self.model,
            "size": size,
            "quality": quality,
            "mode": mode,
        }

    def _edit_image(self, *, prompt: str, image_path: Path, size: str, quality: str):
        if not image_path.exists():
            raise OpenAIImageProviderError(f"Input image does not exist: {image_path}")
        with image_path.open("rb") as image_file:
            return self.client.images.edit(
                model=self.model,
                image=[image_file],
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
            )

    def _decode_b64_response(self, response) -> bytes:
        data = getattr(response, "data", None)
        if not data:
            raise OpenAIImageProviderError("OpenAI image response contained no data.")
        item = data[0]
        b64_json = getattr(item, "b64_json", None)
        if b64_json is None and isinstance(item, dict):
            b64_json = item.get("b64_json")
        if not b64_json:
            raise OpenAIImageProviderError("OpenAI image response did not include b64_json.")
        return base64.b64decode(b64_json)


def _openai_image_factory(config: ProviderConfig) -> OpenAIImageProvider:
    values = config.config or {}
    return OpenAIImageProvider(
        api_key=str(values.get("api_key") or settings.openai_api_key),
        base_url=str(values.get("base_url") or settings.openai_base_url or ""),
        model=str(values.get("model") or settings.openai_image_model or _DEFAULT_MODEL),
        local_proxy=str(values.get("local_proxy") or settings.openai_local_proxy or ""),
        timeout=float(values.get("timeout") or settings.openai_image_timeout),
    )


register_provider("image", "openai", _openai_image_factory)
