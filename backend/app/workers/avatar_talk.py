from __future__ import annotations

import asyncio
import json
import math
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock
from typing import Any

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.models import Asset, BrandVoice, TaskAsset, VideoTask, Voice
from app.db.session import SessionLocal
from app.providers.base import invoke, resolve, resolve_named_provider
from app.providers.url_guard import (
    ensure_https_url_allowed,
    object_storage_public_hosts,
    parse_host_suffixes,
)
from app.services import provider_costs
from app.services.apimart_costs import (
    apimart_cost_cents_from_credits,
    apimart_cost_cents_from_price_table,
    apimart_cost_cents_from_result,
    apimart_price_table_credits,
)
from app.services.batches import refresh_batch_job
from app.services.history import prune_video_history_best_effort
from app.services.plan_access import (
    require_doubao_voice_clone_access,
    uses_doubao_voice_clone,
)
from app.services.progress import ProgressStore, build_progress_store
from app.services.quota import release_reserved_quota, settle_reserved_quota
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage
from app.services.subtitle_styles import resolve_subtitle_style
from app.services.synthetic_label import (
    label_artifact_bytes,
    synthetic_label_context,
)
from app.services.task_claims import claim_video_task_for_worker
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

_MIN_TIMELINE_COVERAGE_RATIO = 0.8
_TAIL_TRIM_THRESHOLD_SEC = 0.6
_TAIL_KEEP_AFTER_SPEECH_SEC = 0.5
_AUDIO_FADEOUT_SEC = 0.3
_AUDIO_TAIL_FADEOUT_SEC = 0.05
_SCRIPT_CLAUSE_ENDINGS = tuple(".!?;。！？；，、")
_SEEDANCE_I2V_DEFAULT_DURATION_SEC = 15
_SEEDANCE_I2V_MIN_DURATION_SEC = 5
_SEEDANCE_I2V_MAX_DURATION_SEC = 120
_SEEDANCE_I2V_CLIP_DURATION_SEC = 5
_SEEDANCE_I2V_PROGRESS_START = 25
_SEEDANCE_I2V_PROGRESS_END = 88
_SEEDANCE_I2V_PROGRESS_POLL_RATE = 0.02
_SEEDANCE_I2V_PROGRESS_EPSILON = 0.001
_APIMART_VIDEO_MIN_PRESIGN_TTL_SECONDS = 7200
_SEEDANCE_I2V_RESOLUTIONS = {"480p", "720p", "1080p"}
_CHANGE_LIPS_MODEL = "realman_change_lips"
_CHANGE_LIPS_LOCK_KEY = "provider:omnihuman:change-lips"
_CHANGE_LIPS_BUSY_MESSAGE = "改口型服务繁忙，请稍后重试。"
_CHANGE_LIPS_LOCAL_LOCK = Lock()
_CHANGE_LIPS_DURATION_TOLERANCE_SEC = 0.1
_CHANGE_LIPS_OPTIONAL_FIELDS = {
    "align_audio_reverse",
    "templ_start_seconds",
    "separate_vocal",
    "open_scenedet",
}
_SEEDANCE_I2V_PROMPT_SUFFIX = (
    "产品展示，镜头平稳推进，明亮商业棚拍，干净背景，"
    "突出商品材质与卖点，9:16竖屏电商带货短视频。"
)
_ECOMMERCE_SCRIPT_SYSTEM_PROMPT = (
    "你是电商带货短视频口播文案策划。只输出可直接朗读的中文卖货口播正文，"
    "围绕产品卖点、使用场景、购买理由和自然行动号召展开。"
    "禁止写镜头/运镜/画面/景别描述，禁止写数字人/出镜/微笑致意等人物舞台提示，"
    "禁止 Markdown、标题、编号、旁白标注或括号说明。"
)
_SEEDANCE_SCENE_SYSTEM_PROMPT = (
    "你是电商图生视频的视觉分镜导演。输出 Seedance i2v 视觉分镜提示词，"
    "每条提示词都以同一张产品图作为 first_frame，并给出不同运镜、角度和卖点。"
)
_ECOMMERCE_SCRIPT_FORBIDDEN_TERMS = (
    "镜头",
    "运镜",
    "画面",
    "景别",
    "数字人",
    "出镜",
    "微笑致意",
)


class ChangeLipsOutputTooShort(RuntimeError):
    pass


@contextmanager
def _change_lips_serial_slot(store: ProgressStore):
    client = getattr(store, "_redis", None)
    if client is None:
        with _CHANGE_LIPS_LOCAL_LOCK:
            yield
        return
    lock_timeout = max(
        float(settings.engine_omnihuman_timeout_seconds)
        + 8 * float(settings.engine_omnihuman_request_timeout_seconds)
        + 120,
        120,
    )
    lock = client.lock(
        _CHANGE_LIPS_LOCK_KEY,
        timeout=lock_timeout,
        blocking_timeout=lock_timeout,
    )
    try:
        acquired = bool(lock.acquire(blocking=True))
    except redis.RedisError as exc:
        raise RuntimeError(_CHANGE_LIPS_BUSY_MESSAGE) from exc
    if not acquired:
        raise RuntimeError(_CHANGE_LIPS_BUSY_MESSAGE)
    try:
        yield
    finally:
        try:
            lock.release()
        except redis.RedisError:
            logger.warning("change_lips_lock_release_failed")


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


def _seedance_i2v_image_url(ctx: AvatarTalkContext, params: Mapping[str, Any]) -> str:
    from app.workers.video_tasks import _tenant_upload_storage_key

    image_key = str(params.get("image_key") or "")
    storage_key = _tenant_upload_storage_key(ctx.tenant_id, image_key)
    presign_ttl = max(
        int(settings.engine_s3_presign_ttl),
        math.ceil(float(settings.engine_apimart_video_timeout_seconds)),
        _APIMART_VIDEO_MIN_PRESIGN_TTL_SECONDS,
    )
    return ctx.storage.presign_get_url(storage_key, expires_in=presign_ttl)


def _seedance_i2v_provider_payload(
    *,
    prompt: str,
    image_url: str,
    clip_duration: int,
    resolution: str,
    progress_callback: Any,
) -> dict[str, Any]:
    return {
        "model": settings.engine_apimart_video_model,
        "prompt": prompt,
        "duration": clip_duration,
        "resolution": resolution,
        "size": "adaptive",
        "generate_audio": False,
        "image_urls": [image_url],
        "progress_callback": progress_callback,
    }


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _positive_int_or_none(value: Any) -> int | None:
    decimal_value = _decimal_or_none(value)
    if decimal_value is None or decimal_value <= 0:
        return None
    return int(decimal_value)


def _seedance_i2v_clip_billing(
    result: Mapping[str, Any],
    *,
    clip_duration: int,
    resolution: str,
) -> tuple[Decimal, int]:
    credits = _decimal_or_none(result.get("credits"))
    if credits is not None and credits > 0:
        return credits, 0

    cost_cents = _positive_int_or_none(result.get("cost_cents"))
    if cost_cents is not None:
        return Decimal("0"), cost_cents

    price_table_credits = apimart_price_table_credits(
        model=str(result.get("model") or settings.engine_apimart_video_model),
        resolution=result.get("resolution") or resolution,
        duration_sec=result.get("duration") or clip_duration,
    )
    if price_table_credits is not None and price_table_credits > 0:
        return price_table_credits, 0

    return Decimal("0"), apimart_cost_cents_from_result(result)


def _seedance_i2v_resolution(params: Mapping[str, Any] | None) -> str:
    resolution = str((params or {}).get("resolution") or "720p").strip().lower()
    return resolution if resolution in _SEEDANCE_I2V_RESOLUTIONS else "720p"


def _seedance_i2v_fallback_cost_cents(*, actual_seconds: int, resolution: str = "720p") -> int:
    scene_count = max(1, int(math.ceil(actual_seconds / _SEEDANCE_I2V_CLIP_DURATION_SEC)))
    normalized_resolution = str(resolution or "720p").strip().lower()
    selected_resolution = (
        normalized_resolution if normalized_resolution in _SEEDANCE_I2V_RESOLUTIONS else "720p"
    )
    credits = sum(
        (
            apimart_price_table_credits(
                model=settings.engine_apimart_video_model,
                resolution=selected_resolution,
                duration_sec=_SEEDANCE_I2V_CLIP_DURATION_SEC,
            )
            or Decimal("0")
        )
        for _ in range(scene_count)
    )
    cost_cents = apimart_cost_cents_from_credits(credits)
    if cost_cents > 0:
        return cost_cents
    return scene_count * apimart_cost_cents_from_price_table(
        model=settings.engine_apimart_video_model,
        resolution=selected_resolution,
        duration_sec=_SEEDANCE_I2V_CLIP_DURATION_SEC,
    )


def _seedance_i2v_prompt(topic: str | None) -> str:
    subject = str(topic or "").strip()
    if not subject:
        return _SEEDANCE_I2V_PROMPT_SUFFIX
    return f"{subject}。{_SEEDANCE_I2V_PROMPT_SUFFIX}"


def _seedance_i2v_target_duration(duration_sec: float | int | None) -> int:
    if duration_sec is None:
        return _SEEDANCE_I2V_DEFAULT_DURATION_SEC
    raw = int(float(duration_sec))
    return max(_SEEDANCE_I2V_MIN_DURATION_SEC, min(_SEEDANCE_I2V_MAX_DURATION_SEC, raw))


def _task_target_duration_sec(task: VideoTask) -> int:
    params = task.params or {}
    return _seedance_i2v_target_duration(params.get("duration_sec") or task.duration_sec)


def _seedance_i2v_scene_count(duration_sec: float | int | None) -> int:
    target = _seedance_i2v_target_duration(duration_sec)
    return max(1, int(math.ceil(target / _SEEDANCE_I2V_CLIP_DURATION_SEC)))


def _seedance_i2v_billable_duration(duration_sec: float | int | None) -> int:
    return _seedance_i2v_scene_count(duration_sec) * _SEEDANCE_I2V_CLIP_DURATION_SEC


def _seedance_i2v_poll_progress(
    *,
    poll_tick: int,
) -> float:
    safe_poll_tick = max(0, poll_tick)
    span = _SEEDANCE_I2V_PROGRESS_END - _SEEDANCE_I2V_PROGRESS_START
    ratio = 1 - (1 / (1 + safe_poll_tick * _SEEDANCE_I2V_PROGRESS_POLL_RATE))
    progress = _SEEDANCE_I2V_PROGRESS_START + span * ratio
    return min(_SEEDANCE_I2V_PROGRESS_END - _SEEDANCE_I2V_PROGRESS_EPSILON, progress)


def concat_seedance_clips(scene_paths: list[str], output_path: str) -> str:
    if not scene_paths:
        raise RuntimeError("No Seedance scene clips to concatenate.")
    if len(scene_paths) == 1:
        Path(output_path).write_bytes(Path(scene_paths[0]).read_bytes())
        return output_path

    import importlib

    importlib.import_module("app.engine")
    from pixelle_video.services.video import VideoService

    return VideoService().concat_videos(videos=scene_paths, output=output_path)


def _ffmpeg_binary() -> str:
    return os.environ.get("FFMPEG_BINARY", "ffmpeg")


def _strip_video_audio(source: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        _ffmpeg_binary(),
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-an",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Failed to strip Seedance scene audio: {detail}")
    return output


def concat_seedance_clips_without_audio(scene_paths: list[str], output_path: str) -> str:
    if not scene_paths:
        raise RuntimeError("No Seedance scene clips to concatenate.")
    output = Path(output_path)
    if len(scene_paths) == 1:
        _strip_video_audio(Path(scene_paths[0]), output)
        return output_path

    silent_paths: list[Path] = []
    try:
        for index, scene_path in enumerate(scene_paths):
            silent_path = output.with_name(f"{output.stem}_silent_{index:02d}{output.suffix}")
            _strip_video_audio(Path(scene_path), silent_path)
            silent_paths.append(silent_path)
        return concat_seedance_clips([str(path) for path in silent_paths], output_path)
    finally:
        for path in silent_paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _download_bytes(url: str) -> bytes:
    import requests

    allowed_hosts = {"visual.volcengineapi.com"} | object_storage_public_hosts(
        settings.engine_s3_public_endpoint,
        settings.storage_endpoint_url,
        bucket=settings.engine_s3_bucket,
        addressing_style=settings.engine_s3_addressing_style,
    )
    ensure_https_url_allowed(
        url,
        allowed_hosts=allowed_hosts,
        allowed_host_suffixes=parse_host_suffixes(settings.engine_omnihuman_result_host_suffixes),
    )
    response = requests.get(url, timeout=settings.engine_omnihuman_request_timeout_seconds)
    response.raise_for_status()
    return response.content


def _audio_duration_sec(path: Path) -> float:
    from moviepy.editor import AudioFileClip

    try:
        clip = AudioFileClip(str(path))
    except Exception:
        return 0.0
    try:
        return max(0.0, float(clip.duration or 0))
    finally:
        clip.close()


def _video_duration_sec(path: Path) -> float:
    from moviepy.editor import VideoFileClip

    try:
        clip = VideoFileClip(str(path))
    except Exception:
        return 0.0
    try:
        return max(0.0, float(clip.duration or 0))
    finally:
        clip.close()


def _trim_video_bytes(source: Path, *, duration_sec: float, output: Path) -> bytes:
    from moviepy.editor import VideoFileClip

    clip = VideoFileClip(str(source))
    trimmed = None
    try:
        duration = min(max(0.1, float(duration_sec)), max(0.1, float(clip.duration or 0.1)))
        trimmed = clip.subclip(0, duration)
        output.parent.mkdir(parents=True, exist_ok=True)
        trimmed.write_videofile(
            str(output),
            fps=25,
            codec="libx264",
            audio_codec="aac",
            audio_bitrate="192k",
            audio_fps=44100,
            logger=None,
        )
        return output.read_bytes()
    finally:
        if trimmed is not None:
            trimmed.close()
        clip.close()


def _fit_change_lips_video_to_tts(
    video_bytes: bytes,
    *,
    tts_duration_sec: float,
    work_dir: Path,
) -> bytes:
    source = work_dir / "change_lips_result.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(video_bytes)
    output_duration = _video_duration_sec(source)
    if output_duration <= 0:
        raise RuntimeError("改口型结果视频无法解析。")
    if output_duration + _CHANGE_LIPS_DURATION_TOLERANCE_SEC < tts_duration_sec:
        raise ChangeLipsOutputTooShort("改口型结果短于口播音频，请缩短文案或更换源视频。")
    if output_duration - tts_duration_sec > _CHANGE_LIPS_DURATION_TOLERANCE_SEC:
        return _trim_video_bytes(
            source,
            duration_sec=tts_duration_sec,
            output=work_dir / "change_lips_result.trimmed.mp4",
        )
    return video_bytes


def _change_lips_tier() -> str:
    value = str(settings.engine_omnihuman_change_lips_default_tier or "basic").lower()
    return "basic" if value == "basic" else "lite"


def _change_lips_open_sr_enabled(task: VideoTask, *, tier: str) -> bool:
    if tier != "basic" or not settings.engine_omnihuman_change_lips_open_sr:
        return False
    requested = (task.params or {}).get("open_sr")
    return requested is not False


def _change_lips_tts_limit_seconds(tier: str) -> float:
    return 150.0 if tier == "basic" else 240.0


def _validate_change_lips_tts_duration(duration_sec: float, *, tier: str) -> None:
    if duration_sec <= 1.0:
        raise RuntimeError("口播音频过短，请增加口播文案。")
    limit = _change_lips_tts_limit_seconds(tier)
    if duration_sec > limit:
        raise RuntimeError(f"口播文案过长，{tier} 档位最长支持 {int(limit)} 秒。")


def _audio_codec_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return "pcm_s16le"
    if suffix == ".m4a":
        return "aac"
    return "libmp3lame"


def _write_tail_faded_audio(
    source: Path,
    output: Path,
    *,
    fadeout_sec: float = _AUDIO_TAIL_FADEOUT_SEC,
) -> Path:
    from moviepy.audio.fx.audio_fadeout import audio_fadeout
    from moviepy.editor import AudioFileClip

    clip = AudioFileClip(str(source))
    faded = None
    try:
        fade_duration = min(max(0.0, fadeout_sec), max(0.0, float(clip.duration or 0)))
        faded = clip.fx(audio_fadeout, fade_duration) if fade_duration > 0 else clip
        output.parent.mkdir(parents=True, exist_ok=True)
        faded.write_audiofile(
            str(output),
            codec=_audio_codec_for_path(output),
            fps=44100,
            logger=None,
        )
        return output
    finally:
        if faded is not None and faded is not clip:
            faded.close()
        clip.close()


def _tail_faded_tts_audio(source: Path) -> Path:
    output = source.with_name(f"{source.stem}.tail_faded{source.suffix or '.mp3'}")
    try:
        return _write_tail_faded_audio(source, output)
    except Exception as exc:
        logger.warning(
            "avatar_talk.tts_tail_fade_failed",
            error=str(exc),
            suffix=source.suffix.lower(),
        )
        return source


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
    metadata: dict[str, object] | None = None,
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
        metadata_=metadata or {},
    )
    ctx.db.add(asset)
    ctx.db.flush()
    ctx.db.add(TaskAsset(video_task_id=ctx.task_id, asset_id=asset.id, role=role))
    return asset


def _apply_synthetic_label(
    ctx: AvatarTalkContext,
    content: bytes,
    *,
    kind: str,
    suffix: str,
) -> tuple[bytes, dict[str, object]]:
    task = ctx.db.get(VideoTask, ctx.task_id)
    requested_visible = (
        bool((task.params or {}).get("apply_visible_label", False)) if task else False
    )
    visible = requested_visible if kind in {"image", "video"} else False
    label_settings, meta, payload = synthetic_label_context(
        ctx.db,
        tenant_id=ctx.tenant_id,
        content_id=ctx.task_id,
        visible=visible,
    )
    return (
        label_artifact_bytes(
            content,
            kind=kind,
            settings=label_settings,
            meta=meta,
            suffix=suffix,
            visible=visible,
        ),
        payload,
    )


def clean_spoken_script(text: str) -> str:
    cleaned = _strip_markdown_fence(str(text or ""))
    cleaned = re.sub(r"【[^】]*】", "", cleaned)
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    lines: list[str] = []
    for raw_line in cleaned.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"^\s*(?:[-+*]|\d+[.)、])\s*", "", line)
        line = re.sub(r"^\s*>\s*", "", line)
        line = re.sub(r"`([^`]*)`", r"\1", line)
        line = re.sub(r"(\*\*|__)(.*?)\1", r"\2", line)
        line = re.sub(r"(?<!\w)(\*|_)([^*_]+)\1(?!\w)", r"\2", line)
        line = re.sub(r"[`*_#]+", "", line).strip()
        if re.fullmatch(r"[（(][^（）()]*[）)]", line):
            continue
        line = re.sub(
            r"^(?:数字人主播脚本|主播脚本|口播脚本|脚本|标题|文案)\s*[:：]?\s*",
            "",
            line,
        ).strip()
        if re.fullmatch(r"[（(][^（）()]*[）)]", line):
            continue
        if line:
            for piece in re.split(r"(?<=[。！？.!?])", line):
                piece = piece.strip()
                if piece and not any(term in piece for term in _ECOMMERCE_SCRIPT_FORBIDDEN_TERMS):
                    lines.append(piece)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()

def _scene_prompt_source(task: VideoTask) -> str:
    params = task.params or {}
    raw_scene_prompt = params.get("scene_prompt")
    if isinstance(raw_scene_prompt, str) and raw_scene_prompt.strip():
        return raw_scene_prompt.strip()
    return str(task.topic or "").strip() or "product"


def build_seedance_scene_prompt_payload(
    topic: str,
    *,
    duration_sec: float | int | None = None,
) -> dict[str, Any]:
    target_duration = _seedance_i2v_target_duration(duration_sec)
    topic_text = str(topic or "").strip()
    return {
        "topic": topic_text,
        "video_mode": "seedance_i2v",
        "target_duration_sec": target_duration,
        "system_prompt": _SEEDANCE_SCENE_SYSTEM_PROMPT,
        "user_prompt": (
            "请根据产品主题生成一段整体画面提示词，供电商图生视频使用。"
            "只描述产品视觉氛围、场景、光线、材质、构图和商业质感；"
            "不要写口播台词、字幕、旁白、人物出镜或 Markdown。"
            f"目标视频时长约{target_duration}秒。\n产品主题：{topic_text}"
        ),
    }


def build_script_payload(
    topic: str,
    *,
    video_mode: str | None = None,
    duration_sec: float | int | None = None,
) -> dict[str, Any]:
    topic_text = str(topic or "")
    if video_mode != "seedance_i2v":
        return {"topic": topic_text}

    target_duration = _seedance_i2v_target_duration(duration_sec)
    target_chars_min = target_duration * 5
    target_chars_max = target_duration * 6
    return {
        "topic": topic_text,
        "video_mode": "seedance_i2v",
        "target_duration_sec": target_duration,
        "target_chars_min": target_chars_min,
        "target_chars_max": target_chars_max,
        "system_prompt": _ECOMMERCE_SCRIPT_SYSTEM_PROMPT,
        "user_prompt": (
            f"请基于以下产品卖点生成一段约{target_duration}秒的电商带货口播文案。"
            f"字数控制在{target_chars_min}-{target_chars_max}字，语言口语化，"
            "突出卖点、使用场景、购买理由，并在结尾加入自然行动号召。"
            "只输出可直接朗读的卖货正文，不要标题、编号或 Markdown。"
            "严禁出现镜头/运镜/画面/景别描述，严禁出现数字人、出镜、微笑致意、旁白标注。\n"
            f"产品卖点：{topic_text}"
        ),
    }


def _script_generation_payload(task: VideoTask) -> dict[str, Any]:
    return build_script_payload(
        task.topic or "",
        video_mode=task.video_mode or task.mode,
        duration_sec=_task_target_duration_sec(task),
    )


def script_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    task = _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    generated_script = False
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
                operation=lambda: provider.generate_text(_script_generation_payload(task)),
                timeout_seconds=30.0,
            )
        )
        provider_costs.record_deepseek_usage(
            ctx.db,
            tenant_id=ctx.tenant_id,
            result=result,
            video_task_id=ctx.task_id,
        )
        task.script = str(result.get("text") or "")
        generated_script = True

    script = clean_spoken_script(str(task.script or ""))
    if not script:
        if generated_script:
            raise RuntimeError("DeepSeek returned an empty avatar_talk script.")
        raise RuntimeError("Avatar talk script is empty after cleanup.")
    task.script = script
    ctx.db.flush()
    return ctx


def tts_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    task = _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    voice_code, voice_source, brand_voice_provider = _tts_voice_for_task(
        ctx.db,
        task,
        tenant_id=ctx.tenant_id,
    )
    if voice_source == "brand_voice" and uses_doubao_voice_clone(brand_voice_provider):
        require_doubao_voice_clone_access(
            ctx.db,
            tenant_id=ctx.tenant_id,
        )
    provider, capability = _tts_provider_for_voice(
        ctx.db,
        tenant_id=ctx.tenant_id,
        brand_voice_provider=brand_voice_provider,
    )
    result = asyncio.run(
        invoke(
            ctx.db,
            tenant_id=ctx.tenant_id,
            capability=capability,
            provider=provider.__class__.__name__,
            operation=lambda: provider.synthesize_speech(
                {
                    "text": task.script or task.topic or "",
                    "voice": voice_code,
                    "voice_source": voice_source,
                    "brand_voice_provider": brand_voice_provider,
                    "speed": float(task.speed or 1.0),
                    "task_id": ctx.task_id,
                    "output_dir": str(_work_dir(ctx.task_id)),
                }
            ),
            timeout_seconds=settings.engine_omnihuman_request_timeout_seconds,
        )
    )
    provider_costs.record_tts_usage(
        ctx.db,
        tenant_id=ctx.tenant_id,
        result=result,
        video_task_id=ctx.task_id,
    )
    audio_source_path = _tail_faded_tts_audio(Path(str(result["audio_path"])))
    audio_bytes = audio_source_path.read_bytes()
    audio_bytes, label_metadata = _apply_synthetic_label(
        ctx,
        audio_bytes,
        kind="audio",
        suffix=".mp3",
    )
    audio_key = f"tenants/{ctx.tenant_id}/videos/{ctx.task_id}/audio.mp3"
    ctx.storage.put_bytes(audio_key, audio_bytes, content_type="audio/mpeg")
    detected_duration_sec = _audio_duration_sec(audio_source_path)
    duration_ms = int(round(detected_duration_sec * 1000))
    if duration_ms <= 0:
        duration_ms = int(result.get("duration_ms") or 0)
        detected_duration_sec = duration_ms / 1000 if duration_ms > 0 else 1.0
    _add_asset(
        ctx,
        storage_key=audio_key,
        role="output_audio",
        asset_type="audio",
        mime_type="audio/mpeg",
        size_bytes=len(audio_bytes),
        duration_ms=duration_ms,
        metadata={"synthetic_label": label_metadata},
    )
    ctx.audio_key = audio_key
    ctx.timeline = result.get("timeline") or []
    ctx.duration_sec = max(1.0, detected_duration_sec)
    logger.info(
        "avatar_talk.tts",
        task_id=ctx.task_id,
        tenant_id=ctx.tenant_id,
        voice_source=voice_source,
        brand_voice_provider=brand_voice_provider,
        timeline_items=len(ctx.timeline),
        duration_sec=ctx.duration_sec,
    )
    return ctx


def _tts_voice_for_task(
    db: Session,
    task: VideoTask,
    *,
    tenant_id: str,
) -> tuple[str, str, str | None]:
    params = task.params or {}
    brand_voice_id = task.brand_voice_id or str(params.get("brand_voice_id") or "")
    if brand_voice_id:
        brand_voice = db.get(BrandVoice, brand_voice_id)
        if (
            brand_voice is None
            or brand_voice.tenant_id != tenant_id
            or brand_voice.deleted_at is not None
            or brand_voice.status != "ready"
        ):
            raise RuntimeError("Brand voice not found for video task.")
        speaker_id = str(brand_voice.speaker_id or "").strip()
        if not speaker_id:
            raise RuntimeError("Brand voice speaker not found for video task.")
        provider = str(brand_voice.provider or "doubao-voice-clone").strip()
        return speaker_id, "brand_voice", provider

    voice = db.get(Voice, task.voice_id) if task.voice_id else None
    if voice is None:
        raise RuntimeError("Voice not found for avatar_talk.")
    return voice.voice_code, "preset", None


def _tts_provider_for_voice(
    db: Session,
    *,
    tenant_id: str,
    brand_voice_provider: str | None,
):
    if brand_voice_provider == "cosyvoice-voice-clone":
        return (
            resolve_named_provider(
                db,
                tenant_id=tenant_id,
                capability="voice_clone",
                provider=brand_voice_provider,
            ),
            "voice_clone",
        )
    return resolve(db, tenant_id=tenant_id, capability="tts"), "tts"


def avatar_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    avatar = _input_avatar_asset(ctx)
    audio_key = getattr(ctx, "audio_key", None)
    if not audio_key:
        raise RuntimeError("TTS audio is missing for avatar generation.")
    provider = resolve(ctx.db, tenant_id=ctx.tenant_id, capability="avatar")
    task = _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    if avatar.type == "video":
        tier = _change_lips_tier()
        _validate_change_lips_tts_duration(float(ctx.duration_sec or 0), tier=tier)
        payload = {
            "video_url": ctx.storage.presign_get_url(
                avatar.storage_key,
                expires_in=settings.engine_s3_presign_ttl,
            ),
            "audio_url": ctx.storage.presign_get_url(
                audio_key,
                expires_in=settings.engine_s3_presign_ttl,
            ),
            "tier": tier,
            "align_audio": True,
        }
        for key in _CHANGE_LIPS_OPTIONAL_FIELDS:
            value = (task.params or {}).get(key)
            if value is not None:
                payload[key] = value

        def on_change_lips_progress(event: dict[str, Any]) -> None:
            poll_count = int(event.get("poll_count") or 1)
            progress = min(84, 24 + poll_count)
            ctx.store.update(
                _scoped_id(ctx.tenant_id, ctx.task_id),
                status="running",
                progress=progress,
                step="avatar",
                stage="change_lips_generating",
            )

        payload["progress_callback"] = on_change_lips_progress

        def generate_for_tier(selected_tier: str) -> bytes:
            payload["tier"] = selected_tier
            payload.pop("open_sr", None)
            if _change_lips_open_sr_enabled(task, tier=selected_tier):
                payload["open_sr"] = True
            with _change_lips_serial_slot(ctx.store):
                result = asyncio.run(
                    invoke(
                        ctx.db,
                        tenant_id=ctx.tenant_id,
                        capability="avatar",
                        provider=provider.__class__.__name__,
                        operation=lambda: provider.generate_change_lips(payload),
                        timeout_seconds=settings.engine_omnihuman_timeout_seconds,
                    )
                )
            result_bytes = _download_bytes(str(result["video_url"]))
            return _fit_change_lips_video_to_tts(
                result_bytes,
                tts_duration_sec=float(ctx.duration_sec or 0),
                work_dir=_work_dir(ctx.task_id),
            )

        try:
            ctx.base_video_bytes = generate_for_tier(tier)
        except ChangeLipsOutputTooShort:
            if (
                tier != "basic"
                and settings.engine_omnihuman_change_lips_basic_retry_on_short_output
            ):
                _validate_change_lips_tts_duration(float(ctx.duration_sec or 0), tier="basic")
                tier = "basic"
                ctx.base_video_bytes = generate_for_tier(tier)
            else:
                raise
        ctx.change_lips_tier = tier
        ctx.provider_model = _CHANGE_LIPS_MODEL
        return ctx

    payload = {
        "image_url": ctx.storage.presign_get_url(
            avatar.storage_key,
            expires_in=settings.engine_s3_presign_ttl,
        ),
        "audio_url": ctx.storage.presign_get_url(
            audio_key,
            expires_in=settings.engine_s3_presign_ttl,
        ),
        "prompt": task.topic,
        "aigc_meta": {
            "content_producer": "Huading",
            "producer_id": ctx.tenant_id,
            "content_propagator": "Huading",
            "propagate_id": ctx.task_id,
        },
    }

    def on_avatar_progress(event: dict[str, Any]) -> None:
        poll_count = int(event.get("poll_count") or 1)
        progress = min(84, 24 + poll_count)
        ctx.store.update(
            _scoped_id(ctx.tenant_id, ctx.task_id),
            status="running",
            progress=progress,
            step="avatar",
            stage="avatar_generating",
        )

    payload["progress_callback"] = on_avatar_progress
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


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _fallback_scene_prompt(task: VideoTask, index: int, total: int) -> str:
    scene_prompt = _scene_prompt_source(task)
    return (
        f"{scene_prompt}。第{index + 1}/{total}个镜头，产品图作为first_frame，"
        "9:16竖屏，商业棚拍质感，镜头缓慢运动，突出不同卖点和使用场景。"
    )


def _parse_scene_prompt_text(text: str, *, scene_count: int, task: VideoTask) -> list[str]:
    cleaned = _strip_markdown_fence(text)
    prompts: list[str] = []
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        data = data.get("scenes") or data.get("prompts") or data.get("items")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                prompts.append(item.strip())
            elif isinstance(item, dict):
                value = item.get("video_prompt") or item.get("prompt") or item.get("text")
                if value:
                    prompts.append(str(value).strip())
    if not prompts:
        for line in cleaned.splitlines():
            line = re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", line).strip()
            if line:
                prompts.append(line)

    prompts = [prompt for prompt in prompts if prompt]
    while len(prompts) < scene_count:
        prompts.append(_fallback_scene_prompt(task, len(prompts), scene_count))
    return prompts[:scene_count]


def _plan_seedance_i2v_scenes(
    ctx: AvatarTalkContext,
    scene_count: int,
    clip_duration: int,
) -> list[str]:
    task = _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    provider = resolve(ctx.db, tenant_id=ctx.tenant_id, capability="llm")
    target_duration = scene_count * clip_duration
    scene_prompt = _scene_prompt_source(task)
    payload = {
        "topic": task.topic or "",
        "scene_prompt": scene_prompt,
        "video_mode": "seedance_i2v",
        "scene_count": scene_count,
        "clip_duration_sec": clip_duration,
        "target_duration_sec": target_duration,
        "system_prompt": _SEEDANCE_SCENE_SYSTEM_PROMPT,
        "user_prompt": (
            f"请为电商图生视频规划{scene_count}个视觉分镜提示词。每个分镜约"
            f"{clip_duration}秒，全部使用同一张产品图作为first_frame，但运镜、"
            "角度、光线、卖点呈现要有变化。输出 JSON 字符串数组，数组长度必须等于"
            f"{scene_count}，每项只写 Seedance 可用的中文视觉提示词。\n"
            f"产品主题：{task.topic or ''}\n整体画面提示词：{scene_prompt}"
        ),
    }
    result = asyncio.run(
        invoke(
            ctx.db,
            tenant_id=ctx.tenant_id,
            capability="llm",
            provider=provider.__class__.__name__,
            operation=lambda: provider.generate_text(payload),
            timeout_seconds=30.0,
        )
    )
    provider_costs.record_deepseek_usage(
        ctx.db,
        tenant_id=ctx.tenant_id,
        result=result,
        video_task_id=ctx.task_id,
    )
    return _parse_scene_prompt_text(
        str(result.get("text") or ""),
        scene_count=scene_count,
        task=task,
    )


def seedance_i2v_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    task = _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    work_dir = _work_dir(ctx.task_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    params = dict(task.params or {})
    params["tenant_id"] = ctx.tenant_id
    image_url = _seedance_i2v_image_url(ctx, params)
    target_duration = _task_target_duration_sec(task)
    resolution = _seedance_i2v_resolution(params)
    scene_count = _seedance_i2v_scene_count(target_duration)
    clip_duration = _SEEDANCE_I2V_CLIP_DURATION_SEC
    scene_prompts = _plan_seedance_i2v_scenes(ctx, scene_count, clip_duration)
    scene_paths: list[str] = []
    seedance_poll_ticks = 0
    last_seedance_progress = float(_SEEDANCE_I2V_PROGRESS_START)
    provider_credits = Decimal("0")
    provider_cost_cents = 0
    provider = resolve(ctx.db, tenant_id=ctx.tenant_id, capability="video")

    for index, prompt in enumerate(scene_prompts):
        save_path = work_dir / f"seedance_scene_{index:02d}.mp4"

        def on_seedance_progress(event: dict[str, Any], scene_index: int = index) -> None:
            nonlocal last_seedance_progress, seedance_poll_ticks
            seedance_poll_ticks += 1
            progress = _seedance_i2v_poll_progress(poll_tick=seedance_poll_ticks)
            progress = max(last_seedance_progress, progress)
            last_seedance_progress = progress
            ctx.store.update(
                _scoped_id(ctx.tenant_id, ctx.task_id),
                status="running",
                progress=progress,
                step="seedance",
                stage="seedance_generating",
                frame_current=scene_index + 1,
                frame_total=scene_count,
            )

        payload = _seedance_i2v_provider_payload(
            prompt=prompt or _fallback_scene_prompt(task, index, scene_count),
            image_url=image_url,
            clip_duration=clip_duration,
            resolution=resolution,
            progress_callback=on_seedance_progress,
        )
        result = asyncio.run(provider.generate_video(payload))
        if not isinstance(result, Mapping):
            raise RuntimeError("APIMart video provider returned invalid result.")
        video_bytes = result.get("video_bytes")
        if not isinstance(video_bytes, bytes) or not video_bytes:
            raise RuntimeError(f"APIMart did not produce scene {index + 1}.")
        save_path.write_bytes(video_bytes)
        clip_credits, clip_cost_cents = _seedance_i2v_clip_billing(
            result,
            clip_duration=clip_duration,
            resolution=resolution,
        )
        provider_credits += clip_credits
        provider_cost_cents += clip_cost_cents
        scene_paths.append(str(save_path))

    concat_path = work_dir / "seedance_concat.mp4"
    concat_seedance_clips_without_audio(scene_paths, str(concat_path))
    if not concat_path.exists() or concat_path.stat().st_size <= 0:
        raise RuntimeError("Seedance scene concatenation produced an empty video.")
    ctx.base_video_bytes = concat_path.read_bytes()
    ctx.use_tts_audio = True
    ctx.seedance_billable_seconds = scene_count * clip_duration
    ctx.provider_cost_cents = (
        apimart_cost_cents_from_credits(provider_credits) + provider_cost_cents
    )
    return ctx


def subtitle_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    task = _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    timeline = getattr(ctx, "timeline", []) or []
    duration_sec = float(ctx.duration_sec or 1)
    timeline_items = _caption_timeline_items(timeline)
    subtitle_duration_sec = _subtitle_timing_duration_sec(
        task,
        timeline_items,
        duration_sec,
    )
    script = clean_spoken_script(str(task.script or task.topic or ""))
    captions, source, clause_count = _script_timed_captions(
        script,
        timeline,
        duration_sec=subtitle_duration_sec,
    )
    if not captions:
        captions = _fallback_captions(
            script,
            duration_sec=subtitle_duration_sec,
        )
        source = "script_fallback"
        clause_count = len(captions)
    lines = []
    for index, (start_ms, end_ms, text) in enumerate(captions, start=1):
        lines.append(
            f"{index}\n{_srt_time(start_ms)} --> {_srt_time(end_ms)}\n{text}\n"
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
    logger.info(
        "avatar_talk.subtitle",
        task_id=ctx.task_id,
        tenant_id=ctx.tenant_id,
        timeline_items=len(timeline),
        timeline_first_ms=timeline_items[0][0] if timeline_items else None,
        timeline_last_ms=timeline_items[-1][1] if timeline_items else None,
        audio_duration_ms=int(round(subtitle_duration_sec * 1000)),
        video_duration_ms=int(round(duration_sec * 1000)),
        clause_count=clause_count,
        caption_count=len(captions),
        cue_ranges_ms=[{"start_ms": start, "end_ms": end} for start, end, _text in captions],
        source=source,
    )
    return ctx


def _subtitle_timing_duration_sec(
    task: VideoTask,
    timeline_items: list[tuple[int, int]],
    duration_sec: float,
) -> float:
    if _task_mode(task) == "seedance_i2v" and timeline_items:
        timeline_span_ms = max(1, timeline_items[-1][1] - timeline_items[0][0])
        return timeline_span_ms / 1000
    return duration_sec


def _task_mode(task: VideoTask) -> str:
    return str(task.video_mode or task.mode or "")


def _script_timed_captions(
    script: str,
    timeline: list[dict[str, Any]],
    *,
    duration_sec: float,
) -> tuple[list[tuple[int, int, str]], str, int]:
    clauses = _script_caption_clauses(script)
    if not clauses:
        return [], "script_empty", 0

    timeline_items = _caption_timeline_items(timeline)
    duration_ms = max(1, int(round(duration_sec * 1000)))
    if (
        len(timeline_items) >= len(clauses)
        and _timeline_items_cover_duration(timeline_items, duration_sec=duration_sec)
    ):
        captions, source = _time_clauses_from_timeline(clauses, timeline_items)
        return captions, source, len(clauses)

    return (
        _time_clauses_proportionally(clauses, span_start_ms=0, span_end_ms=duration_ms),
        "script_duration",
        len(clauses),
    )


def _script_caption_clauses(script: str) -> list[str]:
    newline_marker = "\uE000"
    text = _clean_caption_text(
        script.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline_marker)
    ).replace(newline_marker, "\n")
    if not text:
        return []
    clauses: list[str] = []
    buffer: list[str] = []
    for char in text:
        if char == "\n":
            _append_caption_clause(clauses, buffer)
            continue
        buffer.append(char)
        if char in _SCRIPT_CLAUSE_ENDINGS:
            _append_caption_clause(clauses, buffer)
    _append_caption_clause(clauses, buffer)
    return clauses


def _append_caption_clause(clauses: list[str], buffer: list[str]) -> None:
    clause = _clean_caption_text("".join(buffer))
    buffer.clear()
    if clause:
        clauses.append(clause)


def _caption_timeline_items(timeline: list[dict[str, Any]]) -> list[tuple[int, int]]:
    items: list[tuple[int, int]] = []
    for item in timeline:
        raw_text = str(item.get("text") or "")
        if not _clean_caption_text(raw_text) or _is_script_clause_punctuation(raw_text):
            continue
        start_ms = int(item.get("start_ms") or 0)
        end_ms = int(item.get("end_ms") or start_ms + 1000)
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        items.append((start_ms, end_ms))
    return items


def _timeline_items_cover_duration(
    items: list[tuple[int, int]],
    *,
    duration_sec: float,
) -> bool:
    if not items:
        return False
    if duration_sec <= 0:
        return True
    return items[-1][1] >= int(duration_sec * 1000 * _MIN_TIMELINE_COVERAGE_RATIO)


def _time_clauses_from_timeline(
    clauses: list[str],
    timeline_items: list[tuple[int, int]],
) -> tuple[list[tuple[int, int, str]], str]:
    counts = [_caption_sync_len(clause) for clause in clauses]
    total = sum(counts)
    if total > 0 and len(timeline_items) == total:
        captions: list[tuple[int, int, str]] = []
        cursor = 0
        for clause, count in zip(clauses, counts, strict=True):
            if count <= 0:
                start_ms = timeline_items[min(cursor, len(timeline_items) - 1)][0]
                end_ms = start_ms + 1
            else:
                start_ms = timeline_items[cursor][0]
                end_ms = timeline_items[cursor + count - 1][1]
            captions.append((start_ms, max(start_ms + 1, end_ms), clause))
            cursor += count
        return captions, "script_timeline_exact"

    return _time_clauses_by_token_index(clauses, counts, total, timeline_items)


def _time_clauses_by_token_index(
    clauses: list[str],
    counts: list[int],
    total: int,
    timeline_items: list[tuple[int, int]],
) -> tuple[list[tuple[int, int, str]], str]:
    if total <= 0:
        return (
            _time_clauses_proportionally(
                clauses,
                span_start_ms=timeline_items[0][0],
                span_end_ms=timeline_items[-1][1],
            ),
            "script_timeline_interpolated",
        )

    token_count = len(timeline_items)
    captions: list[tuple[int, int, str]] = []
    cursor = 0
    previous_end_idx = 0
    for clause, count in zip(clauses, counts, strict=True):
        start_idx = round(cursor / total * token_count)
        cursor += count
        end_idx = round(cursor / total * token_count)
        start_idx = min(token_count - 1, max(previous_end_idx, start_idx))
        end_idx = min(token_count, max(start_idx + 1, end_idx))
        start_ms = timeline_items[start_idx][0]
        end_ms = timeline_items[end_idx - 1][1]
        captions.append((start_ms, max(start_ms + 1, end_ms), clause))
        previous_end_idx = end_idx
    return captions, "script_timeline_indexed"


def _time_clauses_proportionally(
    clauses: list[str],
    *,
    span_start_ms: int,
    span_end_ms: int,
) -> list[tuple[int, int, str]]:
    span_end_ms = max(span_start_ms + 1, span_end_ms)
    counts = [_caption_sync_len(clause) for clause in clauses]
    total = sum(counts)
    captions: list[tuple[int, int, str]] = []
    previous_end = span_start_ms
    cursor = 0
    for index, (clause, count) in enumerate(zip(clauses, counts, strict=True)):
        if total > 0:
            start_ratio = cursor / total
            cursor += count
            end_ratio = cursor / total
        else:
            start_ratio = index / len(clauses)
            end_ratio = (index + 1) / len(clauses)
        start_ms = max(previous_end, _lerp_ms(span_start_ms, span_end_ms, start_ratio))
        end_ms = max(start_ms + 1, _lerp_ms(span_start_ms, span_end_ms, end_ratio))
        captions.append((start_ms, end_ms, clause))
        previous_end = end_ms
    return captions


def _lerp_ms(start_ms: int, end_ms: int, ratio: float) -> int:
    return int(round(start_ms + (end_ms - start_ms) * ratio))


def _caption_sync_len(text: str) -> int:
    return len(
        [
            char
            for char in _clean_caption_text(text)
            if not char.isspace() and char not in _SCRIPT_CLAUSE_ENDINGS
        ]
    )


def _is_script_clause_punctuation(text: str) -> bool:
    cleaned = _clean_caption_text(text)
    return len(cleaned) == 1 and cleaned in _SCRIPT_CLAUSE_ENDINGS


def _segment_caption_words(text: str) -> list[str]:
    if not text:
        return []
    try:
        tokens = [token for token in _jieba_lcut(text) if token]
    except Exception:
        return list(text)
    if not tokens or "".join(tokens) != text:
        return list(text)
    return tokens


def _jieba_lcut(text: str) -> list[str]:
    import jieba

    for word in ("零门槛", "门槛", "高质量", "营销视频"):
        jieba.add_word(word, freq=1_000_000)
    return list(jieba.lcut(text, cut_all=False))


def _fallback_captions(text: str, *, duration_sec: float) -> list[tuple[int, int, str]]:
    segments = _split_caption_text(_clean_caption_text(text))
    if not segments:
        return []
    duration_ms = max(1000, int(duration_sec * 1000))
    captions = []
    for index, segment in enumerate(segments):
        start_ms = round(duration_ms * index / len(segments))
        end_ms = round(duration_ms * (index + 1) / len(segments))
        captions.append((start_ms, max(start_ms + 1, end_ms), segment))
    return captions


def _clean_caption_text(text: str) -> str:
    text = clean_spoken_script(text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(?<!\w)(\*|_)([^*_]+)\1(?!\w)", r"\2", text)
    text = re.sub(r"[`*_#]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_caption_text(text: str, *, max_chars: int = 42) -> list[str]:
    sentence_parts = re.findall(r"[^.!?。！？；;，,\n]+[.!?。！？；;，,]*", text)
    segments: list[str] = []
    for part in sentence_parts or [text]:
        part = part.strip()
        if not part:
            continue
        segments.extend(_split_long_caption(part, max_chars=max_chars))
    return segments


def _split_long_caption(text: str, *, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    if " " not in text:
        return [text[index : index + max_chars].strip() for index in range(0, len(text), max_chars)]
    segments: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            segments.append(current)
            current = word
    if current:
        segments.append(current)
    return segments


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
    audio_path = work_dir / "voiceover.mp3"
    output_path = work_dir / "final.mp4"
    base_path.write_bytes(base_video_bytes)
    subtitle_path.write_bytes(ctx.storage.get_bytes(subtitle_key))
    external_audio_path = None
    if getattr(ctx, "use_tts_audio", False):
        audio_key = getattr(ctx, "audio_key", None)
        if not audio_key:
            raise RuntimeError("TTS audio is missing for compose.")
        audio_path.write_bytes(ctx.storage.get_bytes(audio_key))
        external_audio_path = audio_path
    burn_kwargs = {
        "task_id": ctx.task_id,
        "producer": settings.engine_aigc_producer,
        "propagate_id": ctx.tenant_id,
    }
    subtitle_style = _task_subtitle_style(ctx)
    if subtitle_style is not None:
        burn_kwargs["subtitle_style"] = subtitle_style
    if external_audio_path is not None:
        burn_kwargs["audio_path"] = external_audio_path
    _burn_subtitles(base_path, subtitle_path, output_path, **burn_kwargs)
    ctx.final_video_bytes = output_path.read_bytes()
    return ctx


def _task_subtitle_style(ctx: AvatarTalkContext) -> dict[str, object] | None:
    if ctx.db is None:
        return None
    task = _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    raw_style = (task.params or {}).get("subtitle_style")
    if not isinstance(raw_style, Mapping):
        return None
    return resolve_subtitle_style(raw_style)


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


def _font(size: int, family: str | None = None):
    from PIL import ImageFont

    candidates = _font_candidates()
    if family:
        candidates = (family, *candidates)
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _font_candidates() -> tuple[str, ...]:
    candidates = [
        settings.engine_subtitle_font_path,
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "arial.ttf",
        "DejaVuSans.ttf",
    ]
    return tuple(dict.fromkeys(item for item in candidates if item))


def _wrap_text(text: str, *, max_width: int, draw, font) -> list[str]:
    has_whitespace = any(char.isspace() for char in text)
    units = text.split() if has_whitespace else _segment_caption_words(text)
    if not units:
        return [text]
    separator = " " if has_whitespace else ""
    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{separator}{unit}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = unit
    if current:
        lines.append(current)
    return lines


def _caption_position(
    *,
    video_w: int,
    video_h: int,
    target_size: tuple[int, int],
    band_height: int,
    position: str | None = None,
) -> tuple[str, int]:
    width, height = target_size
    if position is not None:
        margin = max(24, int(height * 0.06))
        if position == "top":
            return ("center", min(max(0, height - band_height), margin))
        if position == "center":
            return ("center", max(0, (height - band_height) // 2))
        return ("center", max(0, height - margin - band_height))

    if video_w <= 0 or video_h <= 0 or band_height <= 0:
        return ("center", max(0, min(height, int(height * 0.72))))

    scale = min(width / video_w, height / video_h)
    content_h = video_h * scale
    content_top = (height - content_h) / 2
    content_bottom = content_top + content_h
    margin = max(24, int(content_h * 0.06))
    y = int(round(content_bottom - margin - band_height))
    y = max(int(round(content_top)), y)
    y = min(max(0, height - band_height), y)
    return ("center", y)


def _compose_end_time(video_duration: float, last_caption_end: float | None) -> float:
    if video_duration <= 0 or last_caption_end is None or last_caption_end <= 0:
        return video_duration

    speech_end = min(video_duration, max(0.0, last_caption_end))
    if video_duration - speech_end <= _TAIL_TRIM_THRESHOLD_SEC:
        return video_duration

    min_duration = min(video_duration, 1.0)
    return min(
        video_duration,
        max(speech_end, min_duration, speech_end + _TAIL_KEEP_AFTER_SPEECH_SEC),
    )


def _audio_tail_fadeout_duration(
    compose_end: float,
    last_caption_end: float | None,
) -> float:
    if compose_end <= 0:
        return 0.0
    if (
        last_caption_end is not None
        and compose_end - last_caption_end >= _AUDIO_FADEOUT_SEC
    ):
        return min(_AUDIO_FADEOUT_SEC, compose_end)
    return min(_AUDIO_TAIL_FADEOUT_SEC, compose_end)


def _aigc_metadata_params(*, task_id: str, producer: str, propagate_id: str) -> list[str]:
    content_id = _metadata_text(task_id)
    producer_text = _metadata_text(producer)
    propagate = _metadata_text(propagate_id)
    label = {
        "is_ai_generated": True,
        "producer": producer_text,
        "content_id": content_id,
        "propagate_id": propagate,
    }
    return [
        "-metadata",
        f"comment=本视频由AI生成合成（AIGC）。生成服务：{producer_text}；内容编号：{content_id}",
        "-metadata",
        "description=AI-generated content (AIGC)",
        "-metadata",
        "aigc_label="
        + json.dumps(label, ensure_ascii=False, separators=(",", ":")),
    ]


def _metadata_text(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()


def _subtitle_image(
    text: str,
    *,
    width: int,
    height: int,
    style: Mapping[str, object] | None = None,
):
    from PIL import Image, ImageDraw

    if style is not None:
        return _styled_subtitle_image(text, width=width, height=height, style=style)

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
    bottom_padding = max(8, int(font_size * 0.35))
    y = max(0, image.height - text_h - bottom_padding)
    for line, line_w, line_h in zip(lines, line_widths, line_heights, strict=False):
        x = (width - line_w) // 2
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 230))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h + line_gap
    return image


def _styled_subtitle_image(
    text: str,
    *,
    width: int,
    height: int,
    style: Mapping[str, object],
):
    from PIL import Image, ImageDraw

    font_size = int(style.get("font_size") or max(18, int(height * 0.045)))
    font = _font(font_size, family=str(style.get("font_family") or ""))
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
    vertical_padding = max(8, int(font_size * 0.35))
    y = max(0, (image.height - text_h) // 2)
    background = style.get("background")
    if background:
        band_top = max(0, y - vertical_padding)
        band_bottom = min(image.height, y + text_h + vertical_padding)
        draw.rectangle(
            (0, band_top, width, band_bottom),
            fill=_rgba(str(background)),
        )
    fill = _rgba(str(style.get("color") or "#FFFFFF"))
    stroke_color = style.get("stroke_color")
    stroke_width = int(style.get("stroke_width") or 0)
    stroke_fill = _rgba(str(stroke_color)) if stroke_color else None
    for line, line_w, line_h in zip(lines, line_widths, line_heights, strict=False):
        x = (width - line_w) // 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        y += line_h + line_gap
    return image


def _rgba(value: str) -> tuple[int, int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) not in {6, 8}:
        return (255, 255, 255, 255)
    try:
        red = int(raw[0:2], 16)
        green = int(raw[2:4], 16)
        blue = int(raw[4:6], 16)
        alpha = int(raw[6:8], 16) if len(raw) == 8 else 255
        return (red, green, blue, alpha)
    except ValueError:
        return (255, 255, 255, 255)


def _burn_subtitles(
    base_video: Path,
    subtitle_file: Path,
    output: Path,
    *,
    audio_path: Path | None = None,
    target_size: tuple[int, int] = (1080, 1920),
    task_id: str | None = None,
    producer: str | None = None,
    propagate_id: str | None = None,
    subtitle_style: Mapping[str, object] | None = None,
) -> None:
    import numpy as np
    from moviepy.audio.fx.audio_fadeout import audio_fadeout
    from moviepy.editor import (
        AudioFileClip,
        ColorClip,
        CompositeVideoClip,
        ImageClip,
        VideoFileClip,
    )
    from moviepy.video.fx.freeze import freeze
    from PIL import Image

    if not hasattr(Image, "ANTIALIAS"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS

    width, height = target_size
    video = VideoFileClip(str(base_video))
    audio_clip = None
    final = None
    try:
        source_video = video
        compose_duration = float(video.duration or 0)
        if audio_path is not None:
            audio_clip = AudioFileClip(str(audio_path))
            audio_duration = max(0.0, float(audio_clip.duration or 0))
            if audio_duration > compose_duration:
                source_video = video.fx(
                    freeze,
                    t=max(0.0, compose_duration - 0.05),
                    freeze_duration=audio_duration - compose_duration,
                )
            if audio_duration > 0:
                compose_duration = audio_duration

        scale = min(width / video.w, height / video.h)
        resized = source_video.resize(scale).set_position("center")
        canvas = ColorClip(target_size, color=(0, 0, 0), duration=compose_duration)
        clips = [canvas, resized]
        captions = _parse_srt(subtitle_file)
        last_caption_end = max((end for _start, end, _text in captions), default=None)
        compose_end = (
            compose_duration
            if audio_path is not None
            else _compose_end_time(float(video.duration or 0), last_caption_end)
        )
        logger.info(
            "avatar_talk.compose",
            video_duration_ms=int(round(video.duration * 1000)),
            compose_end_ms=int(round(compose_end * 1000)),
            speech_end_ms=int(round(last_caption_end * 1000)) if last_caption_end else None,
            caption_count=len(captions),
            external_audio=audio_path is not None,
            target_width=width,
            target_height=height,
        )
        for start, end, text in captions:
            if end <= 0 or start >= compose_end:
                continue
            caption_start = max(0, start)
            caption_end = min(end, compose_end)
            subtitle_image = _subtitle_image(
                text,
                width=width,
                height=height,
                style=subtitle_style,
            )
            caption = ImageClip(
                np.array(subtitle_image),
                transparent=True,
            )
            caption = (
                caption.set_start(caption_start)
                .set_duration(max(0.1, caption_end - caption_start))
                .set_position(
                    _caption_position(
                        video_w=video.w,
                        video_h=video.h,
                        target_size=target_size,
                        band_height=subtitle_image.height,
                        position=(
                            str(subtitle_style.get("position"))
                            if subtitle_style is not None
                            else None
                        ),
                    )
                )
            )
            clips.append(caption)
        final = CompositeVideoClip(clips, size=target_size).set_duration(compose_duration)
        if audio_clip is not None:
            final = final.set_audio(audio_clip)
        elif video.audio is not None:
            final = final.set_audio(video.audio)
        if audio_clip is None and compose_end < video.duration:
            final = final.subclip(0, compose_end)
        if final.audio is not None:
            fadeout_duration = _audio_tail_fadeout_duration(compose_end, last_caption_end)
            if fadeout_duration > 0:
                final = final.set_audio(final.audio.fx(audio_fadeout, fadeout_duration))
        output.parent.mkdir(parents=True, exist_ok=True)
        final.write_videofile(
            str(output),
            fps=24,
            codec="libx264",
            audio_codec="aac",
            audio_bitrate="192k",
            audio_fps=44100,
            audio=final.audio is not None,
            ffmpeg_params=_aigc_metadata_params(
                task_id=task_id,
                producer=producer or settings.engine_aigc_producer,
                propagate_id=propagate_id or "",
            )
            if task_id
            else [],
            logger=None,
        )
    finally:
        if final is not None:
            final.close()
        if audio_clip is not None:
            audio_clip.close()
        video.close()


def upload_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    final_bytes = getattr(ctx, "final_video_bytes", b"")
    final_bytes, label_metadata = _apply_synthetic_label(
        ctx,
        final_bytes,
        kind="video",
        suffix=".mp4",
    )
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
        metadata={"synthetic_label": label_metadata},
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


ECOM_I2V_STEPS = [
    ("script", 10, script_step),
    ("tts", 25, tts_step),
    ("seedance", 88, seedance_i2v_step),
    ("subtitle", 90, subtitle_step),
    ("compose", 95, compose_step),
    ("upload", 98, upload_step),
]


def _pipeline_error_details(exc: Exception, *, fallback_code: str) -> tuple[str, str]:
    if isinstance(exc, AppError):
        return exc.code, exc.message
    return fallback_code, str(exc)


def run_avatar_talk_pipeline(*, tenant_id: str, task_id: str) -> dict[str, Any]:
    store = build_progress_store(settings.redis_url)
    storage = create_object_storage(settings)
    scoped_id = _scoped_id(tenant_id, task_id)
    with SessionLocal() as db:
        task = _task_or_raise(db, tenant_id=tenant_id, task_id=task_id)
        if task.status == "cancelled":
            store.update(
                scoped_id,
                status="cancelled",
                progress=task.progress or 0,
                step="cancelled",
            )
            return {"task_id": task_id, "status": "cancelled"}
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
            actual_seconds = max(1, int(round(ctx.duration_sec or 1)))
            provider_model = getattr(ctx, "provider_model", None)
            if provider_model == _CHANGE_LIPS_MODEL:
                cost_cents = provider_costs.omnihuman_change_lips_cost_cents(
                    actual_seconds,
                    tier=getattr(ctx, "change_lips_tier", None),
                )
            else:
                cost_cents = provider_costs.omnihuman_cost_cents(actual_seconds)
            settle_reserved_quota(
                db,
                tenant_id=tenant_id,
                video_task_id=task_id,
                actual_seconds=actual_seconds,
                cost_cents=cost_cents,
                provider="omnihuman" if provider_model else None,
                model=provider_model,
            )
            refresh_batch_job(db, batch_id=task.batch_id)
            db.commit()
            output_storage_key = task.storage_key
            prune_video_history_best_effort(
                db,
                tenant_id=tenant_id,
                mode="avatar_talk",
                storage=storage,
                keep=20,
            )
            store.update(
                scoped_id,
                status="done",
                progress=100,
                step="done",
                playback_url=(
                    storage.presign_get_url(
                        output_storage_key,
                        expires_in=settings.engine_s3_presign_ttl,
                    )
                    if output_storage_key
                    else None
                ),
                download_url=(
                    storage.presign_get_url(
                        output_storage_key,
                        expires_in=settings.engine_s3_presign_ttl,
                        download_filename=f"{task.id}.mp4",
                    )
                    if output_storage_key
                    else None
                ),
            )
            return {"task_id": task_id, "status": "done"}
        except Exception as exc:
            error_code, error_message = _pipeline_error_details(
                exc,
                fallback_code="AVATAR_TALK_FAILED",
            )
            task.status = "failed"
            task.error_code = error_code
            task.error_message = error_message
            task.error = error_message
            task.finished_at = datetime.now(UTC)
            release_reserved_quota(db, tenant_id=tenant_id, video_task_id=task_id)
            refresh_batch_job(db, batch_id=task.batch_id)
            db.commit()
            failed_progress = task.progress or 0
            prune_video_history_best_effort(
                db,
                tenant_id=tenant_id,
                mode="avatar_talk",
                storage=storage,
                keep=20,
            )
            store.update(
                scoped_id,
                status="failed",
                progress=failed_progress,
                step="failed",
                error_code=error_code,
                error_message=error_message,
            )
            raise


def run_seedance_i2v_pipeline(*, tenant_id: str, task_id: str) -> dict[str, Any]:
    store = build_progress_store(settings.redis_url)
    storage = create_object_storage(settings)
    scoped_id = _scoped_id(tenant_id, task_id)
    with SessionLocal() as db:
        task = _task_or_raise(db, tenant_id=tenant_id, task_id=task_id)
        if task.status == "cancelled":
            store.update(
                scoped_id,
                status="cancelled",
                progress=task.progress or 0,
                step="cancelled",
            )
            return {"task_id": task_id, "status": "cancelled"}
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
            for step, progress, fn in ECOM_I2V_STEPS:
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
            actual_seconds = max(
                1,
                int(round(getattr(ctx, "seedance_billable_seconds", ctx.duration_sec or 1))),
            )
            cost_cents = int(getattr(ctx, "provider_cost_cents", 0) or 0)
            if cost_cents <= 0:
                cost_cents = _seedance_i2v_fallback_cost_cents(
                    actual_seconds=actual_seconds,
                    resolution=_seedance_i2v_resolution(task.params),
                )
            settle_reserved_quota(
                db,
                tenant_id=tenant_id,
                video_task_id=task_id,
                actual_seconds=actual_seconds,
                cost_cents=cost_cents,
            )
            refresh_batch_job(db, batch_id=task.batch_id)
            db.commit()
            output_storage_key = task.storage_key
            prune_video_history_best_effort(
                db,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                storage=storage,
                keep=20,
            )
            store.update(
                scoped_id,
                status="done",
                progress=100,
                step="done",
                playback_url=(
                    storage.presign_get_url(
                        output_storage_key,
                        expires_in=settings.engine_s3_presign_ttl,
                    )
                    if output_storage_key
                    else None
                ),
                download_url=(
                    storage.presign_get_url(
                        output_storage_key,
                        expires_in=settings.engine_s3_presign_ttl,
                        download_filename=f"{task.id}.mp4",
                    )
                    if output_storage_key
                    else None
                ),
            )
            return {"task_id": task_id, "status": "done"}
        except Exception as exc:
            error_code, error_message = _pipeline_error_details(
                exc,
                fallback_code="SEEDANCE_I2V_FAILED",
            )
            task.status = "failed"
            task.error_code = error_code
            task.error_message = error_message
            task.error = error_message
            task.finished_at = datetime.now(UTC)
            release_reserved_quota(db, tenant_id=tenant_id, video_task_id=task_id)
            refresh_batch_job(db, batch_id=task.batch_id)
            db.commit()
            failed_progress = task.progress or 0
            prune_video_history_best_effort(
                db,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                storage=storage,
                keep=20,
            )
            store.update(
                scoped_id,
                status="failed",
                progress=failed_progress,
                step="failed",
                error_code=error_code,
                error_message=error_message,
            )
            raise


@celery_app.task(bind=True, name="app.workers.avatar_talk.generate")
def generate_avatar_talk_task(self, params: dict[str, Any]) -> dict[str, Any]:
    task_id = self.request.id or params.get("video_task_id") or "unknown"
    tenant_id = str(params["tenant_id"])
    claim = claim_video_task_for_worker(
        tenant_id=tenant_id,
        task_id=str(task_id),
        session_factory=SessionLocal,
    )
    if not claim.claimed:
        return {"task_id": str(task_id), "status": claim.status}
    logger.info(
        "avatar_talk.queued",
        task_id=task_id,
        tenant_id=tenant_id,
        visible_label=bool(params.get("apply_visible_label", False)),
    )
    return run_avatar_talk_pipeline(tenant_id=tenant_id, task_id=task_id)


@celery_app.task(bind=True, name="app.workers.avatar_talk.generate_seedance_i2v")
def generate_seedance_i2v_task(self, params: dict[str, Any]) -> dict[str, Any]:
    task_id = self.request.id or params.get("video_task_id") or "unknown"
    tenant_id = str(params["tenant_id"])
    claim = claim_video_task_for_worker(
        tenant_id=tenant_id,
        task_id=str(task_id),
        session_factory=SessionLocal,
    )
    if not claim.claimed:
        return {"task_id": str(task_id), "status": claim.status}
    logger.info(
        "seedance_i2v.queued",
        task_id=task_id,
        tenant_id=tenant_id,
        visible_label=bool(params.get("apply_visible_label", False)),
    )
    return run_seedance_i2v_pipeline(tenant_id=tenant_id, task_id=task_id)
