from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.storage.base import ObjectStorage
from app.services.storage.keys import get_tenant_storage_bytes


class CoverFrameError(Exception):
    """Base error for friendly cover frame failures."""


class CoverTimestampOutOfRange(CoverFrameError):
    """Raised when the requested timestamp cannot be read from the video."""


@dataclass(frozen=True)
class ExtractedFrame:
    timestamp_sec: float
    image_bytes: bytes
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class CoverImage:
    image_bytes: bytes
    width: int
    height: int


def clamp_frame_count(count: int) -> int:
    return max(1, min(10, int(count)))


def candidate_timestamps(duration_sec: float | int | None, count: int) -> list[float]:
    safe_count = clamp_frame_count(count)
    duration = max(0.0, float(duration_sec or 0))
    if safe_count == 1 or duration <= 0:
        return [0.0 for _ in range(safe_count)]
    end = max(0.0, duration - 0.1)
    step = end / (safe_count - 1)
    return [round(step * index, 3) for index in range(safe_count)]


def extract_frame_candidates(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    video_key: str,
    timestamps: list[float],
) -> list[ExtractedFrame]:
    video_path = _video_from_storage(storage, tenant_id, video_key)
    try:
        return _extract_frame_candidates_from_path(video_path, timestamps=timestamps)
    finally:
        video_path.unlink(missing_ok=True)


def extract_frame_cover(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    video_key: str,
    timestamp_sec: float,
    title: dict[str, Any] | None = None,
) -> CoverImage:
    video_path = _video_from_storage(storage, tenant_id, video_key)
    try:
        return _extract_frame_cover_from_path(video_path, timestamp_sec=timestamp_sec, title=title)
    finally:
        video_path.unlink(missing_ok=True)


def _video_from_storage(storage: ObjectStorage, tenant_id: str, video_key: str) -> Path:
    try:
        return _write_temp_video(
            get_tenant_storage_bytes(
                storage,
                tenant_id=tenant_id,
                storage_key=video_key,
            )
        )
    except Exception as exc:
        raise CoverFrameError("Could not read source video.") from exc


def _write_temp_video(content: bytes) -> Path:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as handle:
        handle.write(content)
        return Path(handle.name)


def _extract_frame_candidates_from_path(
    video_path: Path,
    *,
    timestamps: list[float],
) -> list[ExtractedFrame]:
    from moviepy.editor import VideoFileClip

    clip = VideoFileClip(str(video_path))
    try:
        frames: list[ExtractedFrame] = []
        for timestamp in timestamps:
            image = _frame_image(clip, timestamp)
            frames.append(
                ExtractedFrame(
                    timestamp_sec=timestamp,
                    image_bytes=_encode_image(image, "JPEG"),
                    width=image.width,
                    height=image.height,
                )
            )
        return frames
    finally:
        clip.close()


def _extract_frame_cover_from_path(
    video_path: Path,
    *,
    timestamp_sec: float,
    title: dict[str, Any] | None,
) -> CoverImage:
    from moviepy.editor import VideoFileClip

    clip = VideoFileClip(str(video_path))
    try:
        image = _frame_image(clip, timestamp_sec)
        _draw_title(image, title)
        return CoverImage(
            image_bytes=_encode_image(image, "PNG"),
            width=image.width,
            height=image.height,
        )
    finally:
        clip.close()


def _frame_image(clip, timestamp_sec: float):
    from PIL import Image

    duration = max(0.0, float(clip.duration or 0))
    timestamp = float(timestamp_sec)
    if timestamp < 0 or (duration > 0 and timestamp > duration):
        raise CoverTimestampOutOfRange("timestamp_sec is outside the video duration.")
    safe_timestamp = min(timestamp, max(0.0, duration - 0.001)) if duration > 0 else timestamp
    try:
        return Image.fromarray(clip.get_frame(safe_timestamp)).convert("RGB")
    except Exception as exc:  # pragma: no cover - moviepy-specific failure details vary
        raise CoverFrameError("Could not extract frame from video.") from exc


def _encode_image(image, image_format: str) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def _draw_title(image, title: dict[str, Any] | None) -> None:
    text = str((title or {}).get("text") or "").strip()
    if not text:
        return

    from PIL import ImageDraw, ImageFont

    font_size = int((title or {}).get("font_size") or max(32, int(image.height * 0.06)))
    color = _hex_color(str((title or {}).get("color") or "#FFFFFF"))
    position = str((title or {}).get("position") or "bottom")
    font = _cover_font(font_size, ImageFont)
    draw = ImageDraw.Draw(image)
    max_width = int(image.width * 0.86)
    lines = _wrap_title(text, max_width=max_width, draw=draw, font=font)
    line_gap = max(6, int(font_size * 0.25))
    boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=2) for line in lines]
    text_height = sum(box[3] - box[1] for box in boxes) + line_gap * max(0, len(lines) - 1)
    margin = max(32, int(image.height * 0.06))
    if position == "top":
        y = margin
    elif position == "center":
        y = max(0, (image.height - text_height) // 2)
    else:
        y = max(0, image.height - margin - text_height)

    for line, box in zip(lines, boxes, strict=False):
        line_width = box[2] - box[0]
        x = (image.width - line_width) // 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill=color,
            stroke_width=2,
            stroke_fill=(0, 0, 0),
        )
        y += box[3] - box[1] + line_gap


def _cover_font(font_size: int, image_font):
    for name in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "arial.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            return image_font.truetype(name, size=font_size)
        except OSError:
            continue
    return image_font.load_default()


def _wrap_title(text: str, *, max_width: int, draw, font) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), candidate, font=font, stroke_width=2)
        if box[2] - box[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _hex_color(value: str) -> tuple[int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        return (255, 255, 255)
    try:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError:
        return (255, 255, 255)
