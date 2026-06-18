from pathlib import Path

import pytest

from app.providers.tts.edge_tts_provider import EdgeTTSProvider


@pytest.mark.asyncio
async def test_edge_tts_provider_writes_audio_and_word_boundaries(monkeypatch, tmp_path: Path):
    class _FakeCommunicate:
        def __init__(self, text: str, voice: str, rate: str) -> None:
            self.text = text
            self.voice = voice
            self.rate = rate

        async def stream(self):
            yield {"type": "audio", "data": b"MP3"}
            yield {"type": "WordBoundary", "offset": 0, "duration": 5000000, "text": "hello"}
            yield {"type": "WordBoundary", "offset": 5000000, "duration": 5000000, "text": "world"}

    monkeypatch.setattr(
        "app.providers.tts.edge_tts_provider.edge_tts_sdk.Communicate",
        _FakeCommunicate,
    )
    provider = EdgeTTSProvider(output_dir=str(tmp_path))

    result = await provider.synthesize_speech(
        {
            "text": "hello world",
            "voice": "zh-CN-XiaoxiaoNeural",
            "speed": 1.0,
            "task_id": "task-tts",
        }
    )

    audio_path = Path(result["audio_path"])
    assert audio_path.read_bytes() == b"MP3"
    assert result["duration_ms"] == 1000
    assert result["timeline"] == [
        {"text": "hello", "start_ms": 0, "end_ms": 500},
        {"text": "world", "start_ms": 500, "end_ms": 1000},
    ]
