# Copyright (C) 2026 Huading
#
# This file is part of the Huading video engine, which is derived from
# Pixelle-Video (Copyright (C) 2025 AIDC-AI, licensed under Apache-2.0).
# See backend/app/engine/LICENSE and backend/app/engine/NOTICE.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
Direct-API video providers — unified entry point.

Phase-2 pipelines ("topic -> text-to-video" and "upload product image ->
image-to-video") call ``generate_seedance_video(cfg, ...)`` so they never touch
provider clients or env directly; keys come from the platform-injected
``EngineConfig`` (no hardcoded keys).
"""

from __future__ import annotations

from typing import Any

from pixelle_video.services.api_services.video_seedance import (
    SeedanceResult,
    SeedanceVideoClient,
)

from app.engine.config import EngineConfig

__all__ = ["create_seedance_client", "generate_seedance_video", "SeedanceResult"]


def create_seedance_client(cfg: EngineConfig) -> SeedanceVideoClient:
    """Build a Seedance (Ark) client from EngineConfig (hosted keys)."""
    return SeedanceVideoClient(
        api_key=cfg.seedance_api_key or None,
        base_url=cfg.seedance_base_url or None,
        model=cfg.seedance_model or None,
        local_proxy=cfg.provider_local_proxy or None,
    )


def generate_seedance_video(
    cfg: EngineConfig,
    prompt: str = "",
    *,
    image: str | None = None,
    image_path: str | None = None,
    save_path: str | None = None,
    **params: Any,
) -> SeedanceResult:
    """Generate a video via Seedance.

    Text only -> text-to-video; with ``image`` (URL/data URI) or ``image_path``
    (local file) -> image-to-video. Extra ``params`` (duration, resolution,
    ratio, seed, watermark, generate_audio, image_role, ...) pass through to the
    client. Returns task id + remote video URL (downloaded to ``save_path`` if set).
    """
    client = create_seedance_client(cfg)
    return client.generate_video(
        prompt, image=image, image_path=image_path, save_path=save_path, **params
    )
