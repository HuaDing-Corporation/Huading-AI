from __future__ import annotations

import asyncio
import base64
import inspect
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from sqlalchemy import update

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ReversePromptJob
from app.db.session import SessionLocal
from app.providers.base import resolve
from app.services import quota
from app.services.reverse_prompt import (
    mark_reverse_prompt_job_failed,
    mark_reverse_prompt_job_succeeded,
    nonnegative_int,
    set_reverse_prompt_segment_progress,
    source_asset_or_raise,
)
from app.services.storage.factory import create_object_storage
from app.services.storage.keys import get_tenant_storage_bytes

_SHORT_VIDEO_MAX_DURATION_SEC = 60.0
_MAX_VIDEO_DURATION_SEC = 180.0
_LONG_VIDEO_SEGMENT_SEC = 30.0
_LONG_VIDEO_FRAMES_PER_SEGMENT = 6
_FRAME_MAX_EDGE = 768
_FRAME_TIMEOUT_SEC = 30.0
_AUDIO_TIMEOUT_SEC = 60.0
logger = get_logger(__name__)


class ReversePromptVideoProcessingError(RuntimeError):
    pass


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


def video_segment_plan(duration_sec: float) -> list[VideoSegment]:
    duration = float(duration_sec)
    if duration <= _SHORT_VIDEO_MAX_DURATION_SEC or duration > _MAX_VIDEO_DURATION_SEC:
        raise ReversePromptVideoProcessingError(
            "Long reverse prompt video must be over 60 and at most 180 seconds."
        )
    segments: list[VideoSegment] = []
    start_sec = 0.0
    while start_sec < duration:
        end_sec = min(duration, start_sec + _LONG_VIDEO_SEGMENT_SEC)
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
            audio_transcript, audio_usage = _best_effort_audio_transcript(
                provider,
                temp_path,
            )
            if duration_sec <= _SHORT_VIDEO_MAX_DURATION_SEC:
                result, usage_results = _run_short_video_analysis(
                    provider,
                    temp_path,
                    duration_sec=duration_sec,
                    target_format=job.target_format,
                    audio_transcript=audio_transcript,
                )
            else:
                result, usage_results = _run_long_video_analysis(
                    db,
                    job=job,
                    provider=provider,
                    video_path=temp_path,
                    duration_sec=duration_sec,
                    target_format=job.target_format,
                    audio_transcript=audio_transcript,
                )
            if audio_usage is not None:
                usage_results.insert(0, audio_usage)
            result = _aggregate_provider_usage(result, usage_results)
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
                model=str(result.get("model") or settings.engine_apimart_reverse_prompt_model),
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
                failed_job.raw_model_json = None
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
            raise ReversePromptVideoProcessingError(
                "Reverse prompt video processing failed."
            ) from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def _run_short_video_analysis(
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
    result = _mapping_result(
        _invoke_provider(
            provider.reverse_video_frames(
                {
                    "image_urls": _frame_data_urls(frames),
                    "timestamps_sec": timestamps_sec,
                    "duration_sec": duration_sec,
                    "target_format": target_format,
                }
            )
        )
    )
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
    return result, [result]


def _run_long_video_analysis(
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
        segment_result = _segment_result_with_retry(provider, payload, segment.index)
        segment_analysis = segment_result.get("segment_analysis")
        if not isinstance(segment_analysis, Mapping):
            raise ReversePromptVideoProcessingError(
                f"Reverse prompt segment {segment.index} returned no analysis."
            )
        normalized_segment = dict(segment_analysis)
        _assert_timeline_covers(
            normalized_segment.get("shot_list"),
            start_sec=segment.start_sec,
            end_sec=segment.end_sec,
        )
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
            segments_total=len(segments),
            segments_done=segment.index,
        )

    summary_result = _mapping_result(
        _invoke_provider(
            provider.summarize_video_segments(
                {
                    "duration_sec": duration_sec,
                    "segment_analyses": segment_analyses,
                    "audio_transcript": audio_transcript,
                    "target_format": target_format,
                }
            )
        )
    )
    video_analysis = summary_result.get("video_analysis")
    if not isinstance(video_analysis, Mapping):
        raise ReversePromptVideoProcessingError(
            "Reverse prompt provider returned no video analysis."
        )
    normalized_analysis = dict(video_analysis)
    normalized_analysis["duration_sec"] = duration_sec
    normalized_analysis["audio_transcript"] = audio_transcript
    # SPIKE proved the transcription endpoint cannot classify BGM style.
    normalized_analysis["bgm_style"] = None
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
    usage_results.append(summary_result)
    return summary_result, usage_results


def _segment_result_with_retry(
    provider: Any,
    payload: dict[str, Any],
    segment_index: int,
) -> dict[str, Any]:
    for attempt in range(2):
        try:
            return _mapping_result(
                _invoke_provider(provider.analyze_video_segment(payload))
            )
        except Exception:
            if attempt == 1:
                raise
            logger.warning(
                "reverse_prompt_video_segment_retry",
                segment_index=segment_index,
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
        result = _mapping_result(
            _invoke_provider(
                transcribe(
                    {
                        "audio_bytes": audio_bytes,
                        "filename": "reverse-prompt.mp3",
                        "language": "zh",
                    }
                )
            )
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
    cost_cents = sum(nonnegative_int(item.get("cost_cents")) for item in usage_results)
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
