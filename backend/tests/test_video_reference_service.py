import json
import subprocess
from pathlib import Path

import pytest

from app.core.exceptions import AppError
from app.services.video_reference import normalize_video_reference


def _video_bytes(
    tmp_path: Path,
    *,
    suffix: str,
    size: str,
    duration: float,
    timecode: bool = False,
) -> bytes:
    output = tmp_path / f"source{suffix}"
    codec_args = ["-c:v", "libvpx-vp9"] if suffix == ".webm" else ["-c:v", "libx264"]
    timecode_args = ["-timecode", "00:00:00:00"] if timecode else []
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s={size}:d={duration}:r=10",
            "-an",
            *codec_args,
            "-pix_fmt",
            "yuv420p",
            *timecode_args,
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output.read_bytes()


@pytest.mark.parametrize("suffix", [".mov", ".webm"])
def test_video_reference_real_transcode_normalizes_format_and_short_edge(
    suffix: str,
    tmp_path: Path,
) -> None:
    source = _video_bytes(
        tmp_path,
        suffix=suffix,
        size="800x1280",
        duration=2.25,
    )

    normalized = normalize_video_reference(source, suffix=suffix)

    assert normalized.content != source
    assert normalized.transcoded is True
    assert normalized.video_codec == "h264"
    assert "mp4" in normalized.container
    assert min(normalized.width, normalized.height) == 720
    assert 1_800 < normalized.duration_ms < 15_200


def test_video_reference_normalization_strips_extra_data_streams(tmp_path: Path) -> None:
    source = _video_bytes(
        tmp_path,
        suffix=".mp4",
        size="640x960",
        duration=3,
        timecode=True,
    )

    normalized = normalize_video_reference(source, suffix=".mp4")
    output = tmp_path / "normalized.mp4"
    output.write_bytes(normalized.content)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert [stream["codec_type"] for stream in json.loads(probe.stdout)["streams"]] == [
        "video"
    ]


@pytest.mark.parametrize(
    ("size", "duration", "expected_code"),
    [
        ("400x640", 2.25, "VIDEO_GEN_REFERENCE_RESOLUTION_TOO_LOW"),
        ("640x960", 1.7, "VIDEO_GEN_REFERENCE_DURATION_INVALID"),
        ("640x960", 15.3, "VIDEO_GEN_REFERENCE_DURATION_INVALID"),
    ],
)
def test_video_reference_rejects_resolution_and_strict_duration_boundaries(
    size: str,
    duration: float,
    expected_code: str,
    tmp_path: Path,
) -> None:
    source = _video_bytes(
        tmp_path,
        suffix=".mp4",
        size=size,
        duration=duration,
    )

    with pytest.raises(AppError) as exc_info:
        normalize_video_reference(source, suffix=".mp4")

    assert exc_info.value.code == expected_code


@pytest.mark.parametrize("duration", [1.8, 15.2])
def test_video_reference_accepts_inclusive_single_file_duration_boundaries(
    duration: float,
    tmp_path: Path,
) -> None:
    source = _video_bytes(
        tmp_path,
        suffix=".mp4",
        size="640x960",
        duration=duration,
    )

    normalized = normalize_video_reference(source, suffix=".mp4")

    assert normalized.duration_ms == int(duration * 1000)
