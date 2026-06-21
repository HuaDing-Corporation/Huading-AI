"""cors_origins env parsing (#M2-INT-FIX).

pydantic-settings v2 JSON-decodes list fields from env before validation; the
NoDecode annotation + the before-validator must accept a comma string, a single
URL, and a JSON array. These tests go through the real env source.
"""

import pytest

from app.core.config import Settings

_JWT = "x" * 32


def _settings_with_cors(monkeypatch, raw: str) -> Settings:
    monkeypatch.setenv("CORS_ORIGINS", raw)
    # _env_file=None: ignore any local .env so the env var is the only source.
    return Settings(_env_file=None, jwt_secret_key=_JWT)


def test_comma_separated(monkeypatch) -> None:
    s = _settings_with_cors(monkeypatch, "http://localhost:3000, http://127.0.0.1:3000")
    assert s.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_single_url(monkeypatch) -> None:
    s = _settings_with_cors(monkeypatch, "http://localhost:3000")
    assert s.cors_origins == ["http://localhost:3000"]


def test_json_array(monkeypatch) -> None:
    s = _settings_with_cors(monkeypatch, '["http://a.example", "http://b.example"]')
    assert s.cors_origins == ["http://a.example", "http://b.example"]


def test_default_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    s = Settings(_env_file=None, jwt_secret_key=_JWT)
    assert "http://localhost:3000" in s.cors_origins


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_string(monkeypatch, raw: str) -> None:
    s = _settings_with_cors(monkeypatch, raw)
    assert s.cors_origins == []


def test_omnihuman_settings_are_env_driven_without_default_secret(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_OMNIHUMAN_ACCESS_KEY", "ak")
    monkeypatch.setenv("ENGINE_OMNIHUMAN_SECRET_KEY", "sk")
    monkeypatch.setenv("ENGINE_OMNIHUMAN_REGION", "cn-north-1")
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_omnihuman_access_key == "ak"
    assert s.engine_omnihuman_secret_key == "sk"
    assert s.engine_omnihuman_region == "cn-north-1"
    assert s.engine_omnihuman_request_timeout_seconds > 0
    assert s.engine_omnihuman_timeout_seconds > 0


def test_doubao_seed_tts_settings_are_env_driven(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_DOUBAO_TTS_APPID", "doubao-appid")
    monkeypatch.setenv("ENGINE_DOUBAO_TTS_ACCESS_TOKEN", "seed-token")
    monkeypatch.setenv("ENGINE_DOUBAO_TTS_CLUSTER", "volcano_tts")
    monkeypatch.setenv("ENGINE_DOUBAO_TTS_DEFAULT_VOICE", "BV001_streaming")
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_doubao_tts_appid == "doubao-appid"
    assert s.engine_doubao_tts_access_token == "seed-token"
    assert s.engine_doubao_tts_cluster == "volcano_tts"
    assert s.engine_doubao_tts_default_voice == "BV001_streaming"
    assert s.engine_doubao_tts_endpoint == "https://openspeech.bytedance.com/api/v1/tts"
    assert s.engine_doubao_tts_model == "seed-tts-1.1"
    assert s.engine_doubao_tts_request_timeout_seconds > 0
