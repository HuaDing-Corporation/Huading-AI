"""cors_origins env parsing (#M2-INT-FIX).

pydantic-settings v2 JSON-decodes list fields from env before validation; the
NoDecode annotation + the before-validator must accept a comma string, a single
URL, and a JSON array. These tests go through the real env source.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_JWT = "x" * 32

_GENERATION_WAIT_ENV = {
    "SSE_TIMEOUT_SECONDS": "sse_timeout_seconds",
    "ENGINE_SEEDANCE_TIMEOUT_SECONDS": "engine_seedance_timeout_seconds",
    "ENGINE_OMNIHUMAN_TIMEOUT_SECONDS": "engine_omnihuman_timeout_seconds",
    "OPENAI_IMAGE_TIMEOUT": "openai_image_timeout",
    "ENGINE_APIMART_TIMEOUT_SECONDS": "engine_apimart_timeout_seconds",
    "ENGINE_APIMART_VIDEO_TIMEOUT_SECONDS": "engine_apimart_video_timeout_seconds",
    "ENGINE_IMAGE_PROVIDER_TIMEOUT_SECONDS": "engine_image_provider_timeout_seconds",
}


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


def test_generation_wait_defaults_allow_1500_seconds(monkeypatch) -> None:
    for env_name in _GENERATION_WAIT_ENV:
        monkeypatch.delenv(env_name, raising=False)

    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    for field_name in _GENERATION_WAIT_ENV.values():
        assert getattr(s, field_name) == 1500

    # Polling endpoints should fail fast per request while the overall task waits.
    assert s.engine_seedance_request_timeout_seconds == 120
    assert s.engine_omnihuman_request_timeout_seconds == 120
    assert s.engine_apimart_request_timeout_seconds == 120


def test_generation_wait_settings_remain_env_overridable(monkeypatch) -> None:
    for index, env_name in enumerate(_GENERATION_WAIT_ENV, start=1):
        monkeypatch.setenv(env_name, str(1500 + index))

    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    for index, field_name in enumerate(_GENERATION_WAIT_ENV.values(), start=1):
        assert getattr(s, field_name) == 1500 + index


def test_generation_heartbeat_interval_defaults_and_is_env_overridable(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_GEN_HEARTBEAT_INTERVAL_SECONDS", raising=False)
    assert (
        Settings(_env_file=None, jwt_secret_key=_JWT).engine_gen_heartbeat_interval_seconds
        == 15
    )

    monkeypatch.setenv("ENGINE_GEN_HEARTBEAT_INTERVAL_SECONDS", "7.5")
    assert (
        Settings(_env_file=None, jwt_secret_key=_JWT).engine_gen_heartbeat_interval_seconds
        == 7.5
    )


@pytest.mark.parametrize("raw", ["1e-12", "1e300", "inf", "NaN", "0", "-1"])
def test_generation_heartbeat_interval_rejects_unsafe_values(
    monkeypatch,
    raw: str,
) -> None:
    monkeypatch.setenv("ENGINE_GEN_HEARTBEAT_INTERVAL_SECONDS", raw)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_secret_key=_JWT)


@pytest.mark.parametrize("raw", ["1e-12", "1e300", "inf", "NaN", "0", "-1"])
def test_orphan_recovery_interval_rejects_values_that_disable_fund_recovery(
    monkeypatch,
    raw: str,
) -> None:
    monkeypatch.setenv("ENGINE_ORPHAN_RECOVERY_INTERVAL_SECONDS", raw)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_secret_key=_JWT)


def test_aibrain_usage_anomaly_cooldown_defaults_and_reads_env(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_AIBRAIN_USAGE_ANOMALY_COOLDOWN_SECONDS", raising=False)
    assert (
        Settings(_env_file=None, jwt_secret_key=_JWT)
        .engine_aibrain_usage_anomaly_cooldown_seconds
        == 60
    )

    monkeypatch.setenv("ENGINE_AIBRAIN_USAGE_ANOMALY_COOLDOWN_SECONDS", "45")
    assert (
        Settings(_env_file=None, jwt_secret_key=_JWT)
        .engine_aibrain_usage_anomaly_cooldown_seconds
        == 45
    )


@pytest.mark.parametrize("raw", ["0", "301"])
def test_aibrain_usage_anomaly_cooldown_cannot_be_disabled(
    monkeypatch,
    raw: str,
) -> None:
    monkeypatch.setenv("ENGINE_AIBRAIN_USAGE_ANOMALY_COOLDOWN_SECONDS", raw)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_secret_key=_JWT)


def test_aibrain_usage_anomaly_cooldown_is_in_every_env_example() -> None:
    repository_root = Path(__file__).parents[2]
    examples = (
        repository_root / "backend" / ".env.example",
        repository_root / "infra" / ".env.example",
        repository_root / "infra" / ".env.prod.example",
    )

    for example in examples:
        contents = example.read_text(encoding="utf-8")
        assert "ENGINE_AIBRAIN_USAGE_ANOMALY_COOLDOWN_SECONDS=60" in contents


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


def test_platform_tenant_slugs_are_normalized_and_deduplicated(monkeypatch) -> None:
    monkeypatch.setenv(
        "ENGINE_PLATFORM_TENANT_SLUGS",
        " Huading, ACME,huading ,, ",
    )

    settings = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert settings.engine_platform_tenant_slugs == {"huading", "acme"}


def test_platform_tenant_slugs_default_to_empty(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_PLATFORM_TENANT_SLUGS", raising=False)

    settings = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert settings.engine_platform_tenant_slugs == set()


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


def test_doubao_seed_tts_watermark_defaults_to_false(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_DOUBAO_TTS_AIGC_WATERMARK", raising=False)
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_doubao_tts_aigc_watermark is False


def test_doubao_voice_clone_settings_are_env_driven(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_DOUBAO_VOICE_CLONE_APPID", "clone-appid")
    monkeypatch.setenv("ENGINE_DOUBAO_VOICE_CLONE_ACCESS_TOKEN", "clone-token")
    monkeypatch.setenv("ENGINE_DOUBAO_VOICE_CLONE_RESOURCE_ID", "volc.megatts.voiceclone")
    monkeypatch.setenv(
        "ENGINE_DOUBAO_VOICE_CLONE_SPEAKER_IDS",
        "S_env_slot_001, S_env_slot_002",
    )
    monkeypatch.setenv(
        "ENGINE_DOUBAO_VOICE_CLONE_ENDPOINT",
        "https://openspeech.bytedance.com/api/v1/mega_tts/audio/upload",
    )
    monkeypatch.setenv("ENGINE_DOUBAO_VOICE_CLONE_MODEL_TYPE", "4")
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_doubao_voice_clone_appid == "clone-appid"
    assert s.engine_doubao_voice_clone_access_token == "clone-token"
    assert s.engine_doubao_voice_clone_resource_id == "volc.megatts.voiceclone"
    assert s.engine_doubao_voice_clone_endpoint == (
        "https://openspeech.bytedance.com/api/v1/mega_tts/audio/upload"
    )
    assert s.engine_doubao_voice_clone_status_endpoint == (
        "https://openspeech.bytedance.com/api/v1/mega_tts/status"
    )
    assert s.engine_doubao_voice_clone_model_type == 4
    assert s.engine_doubao_voice_clone_speaker_ids == [
        "S_env_slot_001",
        "S_env_slot_002",
    ]
    assert s.engine_doubao_voice_clone_request_timeout_seconds > 0


def test_cosyvoice_voice_clone_settings_are_env_driven(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_COSYVOICE_VOICE_CLONE_API_KEY", "dashscope-key")
    monkeypatch.setenv(
        "ENGINE_COSYVOICE_VOICE_CLONE_TARGET_MODEL",
        "cosyvoice-v3.5-plus",
    )
    monkeypatch.setenv("ENGINE_COSYVOICE_VOICE_CLONE_REQUEST_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv(
        "ENGINE_COSYVOICE_VOICE_CLONE_BASE_URL",
        "https://workspace.example/api/v1",
    )
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_cosyvoice_voice_clone_api_key == "dashscope-key"
    assert s.engine_cosyvoice_voice_clone_target_model == "cosyvoice-v3.5-plus"
    assert s.engine_cosyvoice_voice_clone_base_url == "https://workspace.example/api/v1"
    assert s.engine_cosyvoice_voice_clone_request_timeout_seconds == 42


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


def test_ecom_replicate_analysis_concurrency_defaults_and_reads_env(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_ECOM_REPLICATE_ANALYSIS_CONCURRENCY", raising=False)
    default_settings = Settings(_env_file=None, jwt_secret_key=_JWT)

    monkeypatch.setenv("ENGINE_ECOM_REPLICATE_ANALYSIS_CONCURRENCY", "3")
    configured_settings = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert default_settings.engine_ecom_replicate_analysis_concurrency == 4
    assert configured_settings.engine_ecom_replicate_analysis_concurrency == 3


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
    assert s.engine_usd_cny_rate == 7.0


def test_direct_cny_provider_cost_settings_are_env_driven(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_OMNIHUMAN_CNY_PER_SEC", "1.5")
    monkeypatch.setenv("ENGINE_SEEDTTS_CNY_PER_CHAR", "0.0004")
    monkeypatch.setenv("ENGINE_COSYVOICE_TTS_CNY_PER_CHAR", "0.0002")
    monkeypatch.setenv("ENGINE_DEEPSEEK_CNY_PER_1K_INPUT", "0.002")
    monkeypatch.setenv("ENGINE_DEEPSEEK_CNY_PER_1K_CACHE_HIT", "0.00004")
    monkeypatch.setenv("ENGINE_DEEPSEEK_CNY_PER_1K_OUTPUT", "0.003")
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_omnihuman_cny_per_sec == 1.5
    assert s.engine_seedtts_cny_per_char == 0.0004
    assert s.engine_cosyvoice_tts_cny_per_char == 0.0002
    assert s.engine_deepseek_cny_per_1k_input == 0.002
    assert s.engine_deepseek_cny_per_1k_cache_hit == 0.00004
    assert s.engine_deepseek_cny_per_1k_output == 0.003


def test_direct_cny_provider_cost_settings_have_defaults(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_OMNIHUMAN_CNY_PER_SEC", raising=False)
    monkeypatch.delenv("ENGINE_SEEDTTS_CNY_PER_CHAR", raising=False)
    monkeypatch.delenv("ENGINE_COSYVOICE_TTS_CNY_PER_CHAR", raising=False)
    monkeypatch.delenv("ENGINE_DEEPSEEK_CNY_PER_1K_INPUT", raising=False)
    monkeypatch.delenv("ENGINE_DEEPSEEK_CNY_PER_1K_CACHE_HIT", raising=False)
    monkeypatch.delenv("ENGINE_DEEPSEEK_CNY_PER_1K_OUTPUT", raising=False)
    s = Settings(_env_file=None, jwt_secret_key=_JWT)

    assert s.engine_omnihuman_cny_per_sec == 1.0
    assert s.engine_seedtts_cny_per_char == 0.0003
    assert s.engine_cosyvoice_tts_cny_per_char == 0.00015
    assert s.engine_deepseek_cny_per_1k_input == 0.001008
    assert s.engine_deepseek_cny_per_1k_cache_hit == 0.00002016
    assert s.engine_deepseek_cny_per_1k_output == 0.002016


def test_tts_provider_cost_env_examples_keep_upstream_rates_distinct() -> None:
    repository_root = Path(__file__).parents[2]
    examples = (
        repository_root / "backend" / ".env.example",
        repository_root / "infra" / ".env.example",
        repository_root / "infra" / ".env.prod.example",
    )

    for example in examples:
        contents = example.read_text(encoding="utf-8")
        assert "ENGINE_SEEDTTS_CNY_PER_CHAR=0.0003" in contents
        assert "ENGINE_COSYVOICE_TTS_CNY_PER_CHAR=0.00015" in contents


def test_deepseek_cost_env_examples_include_cache_hit_rate() -> None:
    repository_root = Path(__file__).parents[2]
    examples = (
        repository_root / "backend" / ".env.example",
        repository_root / "infra" / ".env.example",
        repository_root / "infra" / ".env.prod.example",
    )

    for example in examples:
        contents = example.read_text(encoding="utf-8")
        assert "ENGINE_DEEPSEEK_CNY_PER_1K_INPUT=0.001008" in contents
        assert "ENGINE_DEEPSEEK_CNY_PER_1K_CACHE_HIT=0.00002016" in contents
        assert "ENGINE_DEEPSEEK_CNY_PER_1K_OUTPUT=0.002016" in contents
