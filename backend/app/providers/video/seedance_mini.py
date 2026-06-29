from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import register_provider

_PROVIDER_NAME = "seedance-mini"


class SeedanceMiniProviderPendingError(RuntimeError):
    pass


class SeedanceMiniVideoProvider:
    def __init__(self, *, model: str) -> None:
        self.model = model

    async def generate_video(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise SeedanceMiniProviderPendingError(
            "Seedance 2.0 mini Ark i2v API/model id is pending public integration."
        )


def _seedance_mini_factory(config: ProviderConfig) -> SeedanceMiniVideoProvider:
    values = config.config or {}
    return SeedanceMiniVideoProvider(
        model=str(values.get("model") or settings.engine_seedance_mini_model),
    )


register_provider("video", _PROVIDER_NAME, _seedance_mini_factory)
