from __future__ import annotations

import asyncio
import base64
import inspect
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from requests import RequestException
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ReversePromptJob
from app.db.session import SessionLocal
from app.providers.base import resolve
from app.providers.reverse_prompt.apimart_gemini import (
    APIMartGeminiReversePromptError,
)
from app.services import quota
from app.services.apimart_costs import apimart_cost_cents_from_credits
from app.services.reverse_prompt import (
    mark_reverse_prompt_job_failed,
    mark_reverse_prompt_job_succeeded,
    nonnegative_int,
    set_reverse_prompt_segment_progress,
    source_asset_or_raise,
)
from app.services.reverse_prompt_usage import (
    begin_reverse_prompt_usage_capture,
    capture_reverse_prompt_usage,
    finish_reverse_prompt_usage_capture,
    reverse_prompt_usage_checkpoint,
    reverse_prompt_usage_since,
)
from app.services.storage.factory import create_object_storage
from app.services.storage.keys import get_tenant_storage_bytes

_SHORT_VIDEO_MAX_DURATION_SEC = 60.0
_MAX_VIDEO_DURATION_SEC = 180.0
_LONG_VIDEO_SEGMENT_SEC = 30.0
_LONG_VIDEO_FRAMES_PER_SEGMENT = 6
_FRAME_MAX_EDGE = 768
_FRAME_TIMEOUT_SEC = 30.0
_PROXY_TIMEOUT_SEC = 180.0
_AUDIO_TIMEOUT_SEC = 60.0
_SCENE_CUT_THRESHOLD = 0.25
_SCENE_CUT_SNAP_TOLERANCE_SEC = 1.25
_PTS_TIME_RE = re.compile(r"\bpts_time:([0-9]+(?:\.[0-9]+)?)")
logger = get_logger(__name__)


class ReversePromptVideoProcessingError(RuntimeError):
    pass


class _NativeVideoFallback(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        cause: Exception,
        usage_results: list[Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.cause = cause
        self.usage_results = tuple(
            dict(item) for item in (usage_results or [])
        )


class _ProviderCallFailure(RuntimeError):
    def __init__(
        self,
        *,
        cause: Exception,
        usage_results: list[Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.usage_results = tuple(
            dict(item) for item in (usage_results or [])
        )


_NATIVE_RUNTIME_ERRORS = (
    APIMartGeminiReversePromptError,
    _ProviderCallFailure,
    ReversePromptVideoProcessingError,
    RequestException,
    subprocess.SubprocessError,
    OSError,
)
_DIRECT_FAILURE_ERRORS = (
    AttributeError,
    TypeError,
    KeyError,
    AssertionError,
    SQLAlchemyError,
)
_FALLBACK_EVENTS_KEY = "_fallback_events"


@dataclass(frozen=True)
class VideoSegment:
    index: int
    start_sec: float
    end_sec: float
    timestamps_sec: tuple[float, ...]


def frame_timestamps(duration_sec: float) -> list[float]:
    duration = float(duration_sec)
    if duration < 1.0 or duration > _SHORT_VIDEO_MAX_DURATION_SEC:
        raise ReversePromptVideoProcessingError(
            "Reverse prompt video must be between 1 and 60 seconds."
        )
    frame_count = 8 if duration <= 30.0 else 12
    return [round((index + 0.5) * duration / frame_count, 6) for index in range(frame_count)]


def video_segment_plan(
    duration_sec: float,
    *,
    segment_seconds: float = _LONG_VIDEO_SEGMENT_SEC,
) -> list[VideoSegment]:
    duration = float(duration_sec)
    if duration <= _SHORT_VIDEO_MAX_DURATION_SEC or duration > _MAX_VIDEO_DURATION_SEC:
        raise ReversePromptVideoProcessingError(
            "Long reverse prompt video must be over 60 and at most 180 seconds."
        )
    segment_length = float(segment_seconds)
    if segment_length <= 0 or segment_length > _SHORT_VIDEO_MAX_DURATION_SEC:
        raise ReversePromptVideoProcessingError(
            "Reverse prompt native segment length must be between 1 and 60 seconds."
        )
    segments: list[VideoSegment] = []
    start_sec = 0.0
    while start_sec < duration:
        end_sec = min(duration, start_sec + segment_length)
        segment_duration = end_sec - start_sec
        timestamps = tuple(
            round(
                start_sec
                + (position + 0.5)
                * segment_duration
                / _LONG_VIDEO_FRAMES_PER_SEGMENT,
                6,
            )
            for position in range(_LONG_VIDEO_FRAMES_PER_SEGMENT)
        )
        segments.append(
            VideoSegment(
                index=len(segments) + 1,
                start_sec=start_sec,
                end_sec=end_sec,
                timestamps_sec=timestamps,
            )
        )
        start_sec = end_sec
    return segments


def extract_uniform_video_frames(
    video_path: Path,
    *,
    duration_sec: float,
    run: Callable[..., Any] = subprocess.run,
) -> list[bytes]:
    return extract_video_frames_at_timestamps(
        video_path,
        timestamps_sec=frame_timestamps(duration_sec),
        run=run,
    )


def extract_video_frames_at_timestamps(
    video_path: Path,
    *,
    timestamps_sec: list[float] | tuple[float, ...],
    run: Callable[..., Any] = subprocess.run,
) -> list[bytes]:
    frames: list[bytes] = []
    for timestamp in timestamps_sec:
        frame = b""
        stderr = ""
        seek_attempts = (float(timestamp), max(0.0, float(timestamp) - 1.0))
        for seek_timestamp in dict.fromkeys(seek_attempts):
            command = [
                os.environ.get("FFMPEG_BINARY", "ffmpeg"),
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{seek_timestamp:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                (
                    f"scale={_FRAME_MAX_EDGE}:{_FRAME_MAX_EDGE}:"
                    "force_original_aspect_ratio=decrease"
                ),
                "-q:v",
                "3",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-",
            ]
            result = run(
                command,
                capture_output=True,
                check=False,
                timeout=_FRAME_TIMEOUT_SEC,
            )
            frame = bytes(result.stdout or b"")
            if result.returncode == 0 and frame:
                break
            stderr = bytes(result.stderr or b"").decode("utf-8", errors="replace")
            frame = b""
        if not frame:
            raise ReversePromptVideoProcessingError(
                f"Failed to extract video frame at {timestamp:.3f}s: {stderr[:500]}"
            )
        frames.append(frame)
    return frames


def build_video_analysis_proxy(
    video_path: Path,
    *,
    start_sec: float = 0.0,
    end_sec: float | None = None,
    max_edge: int | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> bytes:
    start = float(start_sec)
    end = float(end_sec) if end_sec is not None else None
    edge = int(max_edge or settings.engine_reverse_prompt_video_proxy_max_edge)
    if start < 0 or (end is not None and end <= start):
        raise ReversePromptVideoProcessingError("Video proxy bounds are invalid.")
    if edge < 2:
        raise ReversePromptVideoProcessingError("Video proxy max edge is invalid.")

    with NamedTemporaryFile(delete=False, suffix=".mp4") as proxy_file:
        proxy_path = Path(proxy_file.name)
    try:
        command = [
            os.environ.get("FFMPEG_BINARY", "ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
        ]
        if start > 0:
            command.extend(["-ss", f"{start:.3f}"])
        command.extend(["-i", str(video_path)])
        if end is not None:
            command.extend(["-t", f"{end - start:.3f}"])
        command.extend(
            [
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                (
                    f"scale={edge}:{edge}:force_original_aspect_ratio=decrease:"
                    "force_divisible_by=2"
                ),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "26",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(proxy_path),
            ]
        )
        result = run(
            command,
            capture_output=True,
            check=False,
            timeout=_PROXY_TIMEOUT_SEC,
        )
        proxy_bytes = proxy_path.read_bytes() if proxy_path.exists() else b""
        if result.returncode != 0 or not proxy_bytes:
            stderr = bytes(result.stderr or b"").decode("utf-8", errors="replace")
            raise ReversePromptVideoProcessingError(
                f"Failed to build reverse prompt video proxy: {stderr[:500]}"
            )
        return proxy_bytes
    finally:
        proxy_path.unlink(missing_ok=True)


def detect_video_scene_cuts(
    video_path: Path,
    *,
    duration_sec: float,
    run: Callable[..., Any] = subprocess.run,
) -> list[float]:
    duration = float(duration_sec)
    if duration <= 0:
        raise ReversePromptVideoProcessingError(
            "Scene-cut detection duration is invalid."
        )
    command = [
        os.environ.get("FFMPEG_BINARY", "ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        (
            "scale=320:-2,"
            f"select='gt(scene,{_SCENE_CUT_THRESHOLD})',showinfo"
        ),
        "-f",
        "null",
        "-",
    ]
    result = run(
        command,
        capture_output=True,
        check=False,
        timeout=_PROXY_TIMEOUT_SEC,
    )
    if result.returncode != 0:
        stderr = bytes(result.stderr or b"").decode("utf-8", errors="replace")
        raise ReversePromptVideoProcessingError(
            f"Failed to detect reverse prompt scene cuts: {stderr[:500]}"
        )
    stderr = bytes(result.stderr or b"").decode("utf-8", errors="replace")
    cuts = sorted(
        {
            round(float(match.group(1)), 6)
            for match in _PTS_TIME_RE.finditer(stderr)
            if 0.05 < float(match.group(1)) < duration - 0.05
        }
    )
    return cuts


def refine_video_timeline_with_scene_cuts(
    video_analysis: dict[str, Any],
    *,
    scene_cuts_sec: list[float] | tuple[float, ...],
    start_sec: float,
    end_sec: float,
) -> None:
    raw_shots = video_analysis.get("shot_list")
    if not isinstance(raw_shots, list) or not raw_shots:
        return
    shots = [shot for shot in raw_shots if isinstance(shot, dict)]
    if len(shots) != len(raw_shots):
        return
    available = sorted(
        {
            float(value)
            for value in scene_cuts_sec
            if start_sec < float(value) < end_sec
        }
    )
    used: set[float] = set()
    previous_boundary = float(start_sec)
    shots[0]["start_sec"] = float(start_sec)
    for position in range(len(shots) - 1):
        left = shots[position]
        right = shots[position + 1]
        try:
            predicted = (
                float(left.get("end_sec")) + float(right.get("start_sec"))
            ) / 2.0
        except (TypeError, ValueError):
            continue
        candidates = [
            cut
            for cut in available
            if cut not in used
            and cut > previous_boundary
            and abs(cut - predicted) <= _SCENE_CUT_SNAP_TOLERANCE_SEC
        ]
        if not candidates:
            previous_boundary = max(previous_boundary, predicted)
            continue
        boundary = min(candidates, key=lambda cut: abs(cut - predicted))
        left["end_sec"] = boundary
        right["start_sec"] = boundary
        used.add(boundary)
        previous_boundary = boundary
    shots[-1]["end_sec"] = float(end_sec)


def extract_audio_track(
    video_path: Path,
    *,
    run: Callable[..., Any] = subprocess.run,
) -> bytes | None:
    command = [
        os.environ.get("FFMPEG_BINARY", "ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-map",
        "0:a:0?",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-codec:a",
        "libmp3lame",
        "-f",
        "mp3",
        "-",
    ]
    result = run(
        command,
        capture_output=True,
        check=False,
        timeout=_AUDIO_TIMEOUT_SEC,
    )
    audio = bytes(result.stdout or b"")
    if audio:
        return audio
    stderr = bytes(result.stderr or b"").decode("utf-8", errors="replace")
    if "does not contain any stream" in stderr or "matches no streams" in stderr:
        return None
    if result.returncode != 0:
        raise ReversePromptVideoProcessingError(
            f"Failed to extract reverse prompt audio: {stderr[:500]}"
        )
    return None


def claim_reverse_prompt_video_job(db, *, job_id: str) -> bool:
    claimed = db.execute(
        update(ReversePromptJob)
        .where(
            ReversePromptJob.id == job_id,
            ReversePromptJob.source_kind == "video",
            ReversePromptJob.status == "queued",
        )
        .values(
            status="running",
            error_code=None,
            error_message=None,
            updated_at=datetime.now(UTC),
        )
    )
    db.commit()
    return claimed.rowcount == 1


def run_reverse_prompt_video_job(
    job_id: str,
    *,
    session_factory=SessionLocal,
) -> dict[str, str]:
    temp_path: Path | None = None
    fallback_events: list[dict[str, object]] = []
    with session_factory() as db:
        job = db.get(ReversePromptJob, job_id)
        if job is None or job.source_kind != "video":
            raise ReversePromptVideoProcessingError("Reverse prompt video job not found.")
        if job.status != "queued":
            return {"job_id": job.id, "status": job.status}
        if not claim_reverse_prompt_video_job(db, job_id=job.id):
            db.expire_all()
            current = db.get(ReversePromptJob, job_id)
            status = current.status if current is not None else "missing"
            return {"job_id": job_id, "status": status}
        db.refresh(job)
        usage_token, captured_usage = begin_reverse_prompt_usage_capture()

        try:
            source_kind, source = source_asset_or_raise(
                db,
                tenant_id=job.tenant_id,
                asset_id=str(job.source_asset_id or ""),
            )
            if source_kind != "video":
                raise ReversePromptVideoProcessingError("Reverse prompt source is not a video.")
            storage = create_object_storage(settings)
            video_bytes = get_tenant_storage_bytes(
                storage,
                tenant_id=job.tenant_id,
                storage_key=source.storage_key,
            )
            if not video_bytes:
                raise ReversePromptVideoProcessingError("Reverse prompt video is empty.")
            with NamedTemporaryFile(delete=False, suffix=".mp4") as temp_file:
                temp_file.write(video_bytes)
                temp_path = Path(temp_file.name)

            duration_sec = float(source.duration_ms or 0) / 1000.0
            provider = resolve(db, tenant_id=job.tenant_id, capability="reverse_prompt")
            audio_transcript, _ = _best_effort_audio_transcript(
                provider,
                temp_path,
            )
            if duration_sec <= _SHORT_VIDEO_MAX_DURATION_SEC:
                result, _ = _run_short_video_analysis(
                    provider,
                    temp_path,
                    job=job,
                    duration_sec=duration_sec,
                    target_format=job.target_format,
                    audio_transcript=audio_transcript,
                    fallback_events=fallback_events,
                )
            else:
                result, _ = _run_long_video_analysis(
                    db,
                    job=job,
                    provider=provider,
                    video_path=temp_path,
                    duration_sec=duration_sec,
                    target_format=job.target_format,
                    audio_transcript=audio_transcript,
                    fallback_events=fallback_events,
                )
            result = _aggregate_provider_usage(result, captured_usage)
            _attach_fallback_events(result, fallback_events)
            if not isinstance(result.get("video_analysis"), dict):
                raise ReversePromptVideoProcessingError(
                    "Reverse prompt provider returned no video analysis."
                )
            mark_reverse_prompt_job_succeeded(db, job=job, result=result)
            total_tokens = nonnegative_int(result.get("total_tokens")) or (
                nonnegative_int(result.get("prompt_tokens"))
                + nonnegative_int(result.get("completion_tokens"))
            )
            quota.settle_reverse_prompt_video_quota(
                db,
                tenant_id=job.tenant_id,
                reverse_prompt_job_id=job.id,
                provider=str(result.get("provider") or "apimart"),
                model=str(
                    result.get("model")
                    or (
                        settings.engine_apimart_reverse_prompt_video_model
                        if settings.engine_reverse_prompt_video_analysis_mode == "native"
                        else settings.engine_apimart_reverse_prompt_model
                    )
                ),
                total_tokens=total_tokens,
                cost_cents=nonnegative_int(result.get("cost_cents")),
            )
            db.commit()
            return {"job_id": job.id, "status": "succeeded"}
        except Exception as exc:
            db.rollback()
            failed_job = db.get(ReversePromptJob, job_id)
            if failed_job is not None:
                failed_job.result_json = None
                failed_job.raw_model_json = (
                    _fallback_event_payload(fallback_events)
                    if fallback_events
                    else None
                )
                quota.release_reverse_prompt_video_quota(
                    db,
                    tenant_id=failed_job.tenant_id,
                    reverse_prompt_job_id=failed_job.id,
                )
                mark_reverse_prompt_job_failed(
                    db,
                    job=failed_job,
                    message="Reverse prompt video processing failed.",
                )
            logger.exception("reverse_prompt_video_failed", job_id=job_id)
            if isinstance(exc, _DIRECT_FAILURE_ERRORS):
                raise
            raise ReversePromptVideoProcessingError(
                "Reverse prompt video processing failed."
            ) from exc
        finally:
            finish_reverse_prompt_usage_capture(usage_token)
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def _run_native_stage(
    stage: str,
    operation: Callable[[], Any],
    *,
    usage_results: list[Mapping[str, Any]] | None = None,
) -> Any:
    usage_checkpoint = reverse_prompt_usage_checkpoint()
    try:
        return operation()
    except _NATIVE_RUNTIME_ERRORS as exc:
        captured_usage = reverse_prompt_usage_since(usage_checkpoint)
        raise _NativeVideoFallback(
            stage=stage,
            cause=exc,
            usage_results=captured_usage
            or [
                *(usage_results or []),
                *_usage_results_from_exception(exc),
            ],
        ) from exc


def _usage_results_from_exception(exc: Exception) -> list[Mapping[str, Any]]:
    raw_usage = getattr(exc, "usage_results", ())
    if not isinstance(raw_usage, list | tuple):
        return []
    return [dict(item) for item in raw_usage if isinstance(item, Mapping)]


def _fallback_reason(exc: Exception) -> str:
    root_cause = getattr(exc, "cause", exc)
    error_type = str(
        getattr(root_cause, "error_type", "") or ""
    ).strip()
    return error_type or type(root_cause).__name__


def _record_native_fallback(
    *,
    job: ReversePromptJob,
    fallback_events: list[dict[str, object]],
    scope: str,
    fallback: _NativeVideoFallback,
    segment_index: int | None = None,
) -> None:
    incurred_cost_cents = sum(
        nonnegative_int(item.get("cost_cents"))
        for item in fallback.usage_results
    )
    log_fields: dict[str, object] = {
        "job_id": job.id,
        "tenant_id": job.tenant_id,
        "scope": scope,
        "stage": fallback.stage,
        "reason": _fallback_reason(fallback.cause),
        "from_model": settings.engine_apimart_reverse_prompt_video_model,
        "to_model": settings.engine_apimart_reverse_prompt_model,
        "segment_index": segment_index,
        "incurred_cost_cents": incurred_cost_cents,
    }
    fallback_events.append(
        {
            "occurred_at": datetime.now(UTC).isoformat(),
            **log_fields,
        }
    )
    logger.warning("reverse_prompt_native_video_fallback", **log_fields)


def _attach_fallback_events(
    result: dict[str, Any],
    fallback_events: list[dict[str, object]],
) -> None:
    if not fallback_events:
        return
    raw_model_json = result.get("raw_model_json")
    persisted_raw = (
        dict(raw_model_json)
        if isinstance(raw_model_json, Mapping)
        else {}
    )
    persisted_raw.update(_fallback_event_payload(fallback_events))
    result["raw_model_json"] = persisted_raw


def _fallback_event_payload(
    fallback_events: list[dict[str, object]],
) -> dict[str, object]:
    return {
        _FALLBACK_EVENTS_KEY: [
            dict(event) for event in fallback_events
        ]
    }


def _run_short_video_analysis(
    provider: Any,
    video_path: Path,
    *,
    job: ReversePromptJob,
    duration_sec: float,
    target_format: str,
    audio_transcript: str | None,
    fallback_events: list[dict[str, object]],
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    if settings.engine_reverse_prompt_video_analysis_mode == "frames":
        return _run_short_video_frames_analysis(
            provider,
            video_path,
            duration_sec=duration_sec,
            target_format=target_format,
            audio_transcript=audio_transcript,
        )
    try:
        return _run_short_video_native_analysis(
            provider,
            video_path,
            duration_sec=duration_sec,
            target_format=target_format,
            audio_transcript=audio_transcript,
        )
    except _NativeVideoFallback as exc:
        _record_native_fallback(
            job=job,
            fallback_events=fallback_events,
            scope="short",
            fallback=exc,
        )
        frame_result, frame_usage = _run_short_video_frames_analysis(
            provider,
            video_path,
            duration_sec=duration_sec,
            target_format=target_format,
            audio_transcript=audio_transcript,
        )
        return frame_result, [*exc.usage_results, *frame_usage]


def _run_short_video_native_analysis(
    provider: Any,
    video_path: Path,
    *,
    duration_sec: float,
    target_format: str,
    audio_transcript: str | None,
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    scene_cuts = _run_native_stage(
        "scene_detection",
        lambda: detect_video_scene_cuts(
            video_path,
            duration_sec=duration_sec,
        ),
    )
    proxy_bytes = _run_native_stage(
        "proxy_transcode",
        lambda: build_video_analysis_proxy(
            video_path,
            max_edge=settings.engine_reverse_prompt_video_proxy_max_edge,
        ),
    )
    result = _run_native_stage(
        "provider_call",
        lambda: _call_provider(
            lambda: provider.reverse_video_native(
                {
                    "video_bytes": proxy_bytes,
                    "duration_sec": duration_sec,
                    "target_format": target_format,
                }
            ),
        ),
    )
    _run_native_stage(
        "result_validation",
        lambda: _finalize_short_native_result(
            result,
            duration_sec=duration_sec,
            audio_transcript=audio_transcript,
            scene_cuts=scene_cuts,
        ),
        usage_results=[result],
    )
    return result, [result]


def _finalize_short_native_result(
    result: dict[str, Any],
    *,
    duration_sec: float,
    audio_transcript: str | None,
    scene_cuts: list[float],
) -> None:
    _attach_audio_context(
        result,
        duration_sec=duration_sec,
        audio_transcript=audio_transcript,
    )
    refine_video_timeline_with_scene_cuts(
        result["video_analysis"],
        scene_cuts_sec=scene_cuts,
        start_sec=0.0,
        end_sec=duration_sec,
    )


def _run_short_video_frames_analysis(
    provider: Any,
    video_path: Path,
    *,
    duration_sec: float,
    target_format: str,
    audio_transcript: str | None,
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    timestamps_sec = frame_timestamps(duration_sec)
    frames = extract_uniform_video_frames(
        video_path,
        duration_sec=duration_sec,
    )
    result = _call_provider(
        lambda: provider.reverse_video_frames(
            {
                "image_urls": _frame_data_urls(frames),
                "timestamps_sec": timestamps_sec,
                "duration_sec": duration_sec,
                "target_format": target_format,
            }
        ),
    )
    _attach_audio_context(
        result,
        duration_sec=duration_sec,
        audio_transcript=audio_transcript,
    )
    return result, [result]


def _attach_audio_context(
    result: dict[str, Any],
    *,
    duration_sec: float,
    audio_transcript: str | None,
) -> None:
    video_analysis = result.get("video_analysis")
    if not isinstance(video_analysis, Mapping):
        raise ReversePromptVideoProcessingError(
            "Reverse prompt provider returned no video analysis."
        )
    normalized_analysis = dict(video_analysis)
    normalized_analysis["duration_sec"] = duration_sec
    normalized_analysis["audio_transcript"] = audio_transcript
    # SPIKE proved the transcription endpoint cannot classify BGM style.
    normalized_analysis["bgm_style"] = None
    result["video_analysis"] = normalized_analysis


def _run_long_video_analysis(
    db,
    *,
    job: ReversePromptJob,
    provider: Any,
    video_path: Path,
    duration_sec: float,
    target_format: str,
    audio_transcript: str | None,
    fallback_events: list[dict[str, object]],
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    if settings.engine_reverse_prompt_video_analysis_mode == "frames":
        return _run_long_video_frames_analysis(
            db,
            job=job,
            provider=provider,
            video_path=video_path,
            duration_sec=duration_sec,
            target_format=target_format,
            audio_transcript=audio_transcript,
        )
    return _run_long_video_native_analysis(
        db,
        job=job,
        provider=provider,
        video_path=video_path,
        duration_sec=duration_sec,
        target_format=target_format,
        audio_transcript=audio_transcript,
        fallback_events=fallback_events,
    )


def _run_long_video_native_analysis(
    db,
    *,
    job: ReversePromptJob,
    provider: Any,
    video_path: Path,
    duration_sec: float,
    target_format: str,
    audio_transcript: str | None,
    fallback_events: list[dict[str, object]],
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    try:
        scene_cuts = _run_native_stage(
            "scene_detection",
            lambda: detect_video_scene_cuts(
                video_path,
                duration_sec=duration_sec,
            ),
        )
    except _NativeVideoFallback as fallback:
        _record_native_fallback(
            job=job,
            fallback_events=fallback_events,
            scope="long",
            fallback=fallback,
        )
        frame_result, frame_usage = _run_long_video_frames_analysis(
            db,
            job=job,
            provider=provider,
            video_path=video_path,
            duration_sec=duration_sec,
            target_format=target_format,
            audio_transcript=audio_transcript,
        )
        return frame_result, [*fallback.usage_results, *frame_usage]
    segments = video_segment_plan(
        duration_sec,
        segment_seconds=settings.engine_reverse_prompt_video_native_segment_seconds,
    )
    set_reverse_prompt_segment_progress(
        db,
        job=job,
        segments_total=len(segments),
        segments_done=0,
    )
    segment_analyses: list[dict[str, Any]] = []
    usage_results: list[Mapping[str, Any]] = []
    raw_segments: list[dict[str, object]] = []
    for segment in segments:
        try:
            proxy_bytes = _run_native_stage(
                "proxy_transcode",
                lambda segment=segment: build_video_analysis_proxy(
                    video_path,
                    start_sec=segment.start_sec,
                    end_sec=segment.end_sec,
                    max_edge=settings.engine_reverse_prompt_video_proxy_max_edge,
                ),
            )
            payload = {
                "video_bytes": proxy_bytes,
                "duration_sec": duration_sec,
                "segment_index": segment.index,
                "segment_start_sec": segment.start_sec,
                "segment_end_sec": segment.end_sec,
                "target_format": target_format,
            }
            segment_result = _run_native_stage(
                "provider_call",
                lambda payload=payload, segment=segment: _segment_result_with_retry(
                    provider,
                    payload,
                    segment.index,
                    operation_name="analyze_video_native_segment",
                ),
            )
            normalized_segment = _run_native_stage(
                "timeline_validation",
                lambda segment=segment, segment_result=segment_result: _normalize_video_segment(
                    segment=segment,
                    segment_result=segment_result,
                    scene_cuts_sec=scene_cuts,
                ),
                usage_results=[segment_result],
            )
        except _NativeVideoFallback as fallback:
            _record_native_fallback(
                job=job,
                fallback_events=fallback_events,
                scope="long",
                fallback=fallback,
                segment_index=segment.index,
            )
            usage_results.extend(fallback.usage_results)
            segment_result = _run_frame_video_segment(
                provider,
                video_path,
                duration_sec=duration_sec,
                target_format=target_format,
                segment=segment,
            )
            normalized_segment = _normalize_video_segment(
                segment=segment,
                segment_result=segment_result,
            )
        _record_video_segment(
            db,
            job=job,
            segment=segment,
            segments_total=len(segments),
            segment_result=segment_result,
            normalized_segment=normalized_segment,
            segment_analyses=segment_analyses,
            usage_results=usage_results,
            raw_segments=raw_segments,
        )
    return _summarize_native_video_segments(
        provider,
        job=job,
        fallback_events=fallback_events,
        duration_sec=duration_sec,
        target_format=target_format,
        audio_transcript=audio_transcript,
        segment_analyses=segment_analyses,
        usage_results=usage_results,
        raw_segments=raw_segments,
        model=settings.engine_apimart_reverse_prompt_video_model,
        scene_cuts_sec=scene_cuts,
    )


def _run_long_video_frames_analysis(
    db,
    *,
    job: ReversePromptJob,
    provider: Any,
    video_path: Path,
    duration_sec: float,
    target_format: str,
    audio_transcript: str | None,
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    segments = video_segment_plan(duration_sec)
    set_reverse_prompt_segment_progress(
        db,
        job=job,
        segments_total=len(segments),
        segments_done=0,
    )
    segment_analyses: list[dict[str, Any]] = []
    usage_results: list[Mapping[str, Any]] = []
    raw_segments: list[dict[str, object]] = []
    for segment in segments:
        segment_result = _run_frame_video_segment(
            provider,
            video_path,
            duration_sec=duration_sec,
            target_format=target_format,
            segment=segment,
        )
        normalized_segment = _normalize_video_segment(
            segment=segment,
            segment_result=segment_result,
        )
        _record_video_segment(
            db,
            job=job,
            segment=segment,
            segments_total=len(segments),
            segment_result=segment_result,
            normalized_segment=normalized_segment,
            segment_analyses=segment_analyses,
            usage_results=usage_results,
            raw_segments=raw_segments,
        )
    return _summarize_video_segments(
        provider,
        duration_sec=duration_sec,
        target_format=target_format,
        audio_transcript=audio_transcript,
        segment_analyses=segment_analyses,
        usage_results=usage_results,
        raw_segments=raw_segments,
    )


def _run_frame_video_segment(
    provider: Any,
    video_path: Path,
    *,
    duration_sec: float,
    target_format: str,
    segment: VideoSegment,
) -> dict[str, Any]:
    frames = extract_video_frames_at_timestamps(
        video_path,
        timestamps_sec=segment.timestamps_sec,
    )
    payload = {
        "image_urls": _frame_data_urls(frames),
        "timestamps_sec": list(segment.timestamps_sec),
        "duration_sec": duration_sec,
        "segment_index": segment.index,
        "segment_start_sec": segment.start_sec,
        "segment_end_sec": segment.end_sec,
        "target_format": target_format,
    }
    return _segment_result_with_retry(provider, payload, segment.index)


def _normalize_video_segment(
    *,
    segment: VideoSegment,
    segment_result: Mapping[str, Any],
    scene_cuts_sec: list[float] | None = None,
) -> dict[str, Any]:
    segment_analysis = segment_result.get("segment_analysis")
    if not isinstance(segment_analysis, Mapping):
        raise ReversePromptVideoProcessingError(
            f"Reverse prompt segment {segment.index} returned no analysis."
        )
    normalized_segment = dict(segment_analysis)
    if scene_cuts_sec is not None:
        refine_video_timeline_with_scene_cuts(
            normalized_segment,
            scene_cuts_sec=scene_cuts_sec,
            start_sec=segment.start_sec,
            end_sec=segment.end_sec,
        )
    _assert_timeline_covers(
        normalized_segment.get("shot_list"),
        start_sec=segment.start_sec,
        end_sec=segment.end_sec,
    )
    return normalized_segment


def _record_video_segment(
    db,
    *,
    job: ReversePromptJob,
    segment: VideoSegment,
    segments_total: int,
    segment_result: Mapping[str, Any],
    normalized_segment: dict[str, Any],
    segment_analyses: list[dict[str, Any]],
    usage_results: list[Mapping[str, Any]],
    raw_segments: list[dict[str, object]],
) -> None:
    segment_analyses.append(normalized_segment)
    usage_results.append(segment_result)
    raw_segments.append(
        dict(segment_result.get("raw_model_json") or {})
        if isinstance(segment_result.get("raw_model_json"), Mapping)
        else {}
    )
    set_reverse_prompt_segment_progress(
        db,
        job=job,
        segments_total=segments_total,
        segments_done=segment.index,
    )


def _summarize_video_segments(
    provider: Any,
    *,
    duration_sec: float,
    target_format: str,
    audio_transcript: str | None,
    segment_analyses: list[dict[str, Any]],
    usage_results: list[Mapping[str, Any]],
    raw_segments: list[dict[str, object]],
    model: str | None = None,
    scene_cuts_sec: list[float] | None = None,
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    summary_payload = _video_summary_payload(
        duration_sec=duration_sec,
        target_format=target_format,
        audio_transcript=audio_transcript,
        segment_analyses=segment_analyses,
        model=model,
    )
    summary_result = _request_video_summary(provider, summary_payload)
    _finalize_video_summary(
        summary_result,
        duration_sec=duration_sec,
        audio_transcript=audio_transcript,
        raw_segments=raw_segments,
        scene_cuts_sec=scene_cuts_sec,
    )
    usage_results.append(summary_result)
    return summary_result, usage_results


def _summarize_native_video_segments(
    provider: Any,
    *,
    job: ReversePromptJob,
    fallback_events: list[dict[str, object]],
    duration_sec: float,
    target_format: str,
    audio_transcript: str | None,
    segment_analyses: list[dict[str, Any]],
    usage_results: list[Mapping[str, Any]],
    raw_segments: list[dict[str, object]],
    model: str,
    scene_cuts_sec: list[float],
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    summary_payload = _video_summary_payload(
        duration_sec=duration_sec,
        target_format=target_format,
        audio_transcript=audio_transcript,
        segment_analyses=segment_analyses,
        model=model,
    )
    try:
        summary_result = _run_native_stage(
            "summary_provider",
            lambda: _request_video_summary(provider, summary_payload),
        )
        _run_native_stage(
            "summary_validation",
            lambda: _finalize_video_summary(
                summary_result,
                duration_sec=duration_sec,
                audio_transcript=audio_transcript,
                raw_segments=raw_segments,
                scene_cuts_sec=scene_cuts_sec,
            ),
            usage_results=[summary_result],
        )
    except _NativeVideoFallback as fallback:
        _record_native_fallback(
            job=job,
            fallback_events=fallback_events,
            scope="long",
            fallback=fallback,
        )
        usage_results.extend(fallback.usage_results)
        legacy_payload = {
            **summary_payload,
            "model": settings.engine_apimart_reverse_prompt_model,
        }
        summary_result = _request_video_summary(provider, legacy_payload)
        _finalize_video_summary(
            summary_result,
            duration_sec=duration_sec,
            audio_transcript=audio_transcript,
            raw_segments=raw_segments,
            scene_cuts_sec=scene_cuts_sec,
        )
    usage_results.append(summary_result)
    return summary_result, usage_results


def _video_summary_payload(
    *,
    duration_sec: float,
    target_format: str,
    audio_transcript: str | None,
    segment_analyses: list[dict[str, Any]],
    model: str | None,
) -> dict[str, Any]:
    summary_payload: dict[str, Any] = {
        "duration_sec": duration_sec,
        "segment_analyses": segment_analyses,
        "audio_transcript": audio_transcript,
        "target_format": target_format,
    }
    if model is not None:
        summary_payload["model"] = model
    return summary_payload


def _request_video_summary(
    provider: Any,
    summary_payload: Mapping[str, Any],
) -> dict[str, Any]:
    return _call_provider(
        lambda: provider.summarize_video_segments(summary_payload)
    )


def _finalize_video_summary(
    summary_result: dict[str, Any],
    *,
    duration_sec: float,
    audio_transcript: str | None,
    raw_segments: list[dict[str, object]],
    scene_cuts_sec: list[float] | None,
) -> None:
    _attach_audio_context(
        summary_result,
        duration_sec=duration_sec,
        audio_transcript=audio_transcript,
    )
    normalized_analysis = summary_result["video_analysis"]
    if scene_cuts_sec is not None:
        refine_video_timeline_with_scene_cuts(
            normalized_analysis,
            scene_cuts_sec=scene_cuts_sec,
            start_sec=0.0,
            end_sec=duration_sec,
        )
    _assert_timeline_covers(
        normalized_analysis.get("shot_list"),
        start_sec=0.0,
        end_sec=duration_sec,
    )
    summary_result["video_analysis"] = normalized_analysis
    summary_raw = summary_result.get("raw_model_json")
    summary_result["raw_model_json"] = {
        "segments": raw_segments,
        "summary": dict(summary_raw) if isinstance(summary_raw, Mapping) else {},
    }


def _segment_result_with_retry(
    provider: Any,
    payload: dict[str, Any],
    segment_index: int,
    *,
    operation_name: str = "analyze_video_segment",
) -> dict[str, Any]:
    operation = getattr(provider, operation_name)
    failed_usage: list[Mapping[str, Any]] = []
    for attempt in range(2):
        try:
            return _call_provider(lambda: operation(payload))
        except _NATIVE_RUNTIME_ERRORS as exc:
            failed_usage.extend(_usage_results_from_exception(exc))
            if attempt == 1:
                raise _ProviderCallFailure(
                    cause=exc,
                    usage_results=failed_usage,
                ) from exc
            logger.warning(
                "reverse_prompt_video_segment_retry",
                segment_index=segment_index,
                operation=operation_name,
            )
    raise AssertionError("unreachable")


def _best_effort_audio_transcript(
    provider: Any,
    video_path: Path,
) -> tuple[str | None, Mapping[str, Any] | None]:
    try:
        audio_bytes = extract_audio_track(video_path)
        transcribe = getattr(provider, "transcribe_audio", None)
        if not audio_bytes or not callable(transcribe):
            return None, None
        result = _call_provider(
            lambda: transcribe(
                {
                    "audio_bytes": audio_bytes,
                    "filename": "reverse-prompt.mp3",
                    "language": "zh",
                }
            ),
        )
        transcript = str(
            result.get("audio_transcript")
            or result.get("text")
            or ""
        ).strip()
        return (transcript or None), result
    except Exception as exc:
        logger.warning(
            "reverse_prompt_audio_transcription_degraded",
            error_type=type(exc).__name__,
        )
        return None, None


def _frame_data_urls(frames: list[bytes]) -> list[str]:
    return [
        "data:image/jpeg;base64," + base64.b64encode(frame).decode("ascii")
        for frame in frames
    ]


def _call_provider(operation: Callable[[], Any]) -> dict[str, Any]:
    usage_checkpoint = reverse_prompt_usage_checkpoint()
    try:
        result = _mapping_result(_invoke_provider(operation()))
    except Exception as exc:
        if not reverse_prompt_usage_since(usage_checkpoint):
            for usage in _usage_results_from_exception(exc):
                capture_reverse_prompt_usage(usage)
        raise
    if not reverse_prompt_usage_since(usage_checkpoint):
        capture_reverse_prompt_usage(result)
    return result


def _invoke_provider(operation: Any) -> Any:
    return asyncio.run(operation) if inspect.isawaitable(operation) else operation


def _mapping_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, Mapping):
        return dict(result)
    raise ReversePromptVideoProcessingError("Reverse prompt provider returned invalid data.")


def _aggregate_provider_usage(
    result: Mapping[str, Any],
    usage_results: list[Mapping[str, Any]],
) -> dict[str, Any]:
    prompt_tokens = sum(nonnegative_int(item.get("prompt_tokens")) for item in usage_results)
    completion_tokens = sum(
        nonnegative_int(item.get("completion_tokens")) for item in usage_results
    )
    total_tokens = sum(
        nonnegative_int(item.get("total_tokens"))
        or (
            nonnegative_int(item.get("prompt_tokens"))
            + nonnegative_int(item.get("completion_tokens"))
        )
        for item in usage_results
    )
    credits = sum(
        (Decimal(str(item.get("credits") or "0")) for item in usage_results),
        Decimal("0"),
    )
    cost_cents = (
        apimart_cost_cents_from_credits(credits)
        if credits > 0
        else sum(nonnegative_int(item.get("cost_cents")) for item in usage_results)
    )
    return {
        **dict(result),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "credits": credits,
        "cost_cents": cost_cents,
    }


def _assert_timeline_covers(
    raw_shots: Any,
    *,
    start_sec: float,
    end_sec: float,
) -> None:
    if not isinstance(raw_shots, list) or not raw_shots:
        raise ReversePromptVideoProcessingError("Reverse prompt timeline is empty.")
    intervals: list[tuple[float, float]] = []
    for raw_shot in raw_shots:
        if not isinstance(raw_shot, Mapping):
            continue
        try:
            shot_start = float(raw_shot.get("start_sec"))
            shot_end = float(raw_shot.get("end_sec"))
        except (TypeError, ValueError):
            continue
        if shot_end > shot_start:
            intervals.append((shot_start, shot_end))
    intervals.sort()
    cursor = start_sec
    tolerance = 0.05
    for shot_start, shot_end in intervals:
        if shot_start > cursor + tolerance:
            raise ReversePromptVideoProcessingError("Reverse prompt timeline contains a gap.")
        cursor = max(cursor, shot_end)
    if not intervals or intervals[0][0] > start_sec + tolerance or cursor < end_sec - tolerance:
        raise ReversePromptVideoProcessingError(
            "Reverse prompt timeline does not cover the full source."
        )
