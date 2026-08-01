import json
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.utils import normalize_tenant_slug


def _split_list_setting(value: str | list[str] | set[str]) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in text.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


class Settings(BaseSettings):
    app_name: str = "Huading API"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    environment: str = "local"
    log_level: str = "INFO"
    # NoDecode: stop pydantic-settings from JSON-decoding the env value before
    # validation, so a plain comma-separated string reaches the validator below
    # instead of raising SettingsError.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    engine_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    engine_platform_tenant_slugs: Annotated[set[str], NoDecode] = Field(
        default_factory=set
    )

    database_url: str = "postgresql+psycopg://huading:huading@localhost:5432/huading"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_task_always_eager: bool = False
    engine_admin_retry_stale_seconds: int = Field(default=60, ge=0)
    engine_orphan_task_stale_seconds: float = Field(default=1800.0, gt=0)
    engine_orphan_recovery_interval_seconds: float = Field(default=60.0, ge=0)
    engine_gen_heartbeat_interval_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=300.0,
        allow_inf_nan=False,
    )

    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # Readiness probe timeouts so an unreachable dependency degrades fast
    # instead of hanging the /ready handler (#003-FIX P2).
    db_connect_timeout: int = 3  # seconds (psycopg connect_timeout)
    redis_socket_connect_timeout: float = 2.0  # seconds
    redis_socket_timeout: float = 2.0  # seconds

    # SSE progress stream cap (seconds) before emitting an sse_timeout event.
    sse_timeout_seconds: int = 1500
    upload_max_bytes: int = 10 * 1024 * 1024
    upload_video_max_bytes: int = Field(default=200 * 1024 * 1024, gt=0)

    storage_backend: str = "local"
    storage_local_root: str = ".local-storage"
    storage_bucket: str = "huading-dev"
    storage_endpoint_url: str | None = None
    storage_region: str = "us-east-1"
    storage_access_key_id: str | None = None
    storage_secret_access_key: str | None = None

    engine_bgm_seed_on_startup: bool = True
    engine_s3_endpoint: str | None = None
    engine_s3_public_endpoint: str | None = None
    engine_s3_access_key: str | None = None
    engine_s3_secret_key: str | None = None
    engine_s3_bucket: str = "huading-videos"
    engine_s3_region: str = "us-east-1"
    engine_s3_secure: bool = False
    engine_s3_addressing_style: str = "path"
    engine_s3_presign_ttl: int = 3600
    engine_aigc_producer: str = "Huading"
    engine_label_provider_code: str = ""

    # ---- Video engine ----
    # Keys are injected from the platform/environment, never hardcoded (#002-FIX-1).
    # Multi-tenancy is out of scope (M2); the engine config is a single process-wide
    # singleton, so run the worker single-config / single-process (see backend README).
    engine_llm_api_key: str = ""
    engine_llm_base_url: str = ""
    engine_llm_model: str = ""
    engine_dashscope_api_key: str = ""
    # Doubao-Seedance (Volcengine Ark) for seedance_t2v / seedance_i2v modes.
    engine_seedance_api_key: str = ""
    engine_seedance_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    engine_seedance_model: str = "doubao-seedance-2-0-260128"
    # Seedance 2.0 mini public API/model id was not confirmed in official Ark docs
    # at implementation time. Keep the model configurable and use mock tests until
    # Ark exposes the production id.
    engine_seedance_mini_model: str = "doubao-seedance-2-0-mini-pending"
    engine_seedance_request_timeout_seconds: float = 120.0
    engine_seedance_poll_interval_seconds: float = 5.0
    engine_seedance_timeout_seconds: float = 1500.0
    # Volcengine Jimeng OmniHuman (CV API). Secrets are env-only and never
    # hardcoded; req_key is a public model identifier in the provider adapter.
    engine_omnihuman_access_key: str = ""
    engine_omnihuman_secret_key: str = ""
    engine_omnihuman_region: str = "cn-north-1"
    engine_omnihuman_request_timeout_seconds: float = 120.0
    engine_omnihuman_poll_interval_seconds: float = 5.0
    engine_omnihuman_timeout_seconds: float = 1500.0
    engine_omnihuman_result_host_suffixes: str = "aigc-cloud.com"
    engine_omnihuman_cny_per_sec: float = 1.0
    engine_omnihuman_change_lips_lite_req_key: str = "realman_change_lips"
    engine_omnihuman_change_lips_basic_req_key: str = "realman_change_lips_basic_chimera"
    engine_omnihuman_change_lips_region: str = "cn-beijing"
    engine_omnihuman_change_lips_lite_cny_per_sec: float = 0.3
    engine_omnihuman_change_lips_basic_cny_per_sec: float = 1.05
    engine_omnihuman_change_lips_default_tier: str = "basic"
    engine_omnihuman_change_lips_open_sr: bool = True
    engine_omnihuman_change_lips_basic_retry_on_short_output: bool = False
    # Volcengine Doubao Seed-TTS. Credentials are env-only; when absent the
    # provider resolver keeps using edge-tts so CI/dev stays self-contained.
    engine_doubao_tts_appid: str = ""
    engine_doubao_tts_access_token: str = ""
    engine_doubao_tts_api_key: str = ""
    engine_doubao_tts_resource_id: str = "seed-tts-2.0"
    engine_doubao_tts_default_voice: str = "zh_male_m191_uranus_bigtts"
    engine_doubao_tts_endpoint: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
    engine_doubao_tts_request_timeout_seconds: float = 60.0
    engine_doubao_tts_aigc_watermark: bool = False
    engine_seedtts_cny_per_char: float = 0.0003
    # Volcengine Doubao voice clone. These default to the same Seed-TTS account
    # values when clone-specific env vars are absent.
    engine_doubao_voice_clone_appid: str = ""
    engine_doubao_voice_clone_access_token: str = ""
    engine_doubao_voice_clone_api_key: str = ""
    engine_doubao_voice_clone_speaker_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
    engine_doubao_voice_clone_resource_id: str = "volc.megatts.voiceclone"
    engine_doubao_voice_clone_endpoint: str = (
        "https://openspeech.bytedance.com/api/v1/mega_tts/audio/upload"
    )
    engine_doubao_voice_clone_status_endpoint: str = (
        "https://openspeech.bytedance.com/api/v1/mega_tts/status"
    )
    engine_doubao_voice_clone_request_timeout_seconds: float = 60.0
    engine_doubao_voice_clone_poll_interval_seconds: float = 2.0
    engine_doubao_voice_clone_timeout_seconds: float = 60.0
    engine_doubao_voice_clone_model_type: int = 4
    engine_doubao_voice_clone_tts_resource_id: str = "seed-icl-2.0"
    # Alibaba Cloud DashScope CosyVoice clone path. API keys stay env-only.
    engine_cosyvoice_voice_clone_api_key: str = ""
    engine_cosyvoice_voice_clone_target_model: str = "cosyvoice-v3.5-plus"
    engine_cosyvoice_voice_clone_base_url: str = ""
    engine_cosyvoice_voice_clone_request_timeout_seconds: float = 60.0
    engine_cosyvoice_tts_cny_per_char: float = 0.00015
    # OpenAI Images for the photo pipeline. Credentials stay env-only.
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_image_model: str = "gpt-image-2"
    openai_local_proxy: str = ""
    openai_image_timeout: float = 1500.0
    # APIMart Images. APIMart exposes GPT image generation as async tasks and
    # requires public image URLs for edit-style requests.
    engine_apimart_api_key: str = ""
    engine_apimart_base_url: str = "https://api.apimart.ai/v1"
    engine_apimart_image_model: str = "gpt-image-2"
    engine_apimart_video_model: str = "doubao-seedance-2.0"
    engine_apimart_request_timeout_seconds: float = 120.0
    engine_apimart_poll_initial_delay_seconds: float = 10.0
    engine_apimart_poll_interval_seconds: float = 4.0
    engine_apimart_timeout_seconds: float = 1500.0
    engine_apimart_video_poll_initial_delay_seconds: float = 30.0
    engine_apimart_video_poll_interval_seconds: float = 10.0
    engine_apimart_video_timeout_seconds: float = 1500.0
    # APIMart denomination and paid CNY conversion, verified from recharge
    # invoices on 2026-07-30. These are not official model list prices.
    engine_apimart_credit_usd: float = 0.10
    engine_apimart_reverse_prompt_model: str = "gemini-3.1-pro-preview"
    engine_apimart_reverse_prompt_video_model: str = "gemini-3.6-flash"
    engine_reverse_prompt_video_analysis_mode: Literal["native", "frames"] = "native"
    engine_reverse_prompt_video_native_segment_seconds: int = Field(
        default=60,
        ge=15,
        le=60,
    )
    engine_reverse_prompt_video_proxy_max_edge: int = Field(
        default=640,
        ge=240,
        le=1280,
    )
    engine_apimart_scene_prompt_model: str = "gpt-5.6-luna"
    # Compatibility-only APIMart discounted USD rates from its pricing center,
    # verified 2026-07-30. Central model/tier pricing is authoritative.
    engine_apimart_scene_prompt_input_usd_per_m: float = 0.8
    engine_apimart_scene_prompt_output_usd_per_m: float = 4.8
    # AIBRAIN user rates are product charges per 1K tokens, not upstream prices.
    engine_aibrain_low_input_credits_per_1k: Decimal = Decimal("1.73")
    engine_aibrain_low_output_credits_per_1k: Decimal = Decimal("10.37")
    engine_aibrain_mid_input_credits_per_1k: Decimal = Decimal("4.32")
    engine_aibrain_mid_output_credits_per_1k: Decimal = Decimal("25.92")
    engine_aibrain_high_input_credits_per_1k: Decimal = Decimal("8.64")
    engine_aibrain_high_output_credits_per_1k: Decimal = Decimal("51.84")
    # Compatibility snapshots of APIMart discounted low-context provider rates.
    # Provider cost accounting uses apimart_token_pricing.py instead.
    engine_aibrain_low_input_provider_credits_per_m: Decimal = Decimal("8")
    engine_aibrain_low_output_provider_credits_per_m: Decimal = Decimal("48")
    engine_aibrain_mid_input_provider_credits_per_m: Decimal = Decimal("20")
    engine_aibrain_mid_output_provider_credits_per_m: Decimal = Decimal("120")
    engine_aibrain_high_input_provider_credits_per_m: Decimal = Decimal("40")
    engine_aibrain_high_output_provider_credits_per_m: Decimal = Decimal("240")
    engine_aibrain_max_completion_tokens: int = Field(default=4096, ge=1, le=16384)
    engine_aibrain_reservation_stale_minutes: int = Field(default=30, ge=1)
    # Compatibility-only snapshots accepted from older deployments. Production
    # costing and observability must use app.services.apimart_token_pricing.
    engine_apimart_reverse_prompt_input_credits_per_m: float = 16.0
    engine_apimart_reverse_prompt_output_credits_per_m: float = 96.0
    engine_reverse_prompt_video_credits: float = Field(default=100.0, gt=0)
    engine_reverse_prompt_video_long_credits: float = Field(default=250.0, gt=0)
    engine_ecom_replicate_enabled: bool = True
    engine_ecom_replicate_credits_per_image: float = 15.0
    # Legacy operational fallbacks, not official list prices. Provider-reported
    # credits/cost always take precedence in e-commerce replicate accounting.
    engine_ecom_replicate_analysis_cny_per_call: float = 0.14
    engine_ecom_replicate_render_cny_per_image: float = 0.0432
    engine_ecom_replicate_main_size: str = "1024x1024"
    engine_ecom_replicate_detail_size: str = "768x1024"
    engine_ecom_replicate_detail_fallback_size: str = "1024x1536"
    engine_ecom_replicate_quality: str = "high"
    engine_ecom_replicate_max_retry: int = 2
    engine_ecom_replicate_analysis_concurrency: int = Field(default=4, ge=1)
    engine_usd_cny_rate: float = 7.0
    engine_deepseek_cny_per_1k_input: float = 0.001008
    engine_deepseek_cny_per_1k_cache_hit: float = 0.00002016
    engine_deepseek_cny_per_1k_output: float = 0.002016
    engine_image_provider_timeout_seconds: float = 1500.0
    engine_subtitle_font_path: str = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    engine_default_template: str = "1080x1920/static_default.html"
    # Playwright browser channel for frame rendering: "chrome"/"msedge" to use a
    # system browser, or "" for Playwright's bundled Chromium.
    engine_browser_channel: str = ""
    # Resource root (templates/bgm/workflows/output). None -> app/engine/runtime.
    engine_runtime_root: str | None = None
    # Object-storage key prefix for generated videos (isolated per task under it).
    engine_output_prefix: str = "videos"
    # JSON override for public publish platform catalog. Public URLs only; never
    # store social credentials here.
    publish_platforms_json: str = ""

    @property
    def effective_cors_origins(self) -> list[str]:
        return self.engine_cors_origins or self.cors_origins

    @field_validator(
        "cors_origins",
        "engine_cors_origins",
        "engine_doubao_voice_clone_speaker_ids",
        mode="before",
    )
    @classmethod
    def split_list_setting(cls, value: str | list[str]) -> list[str]:
        # Accept a JSON array, a comma-separated string, or a single URL.
        return _split_list_setting(value)

    @field_validator("engine_platform_tenant_slugs", mode="before")
    @classmethod
    def normalize_platform_tenant_slugs(
        cls,
        value: str | list[str] | set[str],
    ) -> set[str]:
        return {
            normalized
            for item in _split_list_setting(value)
            if (normalized := normalize_tenant_slug(item))
        }

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
