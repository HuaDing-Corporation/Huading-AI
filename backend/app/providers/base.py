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
    "scene_prompt",
    "chat",
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


@dataclass(frozen=True)
class VideoProviderCapabilities:
    supported_sizes: frozenset[str]
    automatic_size: str


@runtime_checkable
class VideoProvider(Protocol):
    capabilities: VideoProviderCapabilities

    async def generate_video(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ImageProviderCapabilities:
    supported_resolutions: frozenset[str]
    max_reference_images: int


@runtime_checkable
class ImageProvider(Protocol):
    capabilities: ImageProviderCapabilities

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

    async def reverse_video_native(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    async def analyze_video_native_segment(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    async def summarize_video_segments(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class ScenePromptProvider(Protocol):
    async def generate_scene_prompt(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class ChatProvider(Protocol):
    async def chat(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


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
    | ScenePromptProvider
    | ChatProvider
)


@dataclass(frozen=True)
class ResolvedProvider:
    name: str
    provider: Provider


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


class ImageProviderCapabilitiesError(RuntimeError):
    code = "IMAGE_PROVIDER_CAPABILITIES_UNDECLARED"
    user_message = "当前图片服务能力配置不完整，暂时无法生成图片。"


class ImageProviderResolutionUnsupportedError(ImageProviderCapabilitiesError):
    code = "IMAGE_PROVIDER_RESOLUTION_UNSUPPORTED"

    def __init__(self, requested_resolution: str, supported_resolutions: frozenset[str]) -> None:
        ordered = [
            resolution
            for resolution in ("1k", "2k", "4k")
            if resolution in supported_resolutions
        ]
        ordered.extend(sorted(supported_resolutions.difference(ordered)))
        supported = "/".join(resolution.upper() for resolution in ordered)
        self.user_message = (
            f"当前图片服务不支持 {requested_resolution.upper()}，请选择 {supported}。"
        )
        super().__init__(self.user_message)


class ImageProviderReferenceImagesUnsupportedError(ImageProviderCapabilitiesError):
    code = "IMAGE_PROVIDER_REFERENCE_IMAGES_UNSUPPORTED"

    def __init__(self, max_reference_images: int) -> None:
        self.user_message = f"当前图片服务最多支持 {max_reference_images} 张参考图。"
        super().__init__(self.user_message)


class VideoProviderCapabilitiesError(RuntimeError):
    code = "VIDEO_PROVIDER_CAPABILITIES_UNDECLARED"


class VideoProviderSizeUnsupportedError(VideoProviderCapabilitiesError):
    code = "VIDEO_PROVIDER_SIZE_UNSUPPORTED"

    def __init__(self, requested_size: str, supported_sizes: frozenset[str]) -> None:
        self.requested_size = requested_size
        self.supported_sizes = supported_sizes
        super().__init__(
            f"Video provider does not support size {requested_size!r}; "
            f"supported sizes: {sorted(supported_sizes)}."
        )


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


def require_image_provider_capabilities(provider: object) -> ImageProviderCapabilities:
    capabilities = getattr(provider, "capabilities", None)
    if (
        not isinstance(capabilities, ImageProviderCapabilities)
        or not isinstance(capabilities.supported_resolutions, frozenset)
        or not capabilities.supported_resolutions
        or any(
            not isinstance(resolution, str)
            or not resolution.strip()
            or resolution != resolution.strip().lower()
            for resolution in capabilities.supported_resolutions
        )
        or isinstance(capabilities.max_reference_images, bool)
        or not isinstance(capabilities.max_reference_images, int)
        or capabilities.max_reference_images < 0
    ):
        raise ImageProviderCapabilitiesError(
            "Image provider must declare ImageProviderCapabilities."
        )
    return capabilities


def require_video_provider_capabilities(provider: object) -> VideoProviderCapabilities:
    capabilities = getattr(provider, "capabilities", None)
    if (
        not isinstance(capabilities, VideoProviderCapabilities)
        or not isinstance(capabilities.supported_sizes, frozenset)
        or not capabilities.supported_sizes
        or any(
            not isinstance(size, str)
            or not size.strip()
            or size != size.strip().lower()
            for size in capabilities.supported_sizes
        )
        or not isinstance(capabilities.automatic_size, str)
        or not capabilities.automatic_size.strip()
        or capabilities.automatic_size != capabilities.automatic_size.strip().lower()
        or capabilities.automatic_size not in capabilities.supported_sizes
    ):
        raise VideoProviderCapabilitiesError(
            "Video provider must declare VideoProviderCapabilities."
        )
    return capabilities


def video_provider_size(provider: object, requested_size: object) -> str:
    capabilities = require_video_provider_capabilities(provider)
    normalized_size = str(requested_size or "").strip().lower()
    provider_size = (
        capabilities.automatic_size if normalized_size == "auto" else normalized_size
    )
    if provider_size not in capabilities.supported_sizes:
        raise VideoProviderSizeUnsupportedError(
            provider_size,
            capabilities.supported_sizes,
        )
    return provider_size


def image_provider_reference_count(params: Mapping[str, Any]) -> int:
    source_storage_keys = params.get("source_storage_keys")
    if source_storage_keys is not None:
        if not isinstance(source_storage_keys, list | tuple):
            raise ValueError("source_storage_keys must be a list.")
        if source_storage_keys:
            return len(source_storage_keys)
    image_keys = params.get("image_keys")
    if image_keys:
        if not isinstance(image_keys, list | tuple):
            raise ValueError("image_keys must be a list.")
        return len(image_keys)
    if params.get("source_storage_key") or params.get("image_key"):
        return 1
    return 0


def validate_image_provider_request(
    provider: object,
    params: Mapping[str, Any],
) -> ImageProviderCapabilities:
    capabilities = require_image_provider_capabilities(provider)
    requested_resolution = str(params.get("image_resolution") or "1k").strip().lower()
    if requested_resolution not in capabilities.supported_resolutions:
        raise ImageProviderResolutionUnsupportedError(
            requested_resolution,
            capabilities.supported_resolutions,
        )
    reference_image_count = image_provider_reference_count(params)
    if reference_image_count > capabilities.max_reference_images:
        raise ImageProviderReferenceImagesUnsupportedError(
            capabilities.max_reference_images
        )
    return capabilities


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


def _resolved_provider(
    config: ProviderConfig,
    *,
    capability: Capability | str,
) -> ResolvedProvider:
    factory = _REGISTRY.get((capability, config.provider))
    if factory is None:
        raise ProviderResolutionError(
            f"Provider {config.provider!r} is not registered for {capability!r}."
        )
    return ResolvedProvider(name=config.provider, provider=factory(config))


def resolve_with_name(
    db: Session,
    *,
    tenant_id: str,
    capability: Capability | str,
) -> ResolvedProvider:
    config = _provider_config(db, tenant_id=tenant_id, capability=capability)
    return _resolved_provider(config, capability=capability)


def resolve(db: Session, *, tenant_id: str, capability: Capability | str) -> Provider:
    return resolve_with_name(db, tenant_id=tenant_id, capability=capability).provider


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
    return _resolved_provider(config, capability=capability).provider


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
