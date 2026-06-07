# Copyright (C) 2026 Huading
#
# Acceptance script for task #002 (engine distillation).
# Derived-work usage of Pixelle-Video (AIDC-AI, Apache-2.0).

"""
Acceptance script: run the standard pipeline WITHOUT Streamlit.

Verifies the distilled engine can produce a video from a topic using only the
injected EngineConfig. Uses a *static* template so no ComfyUI is required — the
chain is LLM (script) + edge-tts (local voice) + Playwright (frame render) +
ffmpeg (concat).

Keys are read from environment variables (platform key-hosting style), e.g.:

    set HUADING_LLM_API_KEY=sk-...
    set HUADING_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    set HUADING_LLM_MODEL=qwen-max
    python scripts/run_standard_pipeline.py

Prerequisites: ffmpeg on PATH, and `playwright install chromium` already run.
"""

import asyncio
import os
import sys
from pathlib import Path

# Make `app` importable when run as a plain script from backend/.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.engine import EngineConfig, create_engine  # noqa: E402


async def main() -> int:
    api_key = os.environ.get("HUADING_LLM_API_KEY")
    base_url = os.environ.get("HUADING_LLM_BASE_URL")
    model = os.environ.get("HUADING_LLM_MODEL")

    if not (api_key and base_url and model):
        print(
            "ERROR: set HUADING_LLM_API_KEY / HUADING_LLM_BASE_URL / HUADING_LLM_MODEL",
            file=sys.stderr,
        )
        return 2

    cfg = EngineConfig(
        llm_api_key=api_key,
        llm_base_url=base_url,
        llm_model=model,
        default_template="1080x1920/static_default.html",
        # Use a system-installed browser by default so frame rendering works
        # without downloading Playwright's bundled Chromium. Override with
        # HUADING_BROWSER_CHANNEL="" to force the bundled Chromium instead.
        browser_channel=os.environ.get("HUADING_BROWSER_CHANNEL", "chrome"),
    )

    engine = await create_engine(cfg)
    try:
        result = await engine.generate_video(
            text="如何提高学习效率",
            pipeline="standard",
            mode="generate",
            n_scenes=3,
            frame_template="1080x1920/static_default.html",
        )
        video_path = Path(result.video_path)
        assert video_path.exists(), f"output missing: {video_path}"
        assert video_path.stat().st_size > 0, "output is empty"
        print(f"OK: generated {video_path} ({result.file_size} bytes, {result.duration:.1f}s)")
        return 0
    finally:
        await engine.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
