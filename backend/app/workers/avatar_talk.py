from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Asset, TaskAsset, VideoTask, Voice
from app.db.session import SessionLocal
from app.providers.base import invoke, resolve
from app.providers.url_guard import (
    ensure_https_url_allowed,
    object_storage_public_hosts,
)
from app.services.progress import ProgressStore, build_progress_store
from app.services.quota import release_reserved_quota, settle_reserved_quota
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@dataclass
class AvatarTalkContext:
    task_id: str
    tenant_id: str
    db: Session
    store: ProgressStore
    storage: ObjectStorage
    duration_sec: float | None = None
    storage_key: str | None = None
    thumbnail_key: str | None = None
    size_bytes: int | None = None


def _scoped_id(tenant_id: str, task_id: str) -> str:
    return f"{tenant_id}:{task_id}"


def _task_or_raise(db: Session, *, tenant_id: str, task_id: str) -> VideoTask:
    task = db.get(VideoTask, task_id)
    if task is None or task.tenant_id != tenant_id:
        raise RuntimeError("Video task not found for avatar_talk worker.")
    return task


def _work_dir(task_id: str) -> Path:
    return Path(tempfile.gettempdir()) / "huading-avatar-talk" / task_id


def _download_bytes(url: str) -> bytes:
    import requests

    allowed_hosts = {"visual.volcengineapi.com"} | object_storage_public_hosts(
        settings.engine_s3_public_endpoint,
        settings.storage_endpoint_url,
        bucket=settings.engine_s3_bucket,
        addressing_style=settings.engine_s3_addressing_style,
    )
    ensure_https_url_allowed(url, allowed_hosts=allowed_hosts)
    response = requests.get(url, timeout=settings.engine_omnihuman_request_timeout_seconds)
    response.raise_for_status()
    return response.content


def _input_avatar_asset(ctx: AvatarTalkContext) -> Asset:
    asset = ctx.db.scalar(
        select(Asset)
        .join(TaskAsset, TaskAsset.asset_id == Asset.id)
        .where(
            TaskAsset.video_task_id == ctx.task_id,
            TaskAsset.role == "input_avatar",
            Asset.status == "ready",
            Asset.deleted_at.is_(None),
        )
    )
    if asset is None:
        raise RuntimeError("Input avatar asset not found.")
    return asset


def _add_asset(
    ctx: AvatarTalkContext,
    *,
    storage_key: str,
    role: str,
    asset_type: str,
    mime_type: str,
    size_bytes: int | None = None,
    duration_ms: int | None = None,
) -> Asset:
    asset = Asset(
        tenant_id=ctx.tenant_id,
        type=asset_type,
        source="generated",
        provider="avatar_talk",
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=size_bytes,
        duration_ms=duration_ms,
        status="ready",
    )
    ctx.db.add(asset)
    ctx.db.flush()
    ctx.db.add(TaskAsset(video_task_id=ctx.task_id, asset_id=asset.id, role=role))
    return asset


def script_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    task = _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    if not task.script:
        if not (
            settings.engine_llm_api_key
            and settings.engine_llm_base_url
            and settings.engine_llm_model
        ):
            raise RuntimeError("DeepSeek is not configured for avatar_talk script generation.")
        provider = resolve(ctx.db, tenant_id=ctx.tenant_id, capability="llm")
        result = asyncio.run(
            invoke(
                ctx.db,
                tenant_id=ctx.tenant_id,
                capability="llm",
                provider=provider.__class__.__name__,
                operation=lambda: provider.generate_text({"topic": task.topic or ""}),
                timeout_seconds=30.0,
            )
        )
        script = str(result.get("text") or "").strip()
        if not script:
            raise RuntimeError("DeepSeek returned an empty avatar_talk script.")
        task.script = script
    return ctx


def tts_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    task = _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    voice = ctx.db.get(Voice, task.voice_id) if task.voice_id else None
    if voice is None:
        raise RuntimeError("Voice not found for avatar_talk.")
    provider = resolve(ctx.db, tenant_id=ctx.tenant_id, capability="tts")
    result = asyncio.run(
        invoke(
            ctx.db,
            tenant_id=ctx.tenant_id,
            capability="tts",
            provider=provider.__class__.__name__,
            operation=lambda: provider.synthesize_speech(
                {
                    "text": task.script or task.topic or "",
                    "voice": voice.voice_code,
                    "speed": float(task.speed or 1.0),
                    "task_id": ctx.task_id,
                    "output_dir": str(_work_dir(ctx.task_id)),
                }
            ),
            timeout_seconds=settings.engine_omnihuman_request_timeout_seconds,
        )
    )
    audio_bytes = Path(str(result["audio_path"])).read_bytes()
    audio_key = f"tenants/{ctx.tenant_id}/videos/{ctx.task_id}/audio.mp3"
    ctx.storage.put_bytes(audio_key, audio_bytes, content_type="audio/mpeg")
    _add_asset(
        ctx,
        storage_key=audio_key,
        role="output_audio",
        asset_type="audio",
        mime_type="audio/mpeg",
        size_bytes=len(audio_bytes),
        duration_ms=int(result.get("duration_ms") or 0),
    )
    ctx.audio_key = audio_key
    ctx.timeline = result.get("timeline") or []
    ctx.duration_sec = max(1, int(result.get("duration_ms") or 1000) / 1000)
    return ctx


def avatar_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    avatar = _input_avatar_asset(ctx)
    audio_key = getattr(ctx, "audio_key", None)
    if not audio_key:
        raise RuntimeError("TTS audio is missing for avatar generation.")
    provider = resolve(ctx.db, tenant_id=ctx.tenant_id, capability="avatar")
    payload = {
        "image_url": ctx.storage.presign_get_url(
            avatar.storage_key,
            expires_in=settings.engine_s3_presign_ttl,
        ),
        "audio_url": ctx.storage.presign_get_url(
            audio_key,
            expires_in=settings.engine_s3_presign_ttl,
        ),
        "prompt": _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id).topic,
        "aigc_meta": {
            "content_producer": "Huading",
            "producer_id": ctx.tenant_id,
            "content_propagator": "Huading",
            "propagate_id": ctx.task_id,
        },
    }
    result = asyncio.run(
        invoke(
            ctx.db,
            tenant_id=ctx.tenant_id,
            capability="avatar",
            provider=provider.__class__.__name__,
            operation=lambda: provider.generate_avatar(payload),
            timeout_seconds=settings.engine_omnihuman_timeout_seconds,
        )
    )
    ctx.base_video_bytes = _download_bytes(str(result["video_url"]))
    return ctx


def subtitle_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    timeline = getattr(ctx, "timeline", []) or []
    lines = []
    for index, item in enumerate(timeline, start=1):
        start_ms = int(item.get("start_ms") or 0)
        end_ms = int(item.get("end_ms") or start_ms + 1000)
        lines.append(
            f"{index}\n{_srt_time(start_ms)} --> {_srt_time(end_ms)}\n{item.get('text') or ''}\n"
        )
    content = "\n".join(lines).encode("utf-8")
    subtitle_key = f"tenants/{ctx.tenant_id}/videos/{ctx.task_id}/subtitle.srt"
    ctx.storage.put_bytes(subtitle_key, content, content_type="application/x-subrip")
    _add_asset(
        ctx,
        storage_key=subtitle_key,
        role="output_subtitle",
        asset_type="subtitle",
        mime_type="application/x-subrip",
        size_bytes=len(content),
    )
    ctx.subtitle_key = subtitle_key
    return ctx


def _srt_time(ms: int) -> str:
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def compose_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    base_video_bytes = getattr(ctx, "base_video_bytes", b"")
    if not base_video_bytes:
        raise RuntimeError("Avatar base video is missing for compose.")
    subtitle_key = getattr(ctx, "subtitle_key", None)
    if not subtitle_key:
        raise RuntimeError("Subtitle asset is missing for compose.")

    work_dir = _work_dir(ctx.task_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    base_path = work_dir / "base.mp4"
    subtitle_path = work_dir / "subtitle.srt"
    output_path = work_dir / "final.mp4"
    base_path.write_bytes(base_video_bytes)
    subtitle_path.write_bytes(ctx.storage.get_bytes(subtitle_key))
    _burn_subtitles(base_path, subtitle_path, output_path)
    ctx.final_video_bytes = output_path.read_bytes()
    return ctx


def _parse_srt_time(value: str) -> float:
    hours, minutes, rest = value.split(":", 2)
    seconds, millis = rest.split(",", 1)
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis.ljust(3, "0")[:3]) / 1000
    )


def _parse_srt(path: Path) -> list[tuple[float, float, str]]:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(?:^|\n)\s*\d+\s*\n"
        r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*"
        r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})\s*\n"
        r"(?P<text>.*?)(?=\n\s*\d+\s*\n|\Z)",
        re.S,
    )
    captions = []
    for match in pattern.finditer(text):
        caption = " ".join(line.strip() for line in match.group("text").splitlines()).strip()
        if caption:
            captions.append(
                (
                    _parse_srt_time(match.group("start")),
                    _parse_srt_time(match.group("end")),
                    caption,
                )
            )
    return captions


def _font(size: int):
    from PIL import ImageFont

    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, *, max_width: int, draw, font) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _subtitle_image(text: str, *, width: int, height: int):
    from PIL import Image, ImageDraw

    font_size = max(18, int(height * 0.045))
    font = _font(font_size)
    image = Image.new("RGBA", (width, int(height * 0.24)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    lines = _wrap_text(text, max_width=int(width * 0.82), draw=draw, font=font)
    line_gap = max(4, int(font_size * 0.25))
    line_heights = []
    line_widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
    text_h = sum(line_heights) + line_gap * max(0, len(lines) - 1)
    y = max(0, (image.height - text_h) // 2)
    pad_x = int(width * 0.06)
    pad_y = int(font_size * 0.45)
    box_w = min(int(width * 0.9), max(line_widths, default=0) + pad_x * 2)
    box_h = text_h + pad_y * 2
    box_x = (width - box_w) // 2
    box_y = max(0, y - pad_y)
    draw.rounded_rectangle(
        (box_x, box_y, box_x + box_w, box_y + box_h),
        radius=max(8, font_size // 2),
        fill=(0, 0, 0, 150),
    )
    for line, line_w, line_h in zip(lines, line_widths, line_heights, strict=False):
        x = (width - line_w) // 2
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 230))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h + line_gap
    return image


def _burn_subtitles(
    base_video: Path,
    subtitle_file: Path,
    output: Path,
    *,
    target_size: tuple[int, int] = (1080, 1920),
) -> None:
    import numpy as np
    from moviepy.editor import ColorClip, CompositeVideoClip, ImageClip, VideoFileClip
    from PIL import Image

    if not hasattr(Image, "ANTIALIAS"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS

    width, height = target_size
    video = VideoFileClip(str(base_video))
    try:
        scale = min(width / video.w, height / video.h)
        resized = video.resize(scale).set_position("center")
        canvas = ColorClip(target_size, color=(0, 0, 0), duration=video.duration)
        clips = [canvas, resized]
        for start, end, text in _parse_srt(subtitle_file):
            if end <= 0 or start >= video.duration:
                continue
            caption = ImageClip(
                np.array(_subtitle_image(text, width=width, height=height)),
                transparent=True,
            )
            caption = (
                caption.set_start(max(0, start))
                .set_duration(max(0.1, min(end, video.duration) - max(0, start)))
                .set_position(("center", int(height * 0.72)))
            )
            clips.append(caption)
        final = CompositeVideoClip(clips, size=target_size).set_duration(video.duration)
        if video.audio is not None:
            final = final.set_audio(video.audio)
        output.parent.mkdir(parents=True, exist_ok=True)
        final.write_videofile(
            str(output),
            fps=24,
            codec="libx264",
            audio_codec="aac",
            audio=video.audio is not None,
            logger=None,
        )
        final.close()
    finally:
        video.close()


def upload_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    final_bytes = getattr(ctx, "final_video_bytes", b"")
    final_key = f"tenants/{ctx.tenant_id}/videos/{ctx.task_id}/final.mp4"
    ctx.storage.put_bytes(final_key, final_bytes, content_type="video/mp4")
    _add_asset(
        ctx,
        storage_key=final_key,
        role="output_video",
        asset_type="video",
        mime_type="video/mp4",
        size_bytes=len(final_bytes),
        duration_ms=int((ctx.duration_sec or 0) * 1000),
    )
    ctx.storage_key = final_key
    ctx.size_bytes = len(final_bytes)
    return ctx


AVATAR_TALK_STEPS = [
    ("script", 10, script_step),
    ("tts", 20, tts_step),
    ("avatar", 85, avatar_step),
    ("subtitle", 90, subtitle_step),
    ("compose", 95, compose_step),
    ("upload", 98, upload_step),
]


def run_avatar_talk_pipeline(*, tenant_id: str, task_id: str) -> dict[str, Any]:
    store = build_progress_store(settings.redis_url)
    storage = create_object_storage(settings)
    scoped_id = _scoped_id(tenant_id, task_id)
    with SessionLocal() as db:
        task = _task_or_raise(db, tenant_id=tenant_id, task_id=task_id)
        task.status = "running"
        task.progress = max(task.progress or 0, 1)
        task.started_at = task.started_at or datetime.now(UTC)
        db.commit()
        store.update(scoped_id, status="running", progress=1, step="queued")

        ctx = AvatarTalkContext(
            task_id=task_id,
            tenant_id=tenant_id,
            db=db,
            store=store,
            storage=storage,
        )
        try:
            for step, progress, fn in AVATAR_TALK_STEPS:
                ctx = fn(ctx)
                task.progress = int(progress)
                db.commit()
                store.update(scoped_id, status="running", progress=int(progress), step=step)

            task.status = "done"
            task.progress = 100
            task.finished_at = datetime.now(UTC)
            if ctx.storage_key:
                task.storage_key = ctx.storage_key
            if ctx.thumbnail_key:
                task.thumbnail_key = ctx.thumbnail_key
            if ctx.size_bytes is not None:
                task.size_bytes = ctx.size_bytes
            if ctx.duration_sec is not None:
                task.duration_sec = float(ctx.duration_sec)
            settle_reserved_quota(
                db,
                tenant_id=tenant_id,
                video_task_id=task_id,
                actual_seconds=max(1, int(round(ctx.duration_sec or 1))),
                cost_cents=max(1, int(round(ctx.duration_sec or 1))) * 100,
            )
            db.commit()
            store.update(
                scoped_id,
                status="done",
                progress=100,
                step="done",
                playback_url=(
                    storage.presign_get_url(
                        task.storage_key,
                        expires_in=settings.engine_s3_presign_ttl,
                    )
                    if task.storage_key
                    else None
                ),
                download_url=(
                    storage.presign_get_url(
                        task.storage_key,
                        expires_in=settings.engine_s3_presign_ttl,
                        download_filename=f"{task.id}.mp4",
                    )
                    if task.storage_key
                    else None
                ),
            )
            return {"task_id": task_id, "status": "done"}
        except Exception as exc:
            task.status = "failed"
            task.error_code = "AVATAR_TALK_FAILED"
            task.error_message = str(exc)
            task.error = str(exc)
            task.finished_at = datetime.now(UTC)
            release_reserved_quota(db, tenant_id=tenant_id, video_task_id=task_id)
            db.commit()
            store.update(
                scoped_id,
                status="failed",
                progress=task.progress or 0,
                step="failed",
                error_code="AVATAR_TALK_FAILED",
                error_message=str(exc),
            )
            raise


@celery_app.task(bind=True, name="app.workers.avatar_talk.generate")
def generate_avatar_talk_task(self, params: dict[str, Any]) -> dict[str, Any]:
    task_id = self.request.id or params.get("video_task_id") or "unknown"
    tenant_id = str(params["tenant_id"])
    logger.info("avatar_talk.queued", task_id=task_id, tenant_id=tenant_id)
    return run_avatar_talk_pipeline(tenant_id=tenant_id, task_id=task_id)
