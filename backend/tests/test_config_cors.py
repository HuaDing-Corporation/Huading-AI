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
    monkeypatch.delenv("ENGINE_CORS_ORIGINS", raising=False)
    s = Settings(_env_file=None, jwt_secret_key=_JWT)
    assert "http://localhost:3000" in s.cors_origins


def test_engine_cors_origins_override_legacy_cors_env(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv(
        "ENGINE_CORS_ORIGINS",
        "https://huadingai.cn, https://www.huadingai.cn",
    )
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.effective_cors_origins == [
        "https://huadingai.cn",
        "https://www.huadingai.cn",
    ]


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
    monkeypatch.setenv("ENGINE_DOUBAO_TTS_RESOURCE_ID", "seed-tts-2.0")
    monkeypatch.setenv("ENGINE_DOUBAO_TTS_DEFAULT_VOICE", "zh_male_m191_uranus_bigtts")
    monkeypatch.setenv("ENGINE_DOUBAO_TTS_AIGC_WATERMARK", "false")
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_doubao_tts_appid == "doubao-appid"
    assert s.engine_doubao_tts_access_token == "seed-token"
    assert s.engine_doubao_tts_resource_id == "seed-tts-2.0"
    assert s.engine_doubao_tts_default_voice == "zh_male_m191_uranus_bigtts"
    assert s.engine_doubao_tts_endpoint == (
        "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
    )
    assert s.engine_doubao_tts_request_timeout_seconds > 0
    assert s.engine_doubao_tts_aigc_watermark is False


def test_doubao_seed_tts_watermark_defaults_to_true(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_DOUBAO_TTS_AIGC_WATERMARK", raising=False)
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_doubao_tts_aigc_watermark is True


def test_doubao_voice_clone_settings_are_env_driven(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_DOUBAO_VOICE_CLONE_APPID", "clone-appid")
    monkeypatch.setenv("ENGINE_DOUBAO_VOICE_CLONE_ACCESS_TOKEN", "clone-token")
    monkeypatch.setenv("ENGINE_DOUBAO_VOICE_CLONE_RESOURCE_ID", "seed-icl-2.0")
    monkeypatch.setenv(
        "ENGINE_DOUBAO_VOICE_CLONE_ENDPOINT",
        "https://openspeech.bytedance.com/api/v3/voice-clone",
    )
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_doubao_voice_clone_appid == "clone-appid"
    assert s.engine_doubao_voice_clone_access_token == "clone-token"
    assert s.engine_doubao_voice_clone_resource_id == "seed-icl-2.0"
    assert s.engine_doubao_voice_clone_endpoint == (
        "https://openspeech.bytedance.com/api/v3/voice-clone"
    )
    assert s.engine_doubao_voice_clone_request_timeout_seconds > 0


def test_aigc_producer_settings_are_env_driven(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_AIGC_PRODUCER", "Huading")
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_aigc_producer == "Huading"


def test_aigc_producer_has_default(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_AIGC_PRODUCER", raising=False)
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_aigc_producer


def test_apimart_video_settings_are_env_driven(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_APIMART_VIDEO_MODEL", "custom-seedance-video")
    monkeypatch.setenv("ENGINE_APIMART_VIDEO_POLL_INITIAL_DELAY_SECONDS", "31")
    monkeypatch.setenv("ENGINE_APIMART_VIDEO_POLL_INTERVAL_SECONDS", "11")
    monkeypatch.setenv("ENGINE_APIMART_VIDEO_TIMEOUT_SECONDS", "901")
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_apimart_video_model == "custom-seedance-video"
    assert s.engine_apimart_video_poll_initial_delay_seconds == 31
    assert s.engine_apimart_video_poll_interval_seconds == 11
    assert s.engine_apimart_video_timeout_seconds == 901


def test_apimart_cost_settings_are_env_driven(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_APIMART_CREDIT_USD", "0.20")
    monkeypatch.setenv("ENGINE_USD_CNY_RATE", "7.5")
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_apimart_credit_usd == 0.20
    assert s.engine_usd_cny_rate == 7.5


def test_apimart_cost_settings_have_defaults(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_APIMART_CREDIT_USD", raising=False)
    monkeypatch.delenv("ENGINE_USD_CNY_RATE", raising=False)
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_apimart_credit_usd == 0.10
    assert s.engine_usd_cny_rate == 7.2


def test_direct_cny_provider_cost_settings_are_env_driven(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_OMNIHUMAN_CNY_PER_SEC", "1.5")
    monkeypatch.setenv("ENGINE_SEEDTTS_CNY_PER_CHAR", "0.0004")
    monkeypatch.setenv("ENGINE_DEEPSEEK_CNY_PER_1K_INPUT", "0.002")
    monkeypatch.setenv("ENGINE_DEEPSEEK_CNY_PER_1K_OUTPUT", "0.003")
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_omnihuman_cny_per_sec == 1.5
    assert s.engine_seedtts_cny_per_char == 0.0004
    assert s.engine_deepseek_cny_per_1k_input == 0.002
    assert s.engine_deepseek_cny_per_1k_output == 0.003


def test_direct_cny_provider_cost_settings_have_defaults(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_OMNIHUMAN_CNY_PER_SEC", raising=False)
    monkeypatch.delenv("ENGINE_SEEDTTS_CNY_PER_CHAR", raising=False)
    monkeypatch.delenv("ENGINE_DEEPSEEK_CNY_PER_1K_INPUT", raising=False)
    monkeypatch.delenv("ENGINE_DEEPSEEK_CNY_PER_1K_OUTPUT", raising=False)
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_omnihuman_cny_per_sec == 1.0
    assert s.engine_seedtts_cny_per_char == 0.0003
    assert s.engine_deepseek_cny_per_1k_input == 0.001008
    assert s.engine_deepseek_cny_per_1k_output == 0.002016
