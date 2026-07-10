from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import ProviderConfig, UsageRecord

Capability = Literal[
    "llm",
    "tts",
    "avatar",
    "video",
    "image",
    "asr",
    "publish",
    "voice_clone",
    "reverse_prompt",
]
T = TypeVar("T")


@runtime_checkable
class LLMProvider(Protocol):
    async def generate_text(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class TTSProvider(Protocol):
    async def synthesize_speech(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class AvatarProvider(Protocol):
    async def generate_avatar(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class VideoProvider(Protocol):
    async def generate_video(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class ImageProvider(Protocol):
    async def generate_image(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class ASRProvider(Protocol):
    async def transcribe(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class PublishProvider(Protocol):
    async def publish(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class VoiceCloneProvider(Protocol):
    async def clone_voice(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def delete_voice(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class ReversePromptProvider(Protocol):
    async def reverse_image(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def reverse_video_frames(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


Provider = (
    LLMProvider
    | TTSProvider
    | AvatarProvider
    | VideoProvider
    | ImageProvider
    | ASRProvider
    | PublishProvider
    | VoiceCloneProvider
    | ReversePromptProvider
)
ProviderFactory = Callable[[ProviderConfig], Provider]
Operation = Callable[[], T | Awaitable[T]]
_REGISTRY: dict[tuple[str, str], ProviderFactory] = {}
_DEFAULT_PROVIDERS: dict[str, str] = {
    "voice_clone": "doubao-voice-clone",
}


class ProviderResolutionError(RuntimeError):
    pass


class ProviderInvocationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderUsage:
    unit: str = "call"
    quantity: Decimal | int | float | str = Decimal("1")
    credits: Decimal | int | float | str = Decimal("0")
    cost_cents: int = 0
    model: str | None = None
    video_task_id: str | None = None
    currency: str = "CNY"


def register_provider(
    capability: Capability | str,
    provider: str,
    factory: ProviderFactory,
) -> None:
    _REGISTRY[(capability, provider)] = factory


def clear_provider_registry() -> None:
    _REGISTRY.clear()


def _provider_config(
    db: Session,
    *,
    tenant_id: str,
    capability: Capability | str,
) -> ProviderConfig:
    default_provider = _DEFAULT_PROVIDERS.get(str(capability))
    tenant_config = db.scalar(
        _provider_config_query(
            tenant_id=tenant_id,
            capability=capability,
            provider=default_provider,
        )
    )
    if tenant_config is not None:
        return tenant_config
    if default_provider is not None:
        tenant_config = db.scalar(
            _provider_config_query(
                tenant_id=tenant_id,
                capability=capability,
            )
        )
        if tenant_config is not None:
            return tenant_config

    platform_config = db.scalar(
        _provider_config_query(
            tenant_id=None,
            capability=capability,
            provider=default_provider,
        )
    )
    if platform_config is None and default_provider is not None:
        platform_config = db.scalar(
            _provider_config_query(
                tenant_id=None,
                capability=capability,
            )
        )
    if platform_config is None:
        raise ProviderResolutionError(f"No provider configured for {capability}.")
    if _should_promote_doubao_tts(capability, platform_config):
        return ProviderConfig(
            tenant_id=None,
            capability="tts",
            provider="doubao-seed-tts",
            config={},
            is_active=True,
        )
    return platform_config


def _provider_config_query(
    *,
    tenant_id: str | None,
    capability: Capability | str,
    provider: str | None = None,
):
    query = select(ProviderConfig).where(
        ProviderConfig.capability == capability,
        ProviderConfig.is_active.is_(True),
    )
    if tenant_id is None:
        query = query.where(ProviderConfig.tenant_id.is_(None))
    else:
        query = query.where(ProviderConfig.tenant_id == tenant_id)
    if provider is not None:
        query = query.where(ProviderConfig.provider == provider)
    return query.order_by(ProviderConfig.provider.asc())


def _provider_config_by_name(
    db: Session,
    *,
    tenant_id: str,
    capability: Capability | str,
    provider: str,
) -> ProviderConfig:
    tenant_config = db.scalar(
        _provider_config_query(
            tenant_id=tenant_id,
            capability=capability,
            provider=provider,
        )
    )
    if tenant_config is not None:
        return tenant_config
    platform_config = db.scalar(
        _provider_config_query(
            tenant_id=None,
            capability=capability,
            provider=provider,
        )
    )
    if platform_config is None:
        raise ProviderResolutionError(
            f"No provider {provider!r} configured for {capability}."
        )
    return platform_config


def _should_promote_doubao_tts(
    capability: Capability | str,
    platform_config: ProviderConfig,
) -> bool:
    return (
        capability == "tts"
        and platform_config.provider == "edge-tts"
        and (
            bool(settings.engine_doubao_tts_api_key)
            or (
                bool(settings.engine_doubao_tts_appid)
                and bool(settings.engine_doubao_tts_access_token)
            )
        )
    )


def resolve(db: Session, *, tenant_id: str, capability: Capability | str) -> Provider:
    config = _provider_config(db, tenant_id=tenant_id, capability=capability)
    factory = _REGISTRY.get((capability, config.provider))
    if factory is None:
        raise ProviderResolutionError(
            f"Provider {config.provider!r} is not registered for {capability!r}."
        )
    return factory(config)


def resolve_named_provider(
    db: Session,
    *,
    tenant_id: str,
    capability: Capability | str,
    provider: str,
) -> Provider:
    config = _provider_config_by_name(
        db,
        tenant_id=tenant_id,
        capability=capability,
        provider=provider,
    )
    factory = _REGISTRY.get((capability, config.provider))
    if factory is None:
        raise ProviderResolutionError(
            f"Provider {config.provider!r} is not registered for {capability!r}."
        )
    return factory(config)


async def _await_operation(operation: Operation[T], timeout_seconds: float | None) -> T:
    value = operation()
    if inspect.isawaitable(value):
        if timeout_seconds is None:
            return await value
        return await asyncio.wait_for(value, timeout=timeout_seconds)
    return value


async def invoke(
    db: Session,
    *,
    tenant_id: str,
    capability: Capability | str,
    provider: str,
    operation: Operation[T],
    usage: ProviderUsage | None = None,
    timeout_seconds: float | None = 30.0,
    retries: int = 0,
) -> T:
    last_error: Exception | None = None
    for _attempt in range(retries + 1):
        try:
            result = await _await_operation(operation, timeout_seconds)
        except Exception as exc:
            last_error = exc
            continue

        if usage is not None:
            db.add(
                UsageRecord(
                    tenant_id=tenant_id,
                    video_task_id=usage.video_task_id,
                    capability=capability,
                    provider=provider,
                    model=usage.model,
                    unit=usage.unit,
                    quantity=Decimal(str(usage.quantity)),
                    credits=Decimal(str(usage.credits)),
                    cost_cents=usage.cost_cents,
                    currency=usage.currency,
                    status="settled",
                    settled_at=datetime.now(UTC),
                )
            )
        return result

    raise ProviderInvocationError(f"Provider invocation failed for {capability}.") from last_error
