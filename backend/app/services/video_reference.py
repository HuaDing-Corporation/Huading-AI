from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.exceptions import AppError

VIDEO_REFERENCE_MAX_BYTES = 100 * 1024 * 1024
VIDEO_REFERENCE_MIN_DURATION_MS = 1_800
VIDEO_REFERENCE_MAX_DURATION_MS = 15_200
VIDEO_REFERENCE_MIN_DIMENSION = 480
VIDEO_REFERENCE_TARGET_DIMENSION = 720


@dataclass(frozen=True)
class NormalizedVideoReference:
    content: bytes
    duration_ms: int
    width: int
    height: int
    container: str
    video_codec: str
    audio_codec: str
    transcoded: bool


@dataclass(frozen=True)
class _VideoProbe:
    duration_ms: int | None
    width: int | None
    height: int | None
    container: str
    video_codec: str
    audio_codec: str


def _ffmpeg_binary() -> str:
    return os.environ.get("FFMPEG_BINARY", "ffmpeg")


def _ffprobe_binary() -> str:
    return os.environ.get("FFPROBE_BINARY", "ffprobe")


def _probe_path(path: Path, *, transcode_output: bool = False) -> _VideoProbe:
    try:
        result = subprocess.run(
            [
                _ffprobe_binary(),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        code = (
            "VIDEO_GEN_REFERENCE_TRANSCODE_FAILED"
            if transcode_output
            else "VIDEO_GEN_REFERENCE_DECODE_FAILED"
        )
        raise AppError(
            "参考视频处理失败，请确认文件可正常播放后重试。",
            code=code,
            status_code=422,
        ) from exc
    if result.returncode != 0:
        code = (
            "VIDEO_GEN_REFERENCE_TRANSCODE_FAILED"
            if transcode_output
            else "VIDEO_GEN_REFERENCE_DECODE_FAILED"
        )
        raise AppError(
            "参考视频处理失败，请确认文件可正常播放后重试。",
            code=code,
            status_code=422,
        )
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        code = (
            "VIDEO_GEN_REFERENCE_TRANSCODE_FAILED"
            if transcode_output
            else "VIDEO_GEN_REFERENCE_DECODE_FAILED"
        )
        raise AppError(
            "参考视频处理失败，请确认文件可正常播放后重试。",
            code=code,
            status_code=422,
        ) from exc

    streams = data.get("streams") if isinstance(data, dict) else []
    video_stream = next(
        (stream for stream in streams or [] if stream.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (stream for stream in streams or [] if stream.get("codec_type") == "audio"),
        {},
    )
    format_info = data.get("format") if isinstance(data, dict) else {}
    duration = format_info.get("duration") or video_stream.get("duration")
    try:
        duration_ms = int(round(float(duration) * 1000)) if duration is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    return _VideoProbe(
        duration_ms=duration_ms,
        width=int(video_stream["width"]) if video_stream.get("width") else None,
        height=int(video_stream["height"]) if video_stream.get("height") else None,
        container=str(format_info.get("format_name") or ""),
        video_codec=str(video_stream.get("codec_name") or ""),
        audio_codec=str(audio_stream.get("codec_name") or ""),
    )


def _validate_probe(probe: _VideoProbe) -> None:
    if (
        probe.duration_ms is None
        or probe.duration_ms < VIDEO_REFERENCE_MIN_DURATION_MS
        or probe.duration_ms > VIDEO_REFERENCE_MAX_DURATION_MS
    ):
        raise AppError(
            "单条参考视频时长需在 1.8 秒至 15.2 秒之间。",
            code="VIDEO_GEN_REFERENCE_DURATION_INVALID",
            status_code=422,
        )
    width = int(probe.width or 0)
    height = int(probe.height or 0)
    if min(width, height) < VIDEO_REFERENCE_MIN_DIMENSION:
        raise AppError(
            "参考视频短边不能低于 480 像素。",
            code="VIDEO_GEN_REFERENCE_RESOLUTION_TOO_LOW",
            status_code=422,
        )


def _transcode_to_mp4(source: Path, target: Path, *, downscale: bool) -> None:
    command = [
        _ffmpeg_binary(),
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-write_tmcd",
        "0",
    ]
    if downscale:
        command.extend(
            [
                "-vf",
                (
                    "scale="
                    f"'if(lt(iw,ih),{VIDEO_REFERENCE_TARGET_DIMENSION},-2)':"
                    f"'if(lt(iw,ih),-2,{VIDEO_REFERENCE_TARGET_DIMENSION})'"
                ),
            ]
        )
    command.extend(
        [
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(target),
        ]
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise AppError(
            "参考视频转码失败，请更换视频后重试。",
            code="VIDEO_GEN_REFERENCE_TRANSCODE_FAILED",
            status_code=422,
        ) from exc
    if result.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
        raise AppError(
            "参考视频转码失败，请更换视频后重试。",
            code="VIDEO_GEN_REFERENCE_TRANSCODE_FAILED",
            status_code=422,
        )


def normalize_video_reference(content: bytes, *, suffix: str) -> NormalizedVideoReference:
    normalized_suffix = suffix.lower() if suffix else ".mp4"
    with TemporaryDirectory(prefix="huading-video-reference-") as temp_dir:
        source = Path(temp_dir) / f"source{normalized_suffix}"
        source.write_bytes(content)
        source_probe = _probe_path(source)
        _validate_probe(source_probe)

        shorter_dimension = min(int(source_probe.width or 0), int(source_probe.height or 0))
        downscale = shorter_dimension > VIDEO_REFERENCE_TARGET_DIMENSION
        # Always remux through ffmpeg, even for an already compliant MP4. This strips
        # subtitle/data streams whose independent durations can make the provider
        # reject an otherwise valid DB-backed total duration.
        target = Path(temp_dir) / "normalized.mp4"
        _transcode_to_mp4(source, target, downscale=downscale)
        normalized_probe = _probe_path(target, transcode_output=True)
        _validate_probe(normalized_probe)
        normalized_content = target.read_bytes()

    return NormalizedVideoReference(
        content=normalized_content,
        duration_ms=int(normalized_probe.duration_ms or 0),
        width=int(normalized_probe.width or 0),
        height=int(normalized_probe.height or 0),
        container=normalized_probe.container,
        video_codec=normalized_probe.video_codec,
        audio_codec=normalized_probe.audio_codec,
        transcoded=True,
    )
