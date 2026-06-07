# Copyright (C) 2026 Huading
#
# This package (the Huading video engine) is derived from Pixelle-Video
# (Copyright (C) 2025 AIDC-AI, licensed under Apache-2.0).
# See backend/app/engine/LICENSE and backend/app/engine/NOTICE.

"""
Huading video engine.

Distilled generation core from Pixelle-Video, with the Streamlit/Web layer and
single-machine YAML config removed. Configure via EngineConfig and build with
create_engine().

    from app.engine import EngineConfig, create_engine

    cfg = EngineConfig(llm_api_key="...", llm_base_url="...", llm_model="...")
    engine = await create_engine(cfg)
    result = await engine.generate_video("如何提高学习效率", n_scenes=3)
    await engine.cleanup()
"""

import os as _os
import sys as _sys

# The vendored ``pixelle_video`` package uses absolute imports
# (``from pixelle_video... import ...``). Expose this directory on sys.path so it
# resolves as a top-level package without rewriting every internal import.
_ENGINE_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _ENGINE_DIR not in _sys.path:
    _sys.path.insert(0, _ENGINE_DIR)

from .config import EngineConfig  # noqa: E402
from .factory import configure_runtime, create_engine  # noqa: E402

__all__ = ["EngineConfig", "create_engine", "configure_runtime"]
