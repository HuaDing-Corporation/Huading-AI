from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import register_provider

_DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v3/voice-clone"
_DEFAULT_RESOURCE_ID = "seed-icl-2.0"
_PROVIDER_NAME = "doubao-voice-clone"


class DoubaoVoiceCloneError(RuntimeError):
    pass


class DoubaoVoiceCloneProvider:
    def __init__(
        self,
        *,
        appid: str,
        access_token: str,
        api_key: str = "",
        resource_id: str = _DEFAULT_RESOURCE_ID,
        endpoint: str = _DEFAULT_ENDPOINT,
        http_client: Any | None = None,
        request_timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key and (not appid or not access_token):
            raise DoubaoVoiceCloneError(
                "Doubao voice clone appid/access token or api key are required."
            )
        self.appid = appid
        self.access_token = access_token
        self.api_key = api_key
        self.resource_id = resource_id or _DEFAULT_RESOURCE_ID
        self.endpoint = endpoint or _DEFAULT_ENDPOINT
        self.request_timeout_seconds = request_timeout_seconds
        if http_client is None:
            import requests

            http_client = requests.Session()
        self.http = http_client

    async def clone_voice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.clone_voice_sync, payload)

    async def delete_voice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.delete_voice_sync, payload)

    def clone_voice_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = {
            "speaker_name": str(payload.get("name") or "").strip(),
            "audio": {
                "url": str(payload.get("source_audio_url") or ""),
                "mime_type": str(payload.get("source_audio_mime_type") or ""),
            },
            "metadata": {
                "tenant_id": str(payload.get("tenant_id") or ""),
                "brand_voice_id": str(payload.get("brand_voice_id") or ""),
                "source_audio_asset_id": str(payload.get("source_audio_asset_id") or ""),
            },
        }
        if not body["speaker_name"]:
            raise ValueError("Voice clone name is required.")
        if not body["audio"]["url"]:
            raise ValueError("Voice clone source audio URL is required.")
        response = self.http.post(
            self.endpoint,
            json=body,
            headers=self._headers(),
            timeout=self.request_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        speaker_id = _first_value(data, ("speaker_id", "speakerId", "voice_id", "voiceId"))
        if not speaker_id and isinstance(data.get("data"), Mapping):
            speaker_id = _first_value(
                data["data"],
                ("speaker_id", "speakerId", "voice_id", "voiceId"),
            )
        if not speaker_id:
            raise DoubaoVoiceCloneError("Doubao voice clone did not return speaker_id.")
        status = str(_first_value(data, ("status",)) or "ready").lower()
        if status not in {"processing", "ready", "failed"}:
            status = "ready"
        return {"speaker_id": str(speaker_id), "status": status, "provider": _PROVIDER_NAME}

    def delete_voice_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        speaker_id = str(payload.get("speaker_id") or "").strip()
        if not speaker_id:
            return {"released": False}
        response = self.http.delete(
            self.endpoint,
            json={
                "speaker_id": speaker_id,
                "metadata": {
                    "tenant_id": str(payload.get("tenant_id") or ""),
                    "brand_voice_id": str(payload.get("brand_voice_id") or ""),
                },
            },
            headers=self._headers(),
            timeout=self.request_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        released = bool(data.get("released", True))
        return {"released": released}

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": uuid4().hex,
        }
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        else:
            headers["X-Api-App-Id"] = self.appid
            headers["X-Api-Access-Key"] = self.access_token
        return headers


def _first_value(data: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def _doubao_voice_clone_factory(config: ProviderConfig) -> DoubaoVoiceCloneProvider:
    values = config.config or {}
    return DoubaoVoiceCloneProvider(
        appid=str(
            values.get("appid")
            or settings.engine_doubao_voice_clone_appid
            or settings.engine_doubao_tts_appid
        ),
        access_token=str(
            values.get("access_token")
            or settings.engine_doubao_voice_clone_access_token
            or settings.engine_doubao_tts_access_token
        ),
        api_key=str(
            values.get("api_key")
            or settings.engine_doubao_voice_clone_api_key
            or settings.engine_doubao_tts_api_key
        ),
        resource_id=str(
            values.get("resource_id") or settings.engine_doubao_voice_clone_resource_id
        ),
        endpoint=str(values.get("endpoint") or settings.engine_doubao_voice_clone_endpoint),
        request_timeout_seconds=float(
            values.get("request_timeout_seconds")
            or settings.engine_doubao_voice_clone_request_timeout_seconds
        ),
    )


register_provider("voice_clone", _PROVIDER_NAME, _doubao_voice_clone_factory)
