from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import TenantLabelSettings

LabelKind = Literal["image", "video", "audio"]

DEFAULT_LABEL_POSITION = "br"
DEFAULT_LABEL_TEXT = "AI 生成"
SYNTHETIC_LABEL_VALUE = "AI_GENERATED_SYNTHETIC"
_PROVIDER_CODE_PENDING = "PENDING_PROVIDER_CODE"
_VALID_POSITIONS = {"br", "bl", "tr", "tl", "bc"}


@dataclass(frozen=True)
class LabelSettings:
    position: str = DEFAULT_LABEL_POSITION
    text: str = DEFAULT_LABEL_TEXT
    enabled: bool = True


@dataclass(frozen=True)
class SyntheticLabelMeta:
    content_id: str
    provider_name: str
    provider_code: str
    label: str = SYNTHETIC_LABEL_VALUE


def label_settings_for_tenant(db: Session, *, tenant_id: str) -> LabelSettings:
    row = db.get(TenantLabelSettings, tenant_id)
    if row is None:
        return LabelSettings()
    return LabelSettings(position=row.position, text=row.text, enabled=True)


def upsert_label_settings(
    db: Session,
    *,
    tenant_id: str,
    position: str,
    text: str,
) -> LabelSettings:
    row = db.get(TenantLabelSettings, tenant_id)
    now = datetime.now(UTC)
    if row is None:
        row = TenantLabelSettings(
            tenant_id=tenant_id,
            position=position,
            text=text,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.position = position
        row.text = text
        row.updated_at = now
    db.flush()
    return LabelSettings(position=row.position, text=row.text, enabled=True)


def build_synthetic_label_meta(
    *,
    content_id: str,
    provider_name: str | None = None,
    provider_code: str | None = None,
) -> SyntheticLabelMeta:
    return SyntheticLabelMeta(
        content_id=content_id,
        provider_name=(provider_name or settings.engine_aigc_producer or "Huading"),
        provider_code=(
            provider_code
            if provider_code is not None
            else (settings.engine_label_provider_code or _PROVIDER_CODE_PENDING)
        ),
    )


def implicit_metadata_fields(meta: SyntheticLabelMeta) -> dict[str, str]:
    return {
        "aigc_label": meta.label,
        "aigc_provider_name": meta.provider_name,
        "aigc_provider_code": meta.provider_code,
        "aigc_content_id": meta.content_id,
    }


def synthetic_label_payload(
    label_settings: LabelSettings,
    meta: SyntheticLabelMeta,
    *,
    visible: bool = True,
) -> dict[str, object]:
    return {
        **implicit_metadata_fields(meta),
        "content_id": meta.content_id,
        "provider_name": meta.provider_name,
        "provider_code": meta.provider_code,
        "label": meta.label,
        "position": label_settings.position,
        "text": label_settings.text,
        "enabled": True,
        "visible": visible,
    }


def synthetic_label_context(
    db: Session,
    *,
    tenant_id: str,
    content_id: str,
    visible: bool = True,
) -> tuple[LabelSettings, SyntheticLabelMeta, dict[str, object]]:
    label_settings = label_settings_for_tenant(db, tenant_id=tenant_id)
    meta = build_synthetic_label_meta(content_id=content_id)
    return label_settings, meta, synthetic_label_payload(
        label_settings,
        meta,
        visible=visible,
    )


def label_artifact_bytes(
    content: bytes,
    *,
    kind: LabelKind,
    settings: LabelSettings,
    meta: SyntheticLabelMeta,
    suffix: str | None = None,
    visible: bool = True,
) -> bytes:
    if kind == "image":
        return _label_image_bytes(content, settings=settings, meta=meta, visible=visible)
    if kind == "video":
        return _label_video_bytes(
            content,
            settings=settings,
            meta=meta,
            suffix=suffix or ".mp4",
            visible=visible,
        )
    if kind == "audio":
        return _label_audio_bytes(content, meta=meta, suffix=suffix or ".mp3")
    raise ValueError(f"Unsupported synthetic label kind: {kind}")


def _label_image_bytes(
    content: bytes,
    *,
    settings: LabelSettings,
    meta: SyntheticLabelMeta,
    visible: bool,
) -> bytes:
    with Image.open(BytesIO(content)) as image:
        base = image.convert("RGBA")
    labeled = _draw_image_label(base, settings=settings) if visible else base
    pnginfo = PngImagePlugin.PngInfo()
    for key, value in {
        **implicit_metadata_fields(meta),
        "aigc_label_text": settings.text,
        "aigc_label_position": settings.position,
    }.items():
        pnginfo.add_text(key, str(value))
    buffer = BytesIO()
    labeled.save(buffer, format="PNG", pnginfo=pnginfo)
    return buffer.getvalue()


def _draw_image_label(image: Image.Image, *, settings: LabelSettings) -> Image.Image:
    position = (
        settings.position if settings.position in _VALID_POSITIONS else DEFAULT_LABEL_POSITION
    )
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = image.size
    font_size = max(16, int(min(width, height) * 0.07))
    font = _font(font_size)
    stroke_width = max(2, int(font_size * 0.08))
    bbox = draw.textbbox((0, 0), settings.text, font=font, stroke_width=stroke_width)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x, y = _position_xy(
        position=position,
        width=width,
        height=height,
        text_width=text_width,
        text_height=text_height,
        padding=max(12, int(font_size * 0.6)),
    )
    draw.text(
        (x, y),
        settings.text,
        font=font,
        fill=(255, 255, 255, 210),
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0, 190),
    )
    return Image.alpha_composite(image, overlay)


def _position_xy(
    *,
    position: str,
    width: int,
    height: int,
    text_width: int,
    text_height: int,
    padding: int,
) -> tuple[int, int]:
    if position == "bl":
        return padding, height - text_height - padding
    if position == "tr":
        return width - text_width - padding, padding
    if position == "tl":
        return padding, padding
    if position == "bc":
        return (width - text_width) // 2, height - text_height - padding
    return width - text_width - padding, height - text_height - padding


def _font(size: int):
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
    for name in dict.fromkeys(item for item in candidates if item):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _label_video_bytes(
    content: bytes,
    *,
    settings: LabelSettings,
    meta: SyntheticLabelMeta,
    suffix: str,
    visible: bool,
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="huading-label-") as temp_dir:
        temp_path = Path(temp_dir)
        source = temp_path / f"source{suffix}"
        output = temp_path / f"labeled{suffix}"
        source.write_bytes(content)
        cmd = [_ffmpeg_binary(), "-y", "-i", str(source)]
        if visible:
            cmd.extend(
                [
                    "-vf",
                    _drawtext_filter(settings),
                    *_ffmpeg_metadata_args(meta),
                    "-c:v",
                    "libx264",
                    "-crf",
                    "18",
                    "-preset",
                    "medium",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "copy",
                ]
            )
        else:
            cmd.extend(
                [
                    *_ffmpeg_metadata_args(meta),
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                ]
            )
        cmd.extend(["-movflags", "+faststart+use_metadata_tags", str(output)])
        _run_ffmpeg(cmd)
        return output.read_bytes()


def _label_audio_bytes(
    content: bytes,
    *,
    meta: SyntheticLabelMeta,
    suffix: str,
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="huading-label-") as temp_dir:
        temp_path = Path(temp_dir)
        source = temp_path / f"source{suffix}"
        output = temp_path / f"labeled{suffix}"
        source.write_bytes(content)
        cmd = [
            _ffmpeg_binary(),
            "-y",
            "-i",
            str(source),
            *_ffmpeg_metadata_args(meta),
            "-codec",
            "copy",
            str(output),
        ]
        _run_ffmpeg(cmd)
        return output.read_bytes()


def _drawtext_filter(label_settings: LabelSettings) -> str:
    font_path = _fontfile_for_ffmpeg()
    font_part = f"fontfile='{_escape_drawtext(font_path)}':" if font_path else ""
    return (
        "drawtext="
        f"{font_part}"
        f"text='{_escape_drawtext(label_settings.text)}':"
        "fontcolor=white@0.78:"
        "fontsize=h*0.035:"
        "box=1:"
        "boxcolor=black@0.35:"
        "boxborderw=14:"
        f"{_drawtext_position(label_settings.position)}"
    )


def _drawtext_position(position: str) -> str:
    if position == "bl":
        return "x=24:y=h-text_h-24"
    if position == "tr":
        return "x=w-text_w-24:y=24"
    if position == "tl":
        return "x=24:y=24"
    if position == "bc":
        return "x=(w-text_w)/2:y=h-text_h-24"
    return "x=w-text_w-24:y=h-text_h-24"


def _fontfile_for_ffmpeg() -> str | None:
    for name in (
        settings.engine_subtitle_font_path,
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ):
        if name and Path(name).exists():
            return name.replace("\\", "/")
    return None


def _escape_drawtext(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace(",", "\\,")
    )


def _ffmpeg_metadata_args(meta: SyntheticLabelMeta) -> list[str]:
    args: list[str] = []
    for key, value in implicit_metadata_fields(meta).items():
        args.extend(["-metadata", f"{key}={value}"])
    args.extend(["-metadata", f"comment={meta.label};content_id={meta.content_id}"])
    return args


def _ffmpeg_binary() -> str:
    return os.environ.get("FFMPEG_BINARY", "ffmpeg")


def _run_ffmpeg(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Could not write synthetic label metadata.") from exc
