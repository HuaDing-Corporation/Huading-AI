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
Seedance video pipelines (Phase 2).

Two flows built on the unified Seedance entry point:

- text-to-video:  topic/script -> LLM writes scenes -> Seedance t2v clip per
  scene -> edge-tts voiceover -> ffmpeg mux + concat (+BGM).
- image-to-video: product image (+ script/topic) -> same flow, but every clip is
  Seedance i2v conditioned on the product image.

Keys come from the platform-injected EngineConfig (SEEDANCE_*/LLM via env at the
task layer; never hardcoded). Progress is reported through the same callback
shape the static-template pipeline uses (event_type/progress/frame_current/
frame_total), so the existing Redis/SSE plumbing works unchanged.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from app.engine.config import EngineConfig
from app.engine.video import generate_seedance_video

ProgressCallback = Callable[[Any], None] | None


@dataclass
class ProgressEvent:
    """Mirrors the attributes the worker's progress_cb reads off engine events."""

    event_type: str
    progress: float
    frame_current: int | None = None
    frame_total: int | None = None


@dataclass
class Scene:
    narration: str
    video_prompt: str


_SCENE_PLAN_PROMPT = """你是短视频编导。基于下面的主题/脚本，规划 {n_scenes} 个分镜场景。
每个场景给出：
- narration: 一句口播解说词（25~45 个字，口语化，可直接配音）
- video_prompt: 该场景的视频画面提示词（中文，具体的镜头/光线/动作描述，适合 AI 视频生成）

只输出 JSON 数组，格式：[{{"narration": "...", "video_prompt": "..."}}, ...]，不要其它文字。

主题/脚本：
{topic}
"""


async def _plan_scenes(cfg: EngineConfig, topic: str, n_scenes: int) -> list[Scene]:
    """LLM (OpenAI-compatible, e.g. DeepSeek) writes the narration + visual plan."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url)
    response = await client.chat.completions.create(
        model=cfg.llm_model,
        messages=[
            {"role": "user", "content": _SCENE_PLAN_PROMPT.format(n_scenes=n_scenes, topic=topic)}
        ],
        temperature=0.7,
        max_tokens=2000,
    )
    content = response.choices[0].message.content or ""

    data = _parse_json_array(content)
    scenes = [
        Scene(narration=str(item.get("narration", "")).strip(),
              video_prompt=str(item.get("video_prompt", "")).strip())
        for item in data
        if item.get("narration") and item.get("video_prompt")
    ]
    if not scenes:
        raise RuntimeError(f"LLM scene planning returned no usable scenes: {content[:200]}")
    return scenes[:n_scenes]


def _parse_json_array(content: str) -> list[dict]:
    """Parse a JSON array out of an LLM reply (tolerates code fences/prose)."""
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", content)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    start, end = content.find("["), content.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(content[start : end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    raise RuntimeError(f"could not parse scene JSON from LLM reply: {content[:200]}")


async def _tts(narration: str, voice: str, speed: float, out_path: str) -> str:
    """Local edge-tts voiceover (same engine default voice settings)."""
    import edge_tts

    pct = int(round((speed - 1.0) * 100))
    rate = f"{'+' if pct >= 0 else ''}{pct}%"
    await edge_tts.Communicate(narration, voice, rate=rate).save(out_path)
    return out_path


def _probe_duration(path: str) -> float:
    import ffmpeg

    info = ffmpeg.probe(path)
    return float(info["format"]["duration"])


def _mux_scene(video_path: str, audio_path: str, out_path: str) -> str:
    """Combine a Seedance clip with its narration; hold the last frame if the
    narration runs longer than the clip."""
    import ffmpeg

    video_dur = _probe_duration(video_path)
    audio_dur = _probe_duration(audio_path)

    video_in = ffmpeg.input(video_path)
    audio_in = ffmpeg.input(audio_path)
    video_stream = video_in.video
    if audio_dur > video_dur:
        video_stream = video_stream.filter(
            "tpad", stop_mode="clone", stop_duration=audio_dur - video_dur
        )
    (
        ffmpeg.output(
            video_stream,
            audio_in.audio,
            out_path,
            vcodec="libx264",
            acodec="aac",
            pix_fmt="yuv420p",
            shortest=None,
        )
        .overwrite_output()
        .run(quiet=True)
    )
    return out_path


def _concat_with_bgm(
    segments: list[str], output: str, bgm_path: str | None, bgm_volume: float
) -> str:
    """Concat segments (+optional BGM) via the engine's VideoService."""
    from pixelle_video.services.video import VideoService

    return VideoService().concat_videos(
        videos=segments, output=output, bgm_path=bgm_path, bgm_volume=bgm_volume
    )


def _report(cb: ProgressCallback, event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        cb(event)
    except Exception:  # noqa: BLE001 — progress must never break generation
        logger.warning("seedance pipeline: progress callback failed")


async def run_seedance_pipeline(
    cfg: EngineConfig,
    topic: str,
    *,
    image: str | None = None,
    image_path: str | None = None,
    image_role: str = "first_frame",
    n_scenes: int = 2,
    clip_duration: int = 5,
    resolution: str = "720p",
    ratio: str = "9:16",
    voice: str | None = None,
    tts_speed: float | None = None,
    bgm_path: str | None = None,
    bgm_volume: float = 0.2,
    work_dir: str | None = None,
    progress_callback: ProgressCallback = None,
) -> dict[str, Any]:
    """Run the Seedance pipeline end to end.

    Without an image -> text-to-video; with ``image`` (URL/data URI) or
    ``image_path`` (local file, e.g. an uploaded product shot) -> image-to-video
    conditioned on that image for every scene.

    Returns ``{"video_path", "duration", "file_size"}`` — the same shape the
    static-template engine path returns, so the task layer treats both alike.
    """
    if not topic.strip():
        raise ValueError("topic/script must not be empty")

    workspace = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="seedance-"))
    workspace.mkdir(parents=True, exist_ok=True)
    voice = voice or cfg.tts_voice
    speed = tts_speed if tts_speed is not None else cfg.tts_speed
    mode = "i2v" if (image or image_path) else "t2v"

    _report(progress_callback, ProgressEvent("planning_scenes", 0.05))
    scenes = await _plan_scenes(cfg, topic, n_scenes)
    total = len(scenes)
    logger.info(f"seedance pipeline: mode={mode} scenes={total}")

    segments: list[str] = []
    per_scene = 0.8 / total  # 0.05..0.85 across scenes
    for index, scene in enumerate(scenes):
        base = 0.05 + per_scene * index
        _report(
            progress_callback,
            ProgressEvent("generating_clip", base, frame_current=index + 1, frame_total=total),
        )
        clip = str(workspace / f"clip_{index:02d}.mp4")
        await asyncio.to_thread(
            generate_seedance_video,
            cfg,
            scene.video_prompt,
            image=image,
            image_path=image_path,
            image_role=image_role,
            save_path=clip,
            duration=clip_duration,
            resolution=resolution,
            ratio=ratio,
        )

        _report(
            progress_callback,
            ProgressEvent(
                "voiceover", base + per_scene * 0.7, frame_current=index + 1, frame_total=total
            ),
        )
        audio = str(workspace / f"narration_{index:02d}.mp3")
        await _tts(scene.narration, voice, speed, audio)

        segment = str(workspace / f"segment_{index:02d}.mp4")
        await asyncio.to_thread(_mux_scene, clip, audio, segment)
        segments.append(segment)

    _report(progress_callback, ProgressEvent("concatenating", 0.9))
    final_path = str(workspace / "final.mp4")
    await asyncio.to_thread(_concat_with_bgm, segments, final_path, bgm_path, bgm_volume)

    duration = await asyncio.to_thread(_probe_duration, final_path)
    file_size = Path(final_path).stat().st_size
    _report(progress_callback, ProgressEvent("completed", 1.0))
    return {"video_path": final_path, "duration": duration, "file_size": file_size}
