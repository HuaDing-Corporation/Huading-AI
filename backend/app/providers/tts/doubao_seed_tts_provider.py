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

_DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
_DEFAULT_CLUSTER = "volcano_tts"
_DEFAULT_MODEL = "seed-tts-1.1"
_DEFAULT_VOICE = "BV001_streaming"
_SENTENCE_PATTERN = re.compile(r"[^.!?\n。！？；;]+[.!?\n。！？；;]*")


class DoubaoSeedTTSError(RuntimeError):
    pass


class DoubaoSeedTTSProvider:
    def __init__(
        self,
        *,
        appid: str,
        access_token: str,
        cluster: str = _DEFAULT_CLUSTER,
        default_voice: str = _DEFAULT_VOICE,
        endpoint: str = _DEFAULT_ENDPOINT,
        model: str = _DEFAULT_MODEL,
        http_client: Any | None = None,
        output_dir: str | None = None,
        request_timeout_seconds: float = 60.0,
        max_text_bytes: int = 1024,
    ) -> None:
        if not appid or not access_token:
            raise DoubaoSeedTTSError("Doubao Seed-TTS appid and access token are required.")
        self.appid = appid
        self.access_token = access_token
        self.cluster = cluster or _DEFAULT_CLUSTER
        self.default_voice = default_voice or _DEFAULT_VOICE
        self.endpoint = endpoint or _DEFAULT_ENDPOINT
        self.model = model or _DEFAULT_MODEL
        self.output_dir = Path(output_dir) if output_dir else None
        self.request_timeout_seconds = request_timeout_seconds
        self.max_text_bytes = max_text_bytes
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
        task_id = str(payload.get("task_id") or uuid4().hex)
        output_dir = Path(
            str(payload.get("output_dir") or self.output_dir or tempfile.gettempdir())
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / f"{task_id}.mp3"
        voice = self._voice_type(str(payload.get("voice") or ""))
        speed = float(payload.get("speed") or 1.0)
        uid = str(payload.get("uid") or task_id or "huading")

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
            "app": {
                "appid": self.appid,
                "token": self.access_token,
                "cluster": self.cluster,
            },
            "user": {"uid": uid},
            "audio": {
                "voice_type": voice,
                "encoding": "mp3",
                "speed_ratio": speed,
                "rate": 24000,
            },
            "request": {
                "reqid": uuid4().hex,
                "text": text,
                "operation": "query",
                "with_timestamp": 1,
                "model": self.model,
                "extra_param": json.dumps(
                    {"disable_markdown_filter": True, "aigc_watermark": True},
                    separators=(",", ":"),
                ),
            },
        }
        response = self.http.post(
            self.endpoint,
            json=body,
            headers={
                "Authorization": f"Bearer;{self.access_token}",
                "Content-Type": "application/json",
            },
            timeout=self.request_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        code = int(data.get("code") or 0)
        if code != 3000:
            message = str(data.get("message") or data.get("msg") or data)
            raise DoubaoSeedTTSError(message)
        audio_b64 = str(data.get("data") or "")
        try:
            audio = base64.b64decode(audio_b64)
        except Exception as exc:
            raise DoubaoSeedTTSError("Doubao Seed-TTS returned invalid base64 audio.") from exc
        duration_ms = _duration_ms(data)
        timeline = _extract_timeline(data)
        if duration_ms <= 0:
            duration_ms = max((int(item["end_ms"]) for item in timeline), default=0)
        return audio, timeline, duration_ms

    def _voice_type(self, value: str) -> str:
        # The existing voice table may still contain Edge voice ids. Keep those
        # selectable while routing synthesis through the configured Doubao voice.
        if not value or value.startswith("zh-"):
            return self.default_voice
        return value


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


def _duration_ms(data: Mapping[str, Any]) -> int:
    addition = data.get("addition") if isinstance(data, Mapping) else None
    if isinstance(addition, Mapping):
        value = addition.get("duration")
        if value is not None:
            return max(0, int(round(float(value))))
    value = data.get("duration") if isinstance(data, Mapping) else None
    return max(0, int(round(float(value or 0))))


def _extract_timeline(data: Mapping[str, Any]) -> list[dict[str, int | str]]:
    for items in _iter_candidate_timeline_lists(data):
        timeline = _timeline_from_items(items)
        if timeline:
            return timeline
    return []


def _iter_candidate_timeline_lists(value: Any):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_candidate_timeline_lists(item)
    elif isinstance(value, list):
        if all(isinstance(item, Mapping) for item in value):
            yield value
        for item in value:
            yield from _iter_candidate_timeline_lists(item)


def _timeline_from_items(items: list[Mapping[str, Any]]) -> list[dict[str, int | str]]:
    timeline: list[dict[str, int | str]] = []
    for item in items:
        text = _first_present(item, ("word", "text", "char", "grapheme"))
        start = _first_present(item, ("start_ms", "start_time", "start", "begin_time", "begin"))
        end = _first_present(item, ("end_ms", "end_time", "end", "finish_time", "finish"))
        if text is None or start is None or end is None:
            return []
        start_ms = _time_to_ms(start, key_hint="start_ms" if "start_ms" in item else "")
        end_ms = _time_to_ms(end, key_hint="end_ms" if "end_ms" in item else "")
        timeline.append({"text": str(text), "start_ms": start_ms, "end_ms": end_ms})
    return timeline


def _first_present(item: Mapping[str, Any], keys: tuple[str, ...]) -> Any | None:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _time_to_ms(value: Any, *, key_hint: str = "") -> int:
    numeric = float(value or 0)
    if key_hint.endswith("_ms"):
        return max(0, int(round(numeric)))
    if 0 < numeric < 60 and not float(numeric).is_integer():
        return max(0, int(round(numeric * 1000)))
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
        cluster=str(values.get("cluster") or settings.engine_doubao_tts_cluster),
        default_voice=str(
            values.get("default_voice") or settings.engine_doubao_tts_default_voice
        ),
        endpoint=str(values.get("endpoint") or settings.engine_doubao_tts_endpoint),
        model=str(values.get("model") or settings.engine_doubao_tts_model),
        request_timeout_seconds=float(
            values.get("request_timeout_seconds")
            or settings.engine_doubao_tts_request_timeout_seconds
        ),
        output_dir=str(values.get("output_dir")) if values.get("output_dir") else None,
    )


register_provider("tts", "doubao-seed-tts", _doubao_seed_tts_factory)
