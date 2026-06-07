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
Engine factory.

Builds and initializes a ``PixelleVideoCore`` from a platform-injected
``EngineConfig``. This is the single entry point the backend should use; callers
never touch ``config.yaml`` or the global singleton directly.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

from .config import EngineConfig

# Default runtime root holding templates/ bgm/ workflows/ output/
_DEFAULT_RUNTIME_ROOT = Path(__file__).resolve().parent / "runtime"


def _resolve_runtime_root(cfg: EngineConfig) -> Path:
    root = Path(cfg.runtime_root).resolve() if cfg.runtime_root else _DEFAULT_RUNTIME_ROOT
    (root / "output").mkdir(parents=True, exist_ok=True)
    return root


def configure_runtime(cfg: EngineConfig) -> Path:
    """
    Apply an EngineConfig to the process: set PIXELLE_VIDEO_ROOT and inject config.

    Returns the resolved runtime root. Separated from create_engine() so callers
    that build the core themselves can still reuse the wiring.
    """
    runtime_root = _resolve_runtime_root(cfg)

    # os_util.get_pixelle_video_root_path() reads this env var to locate
    # templates/ bgm/ workflows/ output/.
    os.environ["PIXELLE_VIDEO_ROOT"] = str(runtime_root)

    # frame_html.py reads this to pick a system browser channel (e.g. "chrome")
    # instead of Playwright's bundled Chromium. Empty -> bundled Chromium.
    os.environ["HUADING_BROWSER_CHANNEL"] = cfg.browser_channel or ""

    # Inject config into the global singleton BEFORE PixelleVideoCore is created,
    # since PixelleVideoCore.__init__ reads config_manager.config.
    from pixelle_video.config import config_manager

    config_manager.configure_from_config(cfg.to_pixelle_config())
    logger.info(f"Engine runtime root: {runtime_root}")
    return runtime_root


async def create_engine(cfg: EngineConfig):
    """
    Build and initialize the video engine.

    Args:
        cfg: Platform-injected EngineConfig (hosted keys + run parameters)

    Returns:
        An initialized PixelleVideoCore. Call ``await core.cleanup()`` when done,
        or use it as an async context manager.

    Example:
        >>> cfg = EngineConfig(llm_api_key="...", llm_base_url="...", llm_model="...")
        >>> engine = await create_engine(cfg)
        >>> result = await engine.generate_video("如何提高学习效率", n_scenes=3)
        >>> await engine.cleanup()
    """
    configure_runtime(cfg)

    # Import after env + config are set so module-level singletons see the right state.
    from pixelle_video.service import PixelleVideoCore

    core = PixelleVideoCore()
    await core.initialize()
    return core
