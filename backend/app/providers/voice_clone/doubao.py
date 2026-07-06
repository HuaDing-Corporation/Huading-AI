from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ProviderConfig
from app.providers.base import register_provider

_DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v1/mega_tts/audio/upload"
_DEFAULT_STATUS_ENDPOINT = "https://openspeech.bytedance.com/api/v1/mega_tts/status"
_DEFAULT_RESOURCE_ID = "volc.megatts.voiceclone"
_DEFAULT_MODEL_TYPE = 4
_PROVIDER_NAME = "doubao-voice-clone"
logger = get_logger(__name__)


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
        status_endpoint: str = _DEFAULT_STATUS_ENDPOINT,
        http_client: Any | None = None,
        request_timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: float = 60.0,
        model_type: int = _DEFAULT_MODEL_TYPE,
    ) -> None:
        if not appid or not access_token:
            raise DoubaoVoiceCloneError("Doubao voice clone appid/access token are required.")
        self.appid = appid
        self.access_token = access_token
        self.api_key = api_key
        self.resource_id = resource_id or _DEFAULT_RESOURCE_ID
        self.endpoint = endpoint or _DEFAULT_ENDPOINT
        self.status_endpoint = status_endpoint or _DEFAULT_STATUS_ENDPOINT
        self.request_timeout_seconds = request_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.model_type = int(model_type or _DEFAULT_MODEL_TYPE)
        if http_client is None:
            import requests

            http_client = requests.Session()
        self.http = http_client

    async def clone_voice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.clone_voice_sync, payload)

    async def delete_voice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.delete_voice_sync, payload)

    def clone_voice_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        speaker_id = str(payload.get("speaker_id") or "").strip()
        if not speaker_id:
            raise ValueError("Voice clone speaker_id is required.")
        audio_bytes = _payload_audio_bytes(payload)
        upload_audio_bytes, audio_format = _audio_upload_payload(
            audio_bytes,
            mime_type=str(payload.get("source_audio_mime_type") or ""),
            storage_key=str(payload.get("source_audio_storage_key") or ""),
        )
        body = {
            "appid": self.appid,
            "speaker_id": speaker_id,
            "audios": [
                {
                    "audio_bytes": base64.b64encode(upload_audio_bytes).decode("ascii"),
                    "audio_format": audio_format,
                }
            ],
            "source": 2,
            "language": 0,
            "model_type": self.model_type,
        }
        request_id = uuid4().hex
        response = self.http.post(
            self.endpoint,
            json=body,
            headers=self._headers(),
            timeout=self.request_timeout_seconds,
        )
        logger.info(
            "doubao_voice_clone.upload",
            tenant_id=str(payload.get("tenant_id") or ""),
            brand_voice_id=str(payload.get("brand_voice_id") or ""),
            speaker_id=speaker_id,
            request_id=request_id,
            http_status_code=getattr(response, "status_code", None),
        )
        response.raise_for_status()
        _raise_base_resp_error(response.json())
        status = self._poll_status(
            speaker_id,
            tenant_id=str(payload.get("tenant_id") or ""),
            brand_voice_id=str(payload.get("brand_voice_id") or ""),
        )
        return {"speaker_id": speaker_id, "status": status, "provider": _PROVIDER_NAME}

    def delete_voice_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        speaker_id = str(payload.get("speaker_id") or "").strip()
        if not speaker_id:
            return {"released": False}
        return {"released": True, "remote": False}

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer;{self.access_token}",
            "Resource-Id": self.resource_id,
        }

    def _poll_status(
        self,
        speaker_id: str,
        *,
        tenant_id: str,
        brand_voice_id: str,
    ) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            request_id = uuid4().hex
            response = self.http.post(
                self.status_endpoint,
                json={"appid": self.appid, "speaker_id": speaker_id},
                headers=self._headers(),
                timeout=self.request_timeout_seconds,
            )
            logger.info(
                "doubao_voice_clone.status",
                tenant_id=tenant_id,
                brand_voice_id=brand_voice_id,
                speaker_id=speaker_id,
                request_id=request_id,
                http_status_code=getattr(response, "status_code", None),
            )
            response.raise_for_status()
            data = response.json()
            status_code = _status_code(data)
            if status_code in {2, 4}:
                return "ready"
            if status_code in {0, 3}:
                message = _status_message(data) or "Doubao voice clone training failed."
                raise DoubaoVoiceCloneError(message)
            if status_code != 1:
                raise DoubaoVoiceCloneError(f"Unknown Doubao voice clone status: {status_code}")
            if time.monotonic() >= deadline:
                raise DoubaoVoiceCloneError("Doubao voice clone training timed out.")
            if self.poll_interval_seconds > 0:
                time.sleep(self.poll_interval_seconds)


def _first_value(data: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            value = data[key]
            return value
    return None


def _payload_audio_bytes(payload: Mapping[str, Any]) -> bytes:
    value = payload.get("source_audio_bytes")
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    raise ValueError("Voice clone source audio bytes are required.")


def _audio_upload_payload(
    audio_bytes: bytes,
    *,
    mime_type: str,
    storage_key: str,
) -> tuple[bytes, str]:
    audio_format = _audio_format(mime_type=mime_type, storage_key=storage_key)
    if audio_format is not None:
        return audio_bytes, audio_format
    return _transcode_audio_to_wav(audio_bytes, suffix=_audio_suffix(mime_type, storage_key)), "wav"


def _audio_format(*, mime_type: str, storage_key: str) -> str | None:
    normalized = mime_type.split(";", 1)[0].lower().strip()
    if normalized in {"audio/wav", "audio/x-wav", "audio/wave"}:
        return "wav"
    if normalized in {"audio/mpeg", "audio/mp3"}:
        return "mp3"
    if normalized in {"audio/mp4", "audio/x-m4a"}:
        return "m4a"
    suffix = storage_key.rsplit(".", 1)[-1].lower() if "." in storage_key else ""
    if suffix in {"wav", "mp3", "m4a"}:
        return suffix
    if normalized in {"audio/aac", "audio/ogg", "audio/webm"} or suffix in {
        "aac",
        "ogg",
        "opus",
        "webm",
    }:
        return None
    raise ValueError("Unsupported Doubao voice clone audio format.")


def _audio_suffix(mime_type: str, storage_key: str) -> str:
    suffix = storage_key.rsplit(".", 1)[-1].lower() if "." in storage_key else ""
    if suffix:
        return f".{suffix}"
    normalized = mime_type.split(";", 1)[0].lower().strip()
    return {
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/webm": ".webm",
    }.get(normalized, ".bin")


def _ffmpeg_binary() -> str:
    return os.environ.get("FFMPEG_BINARY", "ffmpeg")


def _transcode_audio_to_wav(audio_bytes: bytes, *, suffix: str) -> bytes:
    try:
        with tempfile.TemporaryDirectory(prefix="huading-voice-clone-") as temp_dir:
            source = Path(temp_dir) / f"source{suffix or '.bin'}"
            target = Path(temp_dir) / "source.wav"
            source.write_bytes(audio_bytes)
            subprocess.run(
                [
                    _ffmpeg_binary(),
                    "-y",
                    "-i",
                    str(source),
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-f",
                    "wav",
                    str(target),
                ],
                check=True,
                capture_output=True,
            )
            return target.read_bytes()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DoubaoVoiceCloneError("Failed to transcode voice clone audio to wav.") from exc


def _status_code(data: Mapping[str, Any]) -> int:
    value = _first_value(data, ("status", "Status"))
    if value is None and isinstance(data.get("data"), Mapping):
        value = _first_value(data["data"], ("status", "Status"))
    if value is None and isinstance(data.get("Data"), Mapping):
        value = _first_value(data["Data"], ("status", "Status"))
    return int(value if value is not None else -1)


def _raise_base_resp_error(data: Mapping[str, Any]) -> None:
    base_resp = _first_value(data, ("BaseResp", "base_resp", "baseResp"))
    if not isinstance(base_resp, Mapping):
        return
    raw_code = _first_value(base_resp, ("StatusCode", "status_code", "code", "Code"))
    if raw_code is None:
        return
    try:
        status_code = int(raw_code)
    except (TypeError, ValueError):
        status_code = -1
    if status_code == 0:
        return
    message = _first_value(
        base_resp,
        ("StatusMessage", "status_message", "message", "Message", "msg"),
    )
    raise DoubaoVoiceCloneError(
        str(message or f"Doubao voice clone upload failed: {status_code}")
    )


def _status_message(data: Mapping[str, Any]) -> str:
    value = _first_value(data, ("message", "msg", "Message"))
    if value is None and isinstance(data.get("data"), Mapping):
        value = _first_value(data["data"], ("message", "msg", "Message"))
    return str(value or "")


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
        status_endpoint=str(
            values.get("status_endpoint") or settings.engine_doubao_voice_clone_status_endpoint
        ),
        request_timeout_seconds=float(
            values.get("request_timeout_seconds")
            or settings.engine_doubao_voice_clone_request_timeout_seconds
        ),
        poll_interval_seconds=float(
            values.get("poll_interval_seconds")
            or settings.engine_doubao_voice_clone_poll_interval_seconds
        ),
        timeout_seconds=float(
            values.get("timeout_seconds") or settings.engine_doubao_voice_clone_timeout_seconds
        ),
        model_type=int(values.get("model_type") or settings.engine_doubao_voice_clone_model_type),
    )


register_provider("voice_clone", _PROVIDER_NAME, _doubao_voice_clone_factory)
