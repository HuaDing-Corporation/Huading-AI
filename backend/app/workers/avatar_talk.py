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
    parse_host_suffixes,
)
from app.services.progress import ProgressStore, build_progress_store
from app.services.quota import release_reserved_quota, settle_reserved_quota
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

_MIN_TIMELINE_COVERAGE_RATIO = 0.8
_MAX_CAPTION_CHARS = 10
_MIN_CAPTION_CHARS = 4
_MAX_CAPTION_GAP_MS = 700
_SCRIPT_CLAUSE_ENDINGS = tuple(".!?;。！？；，、")
_STRONG_CAPTION_ENDINGS = tuple(".!?。！？…")
_WEAK_CAPTION_ENDINGS = tuple(",，、;；:：")


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
    detected_duration_sec = _audio_duration_sec(Path(str(result["audio_path"])))
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
    )
    ctx.audio_key = audio_key
    ctx.timeline = result.get("timeline") or []
    ctx.duration_sec = max(1.0, detected_duration_sec)
    logger.info(
        "avatar_talk.tts",
        task_id=ctx.task_id,
        tenant_id=ctx.tenant_id,
        timeline_items=len(ctx.timeline),
        duration_sec=ctx.duration_sec,
    )
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


def subtitle_step(ctx: AvatarTalkContext) -> AvatarTalkContext:
    task = _task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    timeline = getattr(ctx, "timeline", []) or []
    duration_sec = float(ctx.duration_sec or 1)
    script = str(task.script or task.topic or "")
    captions, source, clause_count = _script_timed_captions(
        script,
        timeline,
        duration_sec=duration_sec,
    )
    if not captions:
        captions = _fallback_captions(
            script,
            duration_sec=duration_sec,
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
    timeline_items = _caption_timeline_items(timeline)
    logger.info(
        "avatar_talk.subtitle",
        task_id=ctx.task_id,
        tenant_id=ctx.tenant_id,
        timeline_items=len(timeline),
        timeline_first_ms=timeline_items[0][0] if timeline_items else None,
        timeline_last_ms=timeline_items[-1][1] if timeline_items else None,
        audio_duration_ms=int(round(duration_sec * 1000)),
        clause_count=clause_count,
        caption_count=len(captions),
        cue_ranges_ms=[{"start_ms": start, "end_ms": end} for start, end, _text in captions],
        source=source,
    )
    return ctx


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


def _timeline_captions(timeline: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    return _group_word_timeline(timeline)


def _group_word_timeline(timeline: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    words: list[tuple[int, int, str]] = []
    for item in timeline:
        raw_text = str(item.get("text") or "")
        if not _clean_caption_text(raw_text):
            continue
        start_ms = int(item.get("start_ms") or 0)
        end_ms = int(item.get("end_ms") or start_ms + 1000)
        if end_ms <= start_ms:
            end_ms = start_ms + 1000
        words.append((start_ms, end_ms, raw_text))
    words = _timeline_caption_words(words)

    captions: list[tuple[int, int, str]] = []
    group_start = 0
    group_end = 0
    group_texts: list[str] = []

    def group_text() -> str:
        return "".join(group_texts)

    def flush_group() -> None:
        nonlocal group_start, group_end, group_texts
        text = _clean_caption_text(group_text())
        if text:
            captions.append((group_start, max(group_start + 1, group_end), text))
        group_start = 0
        group_end = 0
        group_texts = []

    for index, (start_ms, end_ms, text) in enumerate(words):
        next_text = words[index + 1][2] if index + 1 < len(words) else ""
        candidate_len = _caption_visible_len(group_text() + text)
        if (
            group_texts
            and _is_caption_punctuation(next_text)
            and candidate_len >= _MAX_CAPTION_CHARS
        ):
            flush_group()
        elif group_texts and candidate_len > _MAX_CAPTION_CHARS:
            flush_group()

        if not group_texts:
            group_start = start_ms
        group_texts.append(text)
        group_end = end_ms

        visible_len = _caption_visible_len(group_text())
        next_start = words[index + 1][0] if index + 1 < len(words) else None
        gap_to_next = next_start - end_ms if next_start is not None else 0
        if (
            _ends_with(group_text(), _STRONG_CAPTION_ENDINGS)
            or (
                _ends_with(group_text(), _WEAK_CAPTION_ENDINGS)
                and visible_len >= _MIN_CAPTION_CHARS
            )
            or visible_len >= _MAX_CAPTION_CHARS
            or gap_to_next > _MAX_CAPTION_GAP_MS
        ):
            flush_group()

    if group_texts:
        flush_group()
    return captions


def _timeline_caption_words(
    words: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    if not _looks_character_level(words):
        return words

    text = "".join(word for _start_ms, _end_ms, word in words)
    tokens = _segment_caption_words(text)
    if not tokens:
        return words

    grouped: list[tuple[int, int, str]] = []
    cursor = 0
    for token in tokens:
        token_len = len(token)
        if token_len <= 0 or cursor + token_len > len(words):
            return words
        token_words = words[cursor : cursor + token_len]
        if "".join(word for _start_ms, _end_ms, word in token_words) != token:
            return words
        grouped.append((token_words[0][0], token_words[-1][1], token))
        cursor += token_len

    if cursor != len(words):
        return words
    return grouped


def _looks_character_level(words: list[tuple[int, int, str]]) -> bool:
    visible_words = [word for _start_ms, _end_ms, word in words if _caption_visible_len(word)]
    if len(visible_words) < 2:
        return False
    single_units = sum(1 for word in visible_words if _caption_visible_len(word) <= 1)
    return single_units / len(visible_words) >= 0.8


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


def _caption_visible_len(text: str) -> int:
    return len(re.sub(r"\s+", "", _clean_caption_text(text)))


def _ends_with(text: str, endings: tuple[str, ...]) -> bool:
    return _clean_caption_text(text).endswith(endings)


def _is_caption_punctuation(text: str) -> bool:
    cleaned = _clean_caption_text(text)
    return len(cleaned) == 1 and cleaned.endswith(
        _STRONG_CAPTION_ENDINGS + _WEAK_CAPTION_ENDINGS
    )


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

    for name in _font_candidates():
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
) -> tuple[str, int]:
    width, height = target_size
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
    bottom_padding = max(8, int(font_size * 0.35))
    y = max(0, image.height - text_h - bottom_padding)
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
        captions = _parse_srt(subtitle_file)
        logger.info(
            "avatar_talk.compose",
            video_duration_ms=int(round(video.duration * 1000)),
            caption_count=len(captions),
            target_width=width,
            target_height=height,
        )
        for start, end, text in captions:
            if end <= 0 or start >= video.duration:
                continue
            subtitle_image = _subtitle_image(text, width=width, height=height)
            caption = ImageClip(
                np.array(subtitle_image),
                transparent=True,
            )
            caption = (
                caption.set_start(max(0, start))
                .set_duration(max(0.1, min(end, video.duration) - max(0, start)))
                .set_position(
                    _caption_position(
                        video_w=video.w,
                        video_h=video.h,
                        target_size=target_size,
                        band_height=subtitle_image.height,
                    )
                )
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
