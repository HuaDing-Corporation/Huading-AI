from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import edge_tts as edge_tts_sdk

from app.db.models import ProviderConfig
from app.providers.base import register_provider


def _speed_to_rate(speed: float) -> str:
    percent = int(round((speed - 1.0) * 100))
    if percent == 0:
        return "+0%"
    sign = "+" if percent > 0 else ""
    return f"{sign}{percent}%"


def _ticks_to_ms(value: int | float) -> int:
    return int(round(float(value) / 10_000))


class EdgeTTSProvider:
    def __init__(self, *, output_dir: str | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir else None

    async def synthesize_speech(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "")
        voice = str(payload.get("voice") or "zh-CN-XiaoxiaoNeural")
        speed = float(payload.get("speed") or 1.0)
        task_id = str(payload.get("task_id") or uuid4().hex)
        if not text.strip():
            raise ValueError("TTS text is required.")

        output_dir = Path(
            str(payload.get("output_dir") or self.output_dir or tempfile.gettempdir())
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / f"{task_id}.mp3"
        timeline: list[dict[str, int | str]] = []
        audio = bytearray()
        communicate = edge_tts_sdk.Communicate(text, voice, rate=_speed_to_rate(speed))
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio.extend(chunk.get("data") or b"")
            elif chunk.get("type") == "WordBoundary":
                start_ms = _ticks_to_ms(chunk.get("offset") or 0)
                duration_ms = _ticks_to_ms(chunk.get("duration") or 0)
                timeline.append(
                    {
                        "text": str(chunk.get("text") or ""),
                        "start_ms": start_ms,
                        "end_ms": start_ms + duration_ms,
                    }
                )
        audio_path.write_bytes(bytes(audio))
        duration_ms = max((int(item["end_ms"]) for item in timeline), default=0)
        return {
            "audio_path": str(audio_path),
            "timeline": timeline,
            "duration_ms": duration_ms,
            "mime_type": "audio/mpeg",
            "size_bytes": audio_path.stat().st_size,
        }


def _edge_tts_factory(config: ProviderConfig) -> EdgeTTSProvider:
    output_dir = (config.config or {}).get("output_dir")
    return EdgeTTSProvider(output_dir=str(output_dir) if output_dir else None)


register_provider("tts", "edge-tts", _edge_tts_factory)
