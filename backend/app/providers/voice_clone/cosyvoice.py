from __future__ import annotations

import asyncio
import re
import tempfile
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ProviderConfig
from app.providers.base import register_provider

_PROVIDER_NAME = "cosyvoice-voice-clone"
_TTS_PROVIDER_NAME = "cosyvoice-tts"
_DEFAULT_TARGET_MODEL = "cosyvoice-v3.5-plus"
_SAFE_PREFIX_PATTERN = re.compile(r"[a-z0-9]+")

logger = get_logger(__name__)


class CosyVoiceCloneError(RuntimeError):
    pass


class CosyVoiceCloneProvider:
    def __init__(
        self,
        *,
        api_key: str,
        target_model: str = _DEFAULT_TARGET_MODEL,
        enrollment_service: Any | None = None,
        synthesizer_factory: Callable[..., Any] | None = None,
        output_dir: str | None = None,
        base_url: str | None = None,
        request_timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key:
            raise CosyVoiceCloneError("CosyVoice voice clone API key is required.")
        self.api_key = api_key
        self.target_model = target_model or _DEFAULT_TARGET_MODEL
        self.enrollment = enrollment_service or _create_enrollment_service(api_key)
        self.synthesizer_factory = synthesizer_factory or _create_synthesizer
        self.output_dir = Path(output_dir) if output_dir else None
        self.base_url = _normalise_base_url(base_url)
        self.request_timeout_seconds = request_timeout_seconds

    async def clone_voice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.clone_voice_sync, payload)

    async def delete_voice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.delete_voice_sync, payload)

    async def synthesize_speech(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.synthesize_speech_sync, payload)

    def clone_voice_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        source_audio_url = str(payload.get("source_audio_url") or "").strip()
        if not source_audio_url:
            raise ValueError("CosyVoice source audio URL is required.")
        prefix = _safe_prefix(payload)
        with _dashscope_runtime(self.api_key, self.base_url):
            voice_id = self.enrollment.create_voice(
                self.target_model,
                prefix,
                source_audio_url,
            )
        logger.info(
            "cosyvoice.clone_voice",
            tenant_id=str(payload.get("tenant_id") or ""),
            brand_voice_id=str(payload.get("brand_voice_id") or ""),
            target_model=self.target_model,
            prefix=prefix,
        )
        return {"speaker_id": str(voice_id), "status": "ready", "provider": _PROVIDER_NAME}

    def delete_voice_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        speaker_id = str(payload.get("speaker_id") or "").strip()
        if not speaker_id:
            return {"released": False}
        with _dashscope_runtime(self.api_key, self.base_url):
            self.enrollment.delete_voice(speaker_id)
        logger.info(
            "cosyvoice.delete_voice",
            tenant_id=str(payload.get("tenant_id") or ""),
            brand_voice_id=str(payload.get("brand_voice_id") or ""),
        )
        return {"released": True}

    def synthesize_speech_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("TTS text is required.")
        voice = str(payload.get("voice") or "").strip()
        if not voice:
            raise ValueError("CosyVoice voice id is required.")
        unit_id = str(payload.get("task_id") or "cosyvoice")
        output_dir = Path(
            str(payload.get("output_dir") or self.output_dir or tempfile.gettempdir())
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / f"{unit_id}.mp3"
        with _dashscope_runtime(self.api_key, self.base_url):
            synthesizer = self.synthesizer_factory(
                model=self.target_model,
                voice=voice,
                format=_mp3_audio_format(),
                speech_rate=float(payload.get("speed") or 1.0),
            )
            audio = synthesizer.call(
                text,
                timeout_millis=int(self.request_timeout_seconds * 1000),
            )
        if not isinstance(audio, bytes | bytearray):
            raise CosyVoiceCloneError("CosyVoice TTS did not return audio bytes.")
        audio_path.write_bytes(bytes(audio))
        logger.info(
            "cosyvoice.synthesize_speech",
            task_id=unit_id,
            target_model=self.target_model,
            characters=len(text),
        )
        return {
            "audio_path": str(audio_path),
            "timeline": [],
            "duration_ms": 0,
            "mime_type": "audio/mpeg",
            "size_bytes": audio_path.stat().st_size,
            "provider": _TTS_PROVIDER_NAME,
            "model": self.target_model,
            "characters": len(text),
        }


def _create_enrollment_service(api_key: str) -> Any:
    from dashscope.audio.tts_v2.enrollment import VoiceEnrollmentService

    return VoiceEnrollmentService(api_key=api_key)


def _create_synthesizer(**kwargs: Any) -> Any:
    from dashscope.audio.tts_v2 import SpeechSynthesizer

    return SpeechSynthesizer(**kwargs)


def _mp3_audio_format() -> Any:
    from dashscope.audio.tts_v2.speech_synthesizer import AudioFormat

    return AudioFormat.MP3_24000HZ_MONO_256KBPS


def _normalise_base_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


def _workspace_websocket_base_url(http_base_url: str) -> str:
    parts = urlsplit(http_base_url)
    scheme = parts.scheme
    if parts.scheme == "https":
        scheme = "wss"
    elif parts.scheme == "http":
        scheme = "ws"
    path = parts.path.rstrip("/")
    if path.endswith("/api/v1"):
        path = f"{path[: -len('/api/v1')]}/api-ws/v1/inference"
    elif "/api/" in path:
        path = path.replace("/api/", "/api-ws/", 1)
        if not path.endswith("/inference"):
            path = f"{path}/inference"
    elif not path.endswith("/inference"):
        path = f"{path}/api-ws/v1/inference"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


@contextmanager
def _dashscope_runtime(api_key: str, base_url: str):
    import dashscope

    previous_api_key = getattr(dashscope, "api_key", None)
    previous_http_url = getattr(dashscope, "base_http_api_url", None)
    previous_websocket_url = getattr(dashscope, "base_websocket_api_url", None)
    dashscope.api_key = api_key
    if base_url:
        dashscope.base_http_api_url = base_url
        dashscope.base_websocket_api_url = _workspace_websocket_base_url(base_url)
    try:
        yield
    finally:
        dashscope.api_key = previous_api_key
        dashscope.base_http_api_url = previous_http_url
        dashscope.base_websocket_api_url = previous_websocket_url


def _safe_prefix(payload: Mapping[str, Any]) -> str:
    raw = (
        str(payload.get("brand_voice_id") or "")
        or str(payload.get("source_audio_asset_id") or "")
        or str(payload.get("name") or "")
    ).lower()
    compact = "".join(_SAFE_PREFIX_PATTERN.findall(raw))
    if not compact:
        compact = "voice"
    return f"bv{compact}"[:10]


def _cosyvoice_voice_clone_factory(config: ProviderConfig) -> CosyVoiceCloneProvider:
    values = config.config or {}
    return CosyVoiceCloneProvider(
        api_key=settings.engine_cosyvoice_voice_clone_api_key,
        target_model=str(
            values.get("target_model") or settings.engine_cosyvoice_voice_clone_target_model
        ),
        request_timeout_seconds=float(
            values.get("request_timeout_seconds")
            or settings.engine_cosyvoice_voice_clone_request_timeout_seconds
        ),
        output_dir=str(values.get("output_dir")) if values.get("output_dir") else None,
        base_url=settings.engine_cosyvoice_voice_clone_base_url,
    )


register_provider("voice_clone", _PROVIDER_NAME, _cosyvoice_voice_clone_factory)
