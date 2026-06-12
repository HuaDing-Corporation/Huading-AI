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
EngineConfig - platform-injected runtime configuration.

Replaces Pixelle-Video's single-machine ``config.yaml`` with a "platform key
hosting + run parameters as inputs" interface. The platform builds an
``EngineConfig`` (filling in hosted API keys and run options) and passes it to
``create_engine()``; nothing is read from disk.

Multi-tenancy is intentionally out of scope here (M2): each ``EngineConfig`` is a
single build-time input. The interface is shaped so tenant-scoped key resolution
can be layered on later without changing call sites.
"""

from __future__ import annotations

from pixelle_video.config.schema import (
    AccessSecretProviderConfig,
    APIKeyProviderConfig,
    APIProviderCommonConfig,
    APIProvidersConfig,
    ComfyUIConfig,
    ImageSubConfig,
    LLMConfig,
    PixelleVideoConfig,
    TemplateConfig,
    TTSComfyUIConfig,
    TTSLocalConfig,
    TTSSubConfig,
    VideoSubConfig,
)
from pydantic import BaseModel, Field


class EngineConfig(BaseModel):
    """
    Single injection point for platform-hosted keys and run parameters.

    Only ``llm_*`` fields are required; everything else has sensible defaults so a
    minimal text->video run (static template, no ComfyUI) works out of the box.
    """

    project_name: str = Field(default="Huading-Video", description="Project name")

    # ---- LLM (required) ----
    llm_api_key: str = Field(..., description="LLM API key (hosted by platform)")
    llm_base_url: str = Field(..., description="OpenAI-compatible base URL")
    llm_model: str = Field(..., description="LLM model name")

    # ---- Direct API providers (optional, for image/video/VLM without ComfyUI) ----
    openai_api_key: str = Field(default="")
    openai_base_url: str = Field(default="https://api.openai.com/v1")

    dashscope_api_key: str = Field(default="")
    dashscope_base_url: str = Field(default="https://dashscope.aliyuncs.com/api/v1")

    deepseek_api_key: str = Field(default="")
    deepseek_base_url: str = Field(default="https://api.deepseek.com")

    gemini_api_key: str = Field(default="")
    gemini_base_url: str = Field(default="")

    ark_api_key: str = Field(default="")
    ark_base_url: str = Field(default="https://ark.cn-beijing.volces.com/api/v3")

    # Doubao-Seedance (Ark) text-to-video / image-to-video. Separate key so the
    # video provider can be configured independently of other Ark usage.
    seedance_api_key: str = Field(default="")
    seedance_base_url: str = Field(default="https://ark.cn-beijing.volces.com/api/v3")
    seedance_model: str = Field(default="doubao-seedance-2-0-260128")
    seedance_request_timeout_seconds: float = Field(default=120.0, gt=0)
    seedance_poll_interval_seconds: float = Field(default=5.0, gt=0)
    seedance_timeout_seconds: float = Field(default=600.0, gt=0)

    kling_access_key: str = Field(default="")
    kling_secret_key: str = Field(default="")
    kling_base_url: str = Field(default="https://api-beijing.klingai.com")

    provider_local_proxy: str = Field(
        default="", description="Optional local HTTP proxy for providers that need it"
    )

    # ---- ComfyUI / RunningHub (optional) ----
    comfyui_url: str = Field(default="http://127.0.0.1:8188")
    comfyui_api_key: str = Field(default="")
    runninghub_api_key: str = Field(default="")
    runninghub_concurrent_limit: int = Field(default=1, ge=1, le=10)
    runninghub_instance_type: str = Field(default="")

    # ---- Default media workflows (used by image/video templates) ----
    image_workflow: str | None = Field(default=None)
    video_workflow: str | None = Field(default=None)
    tts_workflow: str | None = Field(default=None)

    # ---- TTS (local edge-tts by default) ----
    tts_inference_mode: str = Field(default="local", description="'local' or 'comfyui'")
    tts_voice: str = Field(default="zh-CN-YunjianNeural")
    tts_speed: float = Field(default=1.2, ge=0.5, le=2.0)

    # ---- Template ----
    default_template: str = Field(
        default="1080x1920/static_default.html",
        description="Default frame template (static_* requires no ComfyUI)",
    )

    # ---- Runtime root (PIXELLE_VIDEO_ROOT) ----
    runtime_root: str | None = Field(
        default=None,
        description="Root dir holding templates/bgm/workflows/output. "
        "Defaults to backend/app/engine/runtime.",
    )

    # ---- Frame rendering browser ----
    browser_channel: str = Field(
        default="",
        description="Playwright browser channel for HTML frame rendering "
        "(e.g. 'chrome', 'msedge' to use a system-installed browser). "
        "Empty = Playwright's bundled Chromium (requires `playwright install chromium`).",
    )

    def to_pixelle_config(self) -> PixelleVideoConfig:
        """Map the platform-facing EngineConfig onto the internal PixelleVideoConfig."""
        return PixelleVideoConfig(
            project_name=self.project_name,
            llm=LLMConfig(
                api_key=self.llm_api_key,
                base_url=self.llm_base_url,
                model=self.llm_model,
            ),
            api_providers=APIProvidersConfig(
                common=APIProviderCommonConfig(
                    print_model_input=False,
                    local_proxy=self.provider_local_proxy,
                ),
                openai=APIKeyProviderConfig(
                    api_key=self.openai_api_key, base_url=self.openai_base_url
                ),
                dashscope=APIKeyProviderConfig(
                    api_key=self.dashscope_api_key, base_url=self.dashscope_base_url
                ),
                deepseek=APIKeyProviderConfig(
                    api_key=self.deepseek_api_key, base_url=self.deepseek_base_url
                ),
                gemini=APIKeyProviderConfig(
                    api_key=self.gemini_api_key, base_url=self.gemini_base_url
                ),
                ark=APIKeyProviderConfig(
                    api_key=self.ark_api_key, base_url=self.ark_base_url
                ),
                kling=AccessSecretProviderConfig(
                    base_url=self.kling_base_url,
                    access_key=self.kling_access_key,
                    secret_key=self.kling_secret_key,
                ),
            ),
            comfyui=ComfyUIConfig(
                comfyui_url=self.comfyui_url,
                comfyui_api_key=self.comfyui_api_key or None,
                runninghub_api_key=self.runninghub_api_key or None,
                runninghub_concurrent_limit=self.runninghub_concurrent_limit,
                runninghub_instance_type=self.runninghub_instance_type or None,
                tts=TTSSubConfig(
                    inference_mode=self.tts_inference_mode,
                    local=TTSLocalConfig(voice=self.tts_voice, speed=self.tts_speed),
                    comfyui=TTSComfyUIConfig(default_workflow=self.tts_workflow),
                ),
                image=ImageSubConfig(default_workflow=self.image_workflow),
                video=VideoSubConfig(default_workflow=self.video_workflow),
            ),
            template=TemplateConfig(default_template=self.default_template),
        )
