import base64
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, ProviderConfig


class _Response:
    def __init__(self, lines: list[dict]) -> None:
        self.lines = lines

    def iter_lines(self, decode_unicode: bool = False):
        for item in self.lines:
            line = f"data: {json.dumps(item, separators=(',', ':'))}"
            yield line if decode_unicode else line.encode("utf-8")

    def raise_for_status(self) -> None:
        return None


class _FakeHTTP:
    def __init__(self, responses: list[list[dict]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url: str, *, json: dict, headers: dict, timeout: float, stream: bool):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
                "stream": stream,
            }
        )
        return _Response(self.responses.pop(0))


class _Utf8SseResponse:
    def __init__(self, lines: list[dict]) -> None:
        self.lines = lines
        self.encoding = "ISO-8859-1"
        self.decode_unicode_calls: list[bool] = []

    def iter_lines(self, decode_unicode: bool = False):
        self.decode_unicode_calls.append(decode_unicode)
        for item in self.lines:
            line = (
                f"data: {json.dumps(item, ensure_ascii=False, separators=(',', ':'))}"
            ).encode()
            if decode_unicode:
                yield line.decode("latin-1")
            else:
                yield line


def _seed_sse_response(audio: bytes, *, words: list[dict]) -> list[dict]:
    return [
        {"code": 0, "message": "", "data": base64.b64encode(audio).decode("ascii")},
        {"code": 0, "data": None, "sentence": {"text": "hello", "words": words}},
        {"code": 20000000, "message": "ok", "data": None, "usage": {"characters": 5}},
    ]


def test_parse_sse_response_decodes_utf8_chinese_timeline_from_bytes():
    from app.providers.tts.doubao_seed_tts_provider import _parse_sse_response

    response = _Utf8SseResponse(
        _seed_sse_response(
            b"MP3",
            words=[
                {"word": "公", "startTime": 0.0, "endTime": 0.2},
                {"word": "益", "startTime": 0.2, "endTime": 0.4},
                {"word": "简单", "startTime": 0.4, "endTime": 0.8},
            ],
        )
    )

    audio, timeline = _parse_sse_response(response)

    assert audio == b"MP3"
    assert [item["text"] for item in timeline] == ["公", "益", "简单"]
    assert "æ" not in "".join(str(item["text"]) for item in timeline)
    assert "�" not in "".join(str(item["text"]) for item in timeline)


def test_parse_sse_response_does_not_ask_requests_to_latin1_decode():
    from app.providers.tts.doubao_seed_tts_provider import _parse_sse_response

    response = _Utf8SseResponse(
        _seed_sse_response(
            b"MP3",
            words=[{"word": "微笑", "startTime": 0.0, "endTime": 0.5}],
        )
    )

    _audio, timeline = _parse_sse_response(response)

    assert response.decode_unicode_calls == [False]
    assert response.encoding == "utf-8"
    assert timeline[0]["text"] == "微笑"


@pytest.mark.asyncio
async def test_doubao_seed_tts_sends_auth_header_and_locked_request_body(tmp_path: Path):
    from app.providers.tts.doubao_seed_tts_provider import DoubaoSeedTTSProvider

    http = _FakeHTTP(
        [
            _seed_sse_response(
                b"MP3",
                words=[{"word": "hello", "startTime": 0.205, "endTime": 0.315}],
            )
        ]
    )
    provider = DoubaoSeedTTSProvider(
        appid="doubao-appid",
        access_token="seed-token",
        resource_id="seed-tts-2.0",
        default_voice="zh_male_m191_uranus_bigtts",
        http_client=http,
        output_dir=str(tmp_path),
        request_timeout_seconds=12,
    )

    result = await provider.synthesize_speech(
        {
            "text": "hello",
            "voice": "zh_male_m191_uranus_bigtts",
            "speed": 1.25,
            "task_id": "tts-unit",
        }
    )

    assert Path(result["audio_path"]).read_bytes() == b"MP3"
    assert result["duration_ms"] == 315
    assert result["timeline"] == [{"text": "hello", "start_ms": 205, "end_ms": 315}]
    call = http.calls[0]
    assert call["url"] == "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
    assert call["stream"] is True
    assert call["headers"]["X-Api-App-Id"] == "doubao-appid"
    assert call["headers"]["X-Api-Access-Key"] == "seed-token"
    assert call["headers"]["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert "Authorization" not in call["headers"]
    assert call["timeout"] == 12
    body = call["json"]
    assert body["user"]["uid"] == "tts-unit"
    assert body["req_params"]["text"] == "hello"
    assert body["req_params"]["speaker"] == "zh_male_m191_uranus_bigtts"
    assert body["req_params"]["audio_params"] == {
        "format": "mp3",
        "sample_rate": 24000,
        "speech_rate": 25,
        "enable_subtitle": True,
    }
    assert json.loads(body["req_params"]["additions"]) == {
        "disable_markdown_filter": True,
        "aigc_watermark": True,
    }


@pytest.mark.asyncio
async def test_doubao_seed_tts_can_disable_aigc_watermark_in_additions(
    tmp_path: Path,
):
    from app.providers.tts.doubao_seed_tts_provider import DoubaoSeedTTSProvider

    http = _FakeHTTP(
        [
            _seed_sse_response(
                b"MP3",
                words=[{"word": "hello", "startTime": 0.0, "endTime": 0.2}],
            )
        ]
    )
    provider = DoubaoSeedTTSProvider(
        appid="doubao-appid",
        access_token="seed-token",
        http_client=http,
        output_dir=str(tmp_path),
        aigc_watermark=False,
    )

    await provider.synthesize_speech(
        {
            "text": "hello",
            "voice": "zh_male_m191_uranus_bigtts",
            "task_id": "tts-no-watermark-unit",
        }
    )

    additions = json.loads(http.calls[0]["json"]["req_params"]["additions"])
    assert additions == {"disable_markdown_filter": True, "aigc_watermark": False}


@pytest.mark.asyncio
async def test_doubao_seed_tts_splits_long_text_and_offsets_timeline(tmp_path: Path):
    from app.providers.tts.doubao_seed_tts_provider import DoubaoSeedTTSProvider

    http = _FakeHTTP(
        [
            _seed_sse_response(
                b"AAA",
                words=[{"word": "first", "startTime": 0.0, "endTime": 1.0}],
            ),
            _seed_sse_response(
                b"BBB",
                words=[{"word": "second", "startTime": 0.0, "endTime": 0.7}],
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
    assert [call["json"]["req_params"]["text"] for call in http.calls] == [
        "first sentence.",
        "second sentence.",
    ]
    assert Path(result["audio_path"]).read_bytes() == b"AAABBB"
    assert result["duration_ms"] == 1700
    assert result["timeline"] == [
        {"text": "first", "start_ms": 0, "end_ms": 1000},
        {"text": "second", "start_ms": 1000, "end_ms": 1700},
    ]


@pytest.mark.asyncio
async def test_doubao_seed_tts_maps_speech_rate_and_raises_provider_error(tmp_path: Path):
    from app.providers.tts.doubao_seed_tts_provider import DoubaoSeedTTSError, DoubaoSeedTTSProvider

    http = _FakeHTTP(
        [
            [
                {
                    "code": 45000000,
                    "message": "speaker permission denied",
                    "data": None,
                }
            ]
        ]
    )
    provider = DoubaoSeedTTSProvider(
        appid="doubao-appid",
        access_token="seed-token",
        http_client=http,
        output_dir=str(tmp_path),
    )

    with pytest.raises(DoubaoSeedTTSError, match="speaker permission denied"):
        await provider.synthesize_speech(
            {
                "text": "hello",
                "voice": "zh_female_xiaohe_uranus_bigtts",
                "speed": 2.5,
                "task_id": "tts-error-unit",
            }
        )

    assert http.calls[0]["json"]["req_params"]["audio_params"]["speech_rate"] == 100


def test_doubao_seed_tts_factory_injects_watermark_setting(monkeypatch):
    from app.providers.tts import doubao_seed_tts_provider as module

    monkeypatch.setattr(module.settings, "engine_doubao_tts_appid", "doubao-appid", raising=False)
    monkeypatch.setattr(
        module.settings,
        "engine_doubao_tts_access_token",
        "seed-token",
        raising=False,
    )
    monkeypatch.setattr(module.settings, "engine_doubao_tts_api_key", "", raising=False)
    monkeypatch.setattr(
        module.settings,
        "engine_doubao_tts_aigc_watermark",
        False,
        raising=False,
    )

    provider = module._doubao_seed_tts_factory(
        ProviderConfig(
            tenant_id=None,
            capability="tts",
            provider="doubao-seed-tts",
            config={},
        )
    )

    assert provider.aigc_watermark is False


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
