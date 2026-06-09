from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Huading API"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    environment: str = "local"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    database_url: str = "postgresql+psycopg://huading:huading@localhost:5432/huading"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_task_always_eager: bool = False

    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Readiness probe timeouts so an unreachable dependency degrades fast
    # instead of hanging the /ready handler (#003-FIX P2).
    db_connect_timeout: int = 3  # seconds (psycopg connect_timeout)
    redis_socket_connect_timeout: float = 2.0  # seconds
    redis_socket_timeout: float = 2.0  # seconds

    # SSE progress stream cap (seconds) before emitting an sse_timeout event.
    sse_timeout_seconds: int = 600

    storage_backend: str = "local"
    storage_local_root: str = ".local-storage"
    storage_bucket: str = "huading-dev"
    storage_endpoint_url: str | None = None
    storage_region: str = "us-east-1"
    storage_access_key_id: str | None = None
    storage_secret_access_key: str | None = None

    # ---- Video engine ----
    # Keys are injected from the platform/environment, never hardcoded (#002-FIX-1).
    # Multi-tenancy is out of scope (M2); the engine config is a single process-wide
    # singleton, so run the worker single-config / single-process (see backend README).
    engine_llm_api_key: str = ""
    engine_llm_base_url: str = ""
    engine_llm_model: str = ""
    engine_dashscope_api_key: str = ""
    engine_default_template: str = "1080x1920/static_default.html"
    # Playwright browser channel for frame rendering: "chrome"/"msedge" to use a
    # system browser, or "" for Playwright's bundled Chromium.
    engine_browser_channel: str = ""
    # Resource root (templates/bgm/workflows/output). None -> app/engine/runtime.
    engine_runtime_root: str | None = None
    # Object-storage key prefix for generated videos (task-isolated under it).
    engine_output_prefix: str = "videos"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
