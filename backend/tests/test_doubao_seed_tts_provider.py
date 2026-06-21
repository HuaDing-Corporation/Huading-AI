import base64
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, ProviderConfig


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class _FakeHTTP:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url: str, *, json: dict, headers: dict, timeout: float):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _Response(self.responses.pop(0))


def _seed_response(audio: bytes, *, duration: int, words: list[dict]) -> dict:
    return {
        "code": 3000,
        "message": "Success",
        "data": base64.b64encode(audio).decode("ascii"),
        "addition": {"duration": duration, "frontend": {"words": words}},
    }


@pytest.mark.asyncio
async def test_doubao_seed_tts_sends_auth_header_and_locked_request_body(tmp_path: Path):
    from app.providers.tts.doubao_seed_tts_provider import DoubaoSeedTTSProvider

    http = _FakeHTTP(
        [
            _seed_response(
                b"MP3",
                duration=800,
                words=[{"word": "hello", "start_time": 0, "end_time": 800}],
            )
        ]
    )
    provider = DoubaoSeedTTSProvider(
        appid="doubao-appid",
        access_token="seed-token",
        default_voice="BV001_streaming",
        http_client=http,
        output_dir=str(tmp_path),
        request_timeout_seconds=12,
    )

    result = await provider.synthesize_speech(
        {"text": "hello", "voice": "BV001_streaming", "speed": 1.1, "task_id": "tts-unit"}
    )

    assert Path(result["audio_path"]).read_bytes() == b"MP3"
    assert result["duration_ms"] == 800
    assert result["timeline"] == [{"text": "hello", "start_ms": 0, "end_ms": 800}]
    call = http.calls[0]
    assert call["url"] == "https://openspeech.bytedance.com/api/v1/tts"
    assert call["headers"]["Authorization"] == "Bearer;seed-token"
    assert call["timeout"] == 12
    body = call["json"]
    assert body["app"] == {
        "appid": "doubao-appid",
        "token": "seed-token",
        "cluster": "volcano_tts",
    }
    assert body["user"]["uid"] == "tts-unit"
    assert body["audio"] == {
        "voice_type": "BV001_streaming",
        "encoding": "mp3",
        "speed_ratio": 1.1,
        "rate": 24000,
    }
    assert body["request"]["operation"] == "query"
    assert body["request"]["with_timestamp"] == 1
    assert body["request"]["model"] == "seed-tts-1.1"
    assert json.loads(body["request"]["extra_param"]) == {
        "disable_markdown_filter": True,
        "aigc_watermark": True,
    }


@pytest.mark.asyncio
async def test_doubao_seed_tts_splits_long_text_and_offsets_timeline(tmp_path: Path):
    from app.providers.tts.doubao_seed_tts_provider import DoubaoSeedTTSProvider

    http = _FakeHTTP(
        [
            _seed_response(
                b"AAA",
                duration=1000,
                words=[{"word": "first", "start_time": 0, "end_time": 1000}],
            ),
            _seed_response(
                b"BBB",
                duration=700,
                words=[{"word": "second", "start_time": 0, "end_time": 700}],
            ),
        ]
    )
    provider = DoubaoSeedTTSProvider(
        appid="doubao-appid",
        access_token="seed-token",
        http_client=http,
        output_dir=str(tmp_path),
        max_text_bytes=24,
    )

    result = await provider.synthesize_speech(
        {
            "text": "first sentence. second sentence.",
            "voice": "zh-CN-XiaoxiaoNeural",
            "speed": 1.0,
            "task_id": "long-tts-unit",
        }
    )

    assert len(http.calls) == 2
    assert [call["json"]["request"]["text"] for call in http.calls] == [
        "first sentence.",
        "second sentence.",
    ]
    assert Path(result["audio_path"]).read_bytes() == b"AAABBB"
    assert result["duration_ms"] == 1700
    assert result["timeline"] == [
        {"text": "first", "start_ms": 0, "end_ms": 1000},
        {"text": "second", "start_ms": 1000, "end_ms": 1700},
    ]


def test_tts_resolve_prefers_doubao_default_when_credentials_exist(monkeypatch):
    from app.providers import base
    from app.providers.base import register_provider, resolve
    from app.providers.tts.doubao_seed_tts_provider import DoubaoSeedTTSProvider
    from app.providers.tts.edge_tts_provider import EdgeTTSProvider

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    monkeypatch.setattr(base.settings, "engine_doubao_tts_appid", "doubao-appid", raising=False)
    monkeypatch.setattr(
        base.settings,
        "engine_doubao_tts_access_token",
        "seed-token",
        raising=False,
    )
    register_provider("tts", "edge-tts", lambda _config: EdgeTTSProvider())
    register_provider(
        "tts",
        "doubao-seed-tts",
        lambda _config: DoubaoSeedTTSProvider(
            appid=base.settings.engine_doubao_tts_appid,
            access_token=base.settings.engine_doubao_tts_access_token,
        ),
    )

    try:
        with SessionTesting() as db:
            db.add(
                ProviderConfig(
                    tenant_id=None,
                    capability="tts",
                    provider="edge-tts",
                    config={},
                )
            )
            db.commit()

            assert isinstance(
                resolve(db, tenant_id="tenant-a", capability="tts"),
                DoubaoSeedTTSProvider,
            )
    finally:
        Base.metadata.drop_all(engine)


def test_tts_resolve_falls_back_to_edge_when_doubao_credentials_missing(monkeypatch):
    from app.providers import base
    from app.providers.base import register_provider, resolve
    from app.providers.tts.edge_tts_provider import EdgeTTSProvider

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    monkeypatch.setattr(base.settings, "engine_doubao_tts_appid", "", raising=False)
    monkeypatch.setattr(base.settings, "engine_doubao_tts_access_token", "", raising=False)
    register_provider("tts", "edge-tts", lambda _config: EdgeTTSProvider())

    try:
        with SessionTesting() as db:
            db.add(
                ProviderConfig(
                    tenant_id=None,
                    capability="tts",
                    provider="edge-tts",
                    config={},
                )
            )
            db.commit()

            assert isinstance(resolve(db, tenant_id="tenant-a", capability="tts"), EdgeTTSProvider)
    finally:
        Base.metadata.drop_all(engine)
