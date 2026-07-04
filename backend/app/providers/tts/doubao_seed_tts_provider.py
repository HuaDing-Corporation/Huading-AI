from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import register_provider

_DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
_DEFAULT_RESOURCE_ID = "seed-tts-2.0"
_DEFAULT_VOICE = "zh_male_m191_uranus_bigtts"
_SENTENCE_PATTERN = re.compile(r"[^.!?\n。！？；;]+[.!?\n。！？；;]*")


class DoubaoSeedTTSError(RuntimeError):
    pass


class DoubaoSeedTTSProvider:
    def __init__(
        self,
        *,
        appid: str,
        access_token: str,
        resource_id: str = _DEFAULT_RESOURCE_ID,
        default_voice: str = _DEFAULT_VOICE,
        endpoint: str = _DEFAULT_ENDPOINT,
        api_key: str = "",
        http_client: Any | None = None,
        output_dir: str | None = None,
        request_timeout_seconds: float = 60.0,
        max_text_bytes: int = 1024,
        aigc_watermark: bool = True,
    ) -> None:
        if not api_key and (not appid or not access_token):
            raise DoubaoSeedTTSError(
                "Doubao Seed-TTS appid/access token or api key are required."
            )
        self.appid = appid
        self.access_token = access_token
        self.api_key = api_key
        self.resource_id = resource_id or _DEFAULT_RESOURCE_ID
        self.default_voice = default_voice or _DEFAULT_VOICE
        self.endpoint = endpoint or _DEFAULT_ENDPOINT
        self.output_dir = Path(output_dir) if output_dir else None
        self.request_timeout_seconds = request_timeout_seconds
        self.max_text_bytes = max_text_bytes
        self.aigc_watermark = aigc_watermark
        if http_client is None:
            import requests

            http_client = requests.Session()
        self.http = http_client

    async def synthesize_speech(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.synthesize_speech_sync, payload)

    def synthesize_speech_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("TTS text is required.")
        unit_id = str(payload.get("task_id") or uuid4().hex)
        output_dir = Path(
            str(payload.get("output_dir") or self.output_dir or tempfile.gettempdir())
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / f"{unit_id}.mp3"
        voice = self._voice_type(str(payload.get("voice") or ""))
        speed = float(payload.get("speed") or 1.0)
        uid = str(payload.get("uid") or unit_id or "huading")

        audio = bytearray()
        timeline: list[dict[str, int | str]] = []
        offset_ms = 0
        for segment in _split_text(text, max_bytes=self.max_text_bytes):
            segment_audio, segment_timeline, duration_ms = self._synthesize_segment(
                segment,
                voice=voice,
                speed=speed,
                uid=uid,
            )
            audio.extend(segment_audio)
            timeline.extend(_offset_timeline(segment_timeline, offset_ms))
            offset_ms += duration_ms

        audio_path.write_bytes(bytes(audio))
        return {
            "audio_path": str(audio_path),
            "timeline": timeline,
            "duration_ms": offset_ms,
            "mime_type": "audio/mpeg",
            "size_bytes": audio_path.stat().st_size,
            "provider": "doubao-seed-tts",
            "model": self.resource_id,
            "characters": len(text),
        }

    def _synthesize_segment(
        self,
        text: str,
        *,
        voice: str,
        speed: float,
        uid: str,
    ) -> tuple[bytes, list[dict[str, int | str]], int]:
        body = {
            "user": {"uid": uid},
            "req_params": {
                "text": text,
                "speaker": voice,
                "audio_params": {
                    "format": "mp3",
                    "sample_rate": 24000,
                    "speech_rate": _speech_rate(speed),
                    "enable_subtitle": True,
                },
                "additions": json.dumps(
                    {
                        "disable_markdown_filter": True,
                        "aigc_watermark": self.aigc_watermark,
                    },
                    separators=(",", ":"),
                ),
            },
        }
        response = self.http.post(
            self.endpoint,
            json=body,
            headers=self._headers(),
            timeout=self.request_timeout_seconds,
            stream=True,
        )
        response.raise_for_status()
        audio, timeline = _parse_sse_response(response)
        duration_ms = max((int(item["end_ms"]) for item in timeline), default=0)
        return audio, timeline, duration_ms

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

    def _voice_type(self, value: str) -> str:
        # Legacy voice rows contain Edge ids like zh-CN-XiaoxiaoNeural; those
        # cannot be sent to Seed-TTS 2.0, so use the configured 2.0 voice.
        if not value or value.startswith("zh-"):
            return self.default_voice
        return value


def _speech_rate(speed: float) -> int:
    return max(-50, min(100, int(round((speed - 1.0) * 100))))


def _split_text(text: str, *, max_bytes: int) -> list[str]:
    parts = [match.group(0).strip() for match in _SENTENCE_PATTERN.finditer(text)]
    if not parts:
        parts = [text.strip()]
    segments: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current} {part}".strip() if current else part
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
            continue
        if current:
            segments.append(current)
        if len(part.encode("utf-8")) <= max_bytes:
            current = part
        else:
            hard_parts = _hard_split(part, max_bytes=max_bytes)
            segments.extend(hard_parts[:-1])
            current = hard_parts[-1] if hard_parts else ""
    if current:
        segments.append(current)
    return segments


def _hard_split(text: str, *, max_bytes: int) -> list[str]:
    segments: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and len(candidate.encode("utf-8")) > max_bytes:
            segments.append(current)
            current = char
        else:
            current = candidate
    if current:
        segments.append(current)
    return segments


def _parse_sse_response(response: Any) -> tuple[bytes, list[dict[str, int | str]]]:
    audio = bytearray()
    timeline: list[dict[str, int | str]] = []
    finished = False
    response.encoding = "utf-8"
    for raw_line in response.iter_lines():
        line = (
            raw_line.decode("utf-8", errors="replace")
            if isinstance(raw_line, bytes)
            else str(raw_line)
        )
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        event = json.loads(line.removeprefix("data:").strip())
        code = int(event.get("code") or 0)
        if code == 20000000:
            finished = True
            continue
        if code != 0:
            message = str(event.get("message") or event.get("msg") or event)
            raise DoubaoSeedTTSError(message)
        if event.get("data"):
            try:
                audio.extend(base64.b64decode(str(event["data"])))
            except Exception as exc:
                raise DoubaoSeedTTSError(
                    "Doubao Seed-TTS returned invalid base64 audio."
                ) from exc
        sentence = event.get("sentence")
        if isinstance(sentence, Mapping):
            timeline.extend(_timeline_from_words(sentence.get("words") or []))
    if not finished:
        raise DoubaoSeedTTSError("Doubao Seed-TTS stream ended before SessionFinish.")
    return bytes(audio), timeline


def _timeline_from_words(words: Any) -> list[dict[str, int | str]]:
    if not isinstance(words, list):
        return []
    timeline: list[dict[str, int | str]] = []
    for item in words:
        if not isinstance(item, Mapping):
            continue
        text = _first_present(item, ("word", "text", "char", "grapheme"))
        start_key, start = _first_present_with_key(
            item,
            ("startTime", "start_time", "start_ms", "start"),
        )
        end_key, end = _first_present_with_key(
            item,
            ("endTime", "end_time", "end_ms", "end"),
        )
        if text is None or start is None or end is None:
            continue
        timeline.append(
            {
                "text": str(text),
                "start_ms": _time_to_ms(start, key=start_key),
                "end_ms": _time_to_ms(end, key=end_key),
            }
        )
    return timeline


def _first_present(item: Mapping[str, Any], keys: tuple[str, ...]) -> Any | None:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _first_present_with_key(
    item: Mapping[str, Any],
    keys: tuple[str, ...],
) -> tuple[str, Any | None]:
    for key in keys:
        if key in item:
            return key, item[key]
    return "", None


def _time_to_ms(value: Any, *, key: str) -> int:
    numeric = float(value or 0)
    if key in {"startTime", "endTime", "start_time", "end_time"}:
        numeric *= 1000
    return max(0, int(round(numeric)))


def _offset_timeline(
    timeline: list[dict[str, int | str]],
    offset_ms: int,
) -> list[dict[str, int | str]]:
    return [
        {
            "text": str(item["text"]),
            "start_ms": int(item["start_ms"]) + offset_ms,
            "end_ms": int(item["end_ms"]) + offset_ms,
        }
        for item in timeline
    ]


def _doubao_seed_tts_factory(config: ProviderConfig) -> DoubaoSeedTTSProvider:
    values = config.config or {}
    return DoubaoSeedTTSProvider(
        appid=str(values.get("appid") or settings.engine_doubao_tts_appid),
        access_token=str(values.get("access_token") or settings.engine_doubao_tts_access_token),
        api_key=str(values.get("api_key") or settings.engine_doubao_tts_api_key),
        resource_id=str(values.get("resource_id") or settings.engine_doubao_tts_resource_id),
        default_voice=str(
            values.get("default_voice") or settings.engine_doubao_tts_default_voice
        ),
        endpoint=str(values.get("endpoint") or settings.engine_doubao_tts_endpoint),
        request_timeout_seconds=float(
            values.get("request_timeout_seconds")
            or settings.engine_doubao_tts_request_timeout_seconds
        ),
        aigc_watermark=_config_bool(
            values,
            "aigc_watermark",
            default=settings.engine_doubao_tts_aigc_watermark,
        ),
        output_dir=str(values.get("output_dir")) if values.get("output_dir") else None,
    )


def _config_bool(values: Mapping[str, Any], key: str, *, default: bool) -> bool:
    if key not in values or values[key] is None:
        return default
    value = values[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


register_provider("tts", "doubao-seed-tts", _doubao_seed_tts_factory)
