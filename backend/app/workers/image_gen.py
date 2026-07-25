from __future__ import annotations

import asyncio
import base64
import re
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import openai
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from app.api.deps import scoped_task_id
from app.core.config import settings
from app.core.image_aspect_ratio import (
    aspect_ratio_from_provider_size,
    image_aspect_ratio_from_legacy_size,
    resolve_image_aspect_ratio,
)
from app.core.logging import get_logger
from app.db.models import Asset, EcomReplicateJob, EcomReplicateOutput, TaskAsset, VideoTask
from app.db.session import SessionLocal
from app.providers.base import (
    ImageProviderCapabilitiesError,
    ProviderResolutionError,
    invoke,
    resolve_named_provider,
    resolve_with_name,
    validate_image_provider_request,
)
from app.services.apimart_costs import apimart_cost_cents_from_result
from app.services.ecom_replicate import record_analysis_cost, record_render_cost
from app.services.generation_heartbeat import generation_heartbeat
from app.services.history import prune_video_history_best_effort
from app.services.progress import build_progress_store
from app.services.quota import release_reserved_quota, settle_reserved_quota
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage
from app.services.storage.keys import (
    get_tenant_storage_bytes,
    presign_tenant_storage_key,
    put_tenant_storage_bytes,
)
from app.services.synthetic_label import (
    label_artifact_bytes,
    synthetic_label_context,
)
from app.services.task_claims import (
    claim_ecom_replicate_job_for_worker,
    claim_video_task_for_worker,
)
from app.workers.celery_app import celery_app
from app.workers.video_tasks import _safe_task_id, _tenant_upload_storage_key

logger = get_logger(__name__)

_ERROR_CODE = "IMAGE_GEN_FAILED"
_CONNECTION_ERROR_CODE = "IMAGE_CONNECTION_ERROR"
_MODERATION_ERROR_CODE = "IMAGE_MODERATION_BLOCKED"
_INVALID_REQUEST_ERROR_CODE = "IMAGE_INVALID_REQUEST"
_ALPHA_MISSING_ERROR_CODE = "IMAGE_ALPHA_MISSING"
_ECOM_CUTOUT_KIND = "ecom_cutout"
_ECOM_MODEL_KIND = "ecom_model"
_ECOM_POSTER_KIND = "ecom_poster"
_APIMART_INPUT_IMAGE_SAFE_BYTES = 14 * 1024 * 1024
_APIMART_INPUT_IMAGES_TOTAL_SAFE_BYTES = 18 * 1024 * 1024
_APIMART_INPUT_IMAGE_MAX_EDGE = 2048
_SOURCE_IMAGE_STORAGE_KEY_RE = re.compile(
    r"^tenants/[A-Za-z0-9_-]+/[A-Za-z0-9_./-]+\.(?:jpg|jpeg|png|webp)$"
)
_IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_PHOTO_STRENGTH_LEVEL_INSTRUCTIONS = {
    10: "Apply this only as a faint preference; broad departures are encouraged.",
    20: "Apply this as a light preference; visible departures are welcome.",
    30: "Apply this as a flexible preference; noticeable departures are acceptable.",
    40: "Apply this as a moderate preference; balance adherence with variation.",
    50: "Apply this as a clear preference; keep recognizable alignment while allowing variation.",
    60: "Apply this as a strong preference; departures should remain controlled.",
    70: "Apply this as a very strong priority; allow only limited departures.",
    80: "Apply this as a strict priority; permit only small departures.",
    90: "Apply this as a very strict priority; allow only minimal departures.",
    100: (
        "Apply this as an overriding requirement; do not depart unless another enabled "
        "control explicitly requires it."
    ),
}
_PHOTO_STRENGTH_SPECS = (
    (
        "similarity_strength",
        "Reference similarity",
        "Keep the result visually similar to all reference images in overall appearance, "
        "composition, palette, proportions, and distinctive details.",
    ),
    (
        "creativity_strength",
        "Creative freedom",
        "Introduce new composition, styling, lighting, color, and decorative ideas in "
        "areas not protected by other enabled controls.",
    ),
    (
        "subject_strength",
        "Subject preservation",
        "Preserve each referenced subject's identity, count, shape, proportions, colors, "
        "logos, text, materials, and defining details.",
    ),
)
_POSTER_SIZE = (1080, 1350)
_POSTER_TEMPLATES = {
    "promo_bold": {
        "background": (244, 48, 58),
        "panel": (255, 244, 232),
        "accent": (255, 210, 74),
        "title": (255, 255, 255),
        "subtitle": (70, 20, 20),
        "product_box": (110, 350, 970, 1015),
        "title_box": (80, 90, 1000, 260),
        "subtitle_box": (130, 1090, 950, 1190),
    },
    "minimal": {
        "background": (246, 248, 245),
        "panel": (255, 255, 255),
        "accent": (31, 31, 31),
        "title": (22, 25, 27),
        "subtitle": (89, 92, 95),
        "product_box": (150, 330, 930, 980),
        "title_box": (100, 100, 980, 240),
        "subtitle_box": (130, 1060, 950, 1160),
    },
    "festival": {
        "background": (142, 27, 39),
        "panel": (255, 238, 204),
        "accent": (249, 191, 76),
        "title": (255, 245, 210),
        "subtitle": (255, 238, 204),
        "product_box": (120, 360, 960, 1010),
        "title_box": (90, 100, 990, 250),
        "subtitle_box": (130, 1090, 950, 1190),
    },
}


class TransparentAlphaMissingError(RuntimeError):
    pass


def _error_text(exc: Exception) -> str:
    parts: list[str] = []
    for attr in ("code", "type", "message"):
        value = getattr(exc, attr, None)
        if value:
            parts.append(str(value))

    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            for key in ("code", "type", "message"):
                value = error.get(key)
                if value:
                    parts.append(str(value))
        parts.append(str(body))
    elif body:
        parts.append(str(body))

    if str(exc):
        parts.append(str(exc))
    return " ".join(parts).lower()


def classify_image_error(exc: Exception) -> str:
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        cause_code = classify_image_error(cause)
        if cause_code != _ERROR_CODE:
            return cause_code
    if isinstance(exc, openai.APIConnectionError):
        return _CONNECTION_ERROR_CODE
    if isinstance(exc, openai.BadRequestError):
        text = _error_text(exc)
        if "moderation" in text or "safety system" in text:
            return _MODERATION_ERROR_CODE
        return _INVALID_REQUEST_ERROR_CODE
    if isinstance(exc, TransparentAlphaMissingError):
        return _ALPHA_MISSING_ERROR_CODE
    if isinstance(exc, ImageProviderCapabilitiesError):
        return exc.code
    if isinstance(exc, ProviderResolutionError):
        return "IMAGE_PROVIDER_NOT_CONFIGURED"
    return _ERROR_CODE


def _photo_storage_key(tenant_id: str, task_id: str) -> str:
    return f"tenants/{_safe_task_id(tenant_id)}/photos/{_safe_task_id(task_id)}/output.png"


def _is_cover_request(params: Mapping[str, Any]) -> bool:
    return params.get("purpose") == "cover" or params.get("kind") == "cover"


def _is_ecom_cutout_request(params: Mapping[str, Any]) -> bool:
    return params.get("kind") == _ECOM_CUTOUT_KIND


def _is_ecom_model_request(params: Mapping[str, Any]) -> bool:
    return params.get("kind") == _ECOM_MODEL_KIND


def _is_ecom_poster_request(params: Mapping[str, Any]) -> bool:
    return params.get("kind") == _ECOM_POSTER_KIND


def _ecom_cutout_background(params: Mapping[str, Any]) -> str:
    return "transparent" if params.get("background") == "transparent" else "white"


def _ecom_cutout_prompt(prompt: str, *, background: str) -> str:
    if background == "transparent":
        instruction = (
            "Remove the full background and return a PNG with real transparent alpha around "
            "the unchanged product. Preserve product shape, color, logos, material, and "
            "proportions. Do not add shadows, text, props, people, or extra product details."
        )
    else:
        instruction = (
            "Replace the background with a clean pure white studio background while preserving "
            "the unchanged product shape, color, logos, material, and proportions. Do not add "
            "text, props, people, or extra product details."
        )
    return f"{prompt}\n\nE-commerce cutout instructions: {instruction}"


def build_photo_prompt(
    prompt: str,
    *,
    master_prompt: str | None = None,
    master_negative_prompt: str | None = None,
    negative_prompt: str | None = None,
    similarity_strength: int | None = None,
    creativity_strength: int | None = None,
    subject_strength: int | None = None,
) -> str:
    parts = []
    master_text = str(master_prompt or "").strip()
    if master_text:
        parts.append(master_text)
    parts.append(str(prompt).strip())

    strength_values = {
        "similarity_strength": similarity_strength,
        "creativity_strength": creativity_strength,
        "subject_strength": subject_strength,
    }
    strength_lines = []
    for field_name, label, instruction in _PHOTO_STRENGTH_SPECS:
        value = strength_values[field_name]
        if value is None:
            continue
        level_instruction = _PHOTO_STRENGTH_LEVEL_INSTRUCTIONS[int(value)]
        strength_lines.append(
            f"- {label} ({value}%): {level_instruction} {instruction}"
        )
    if similarity_strength is not None and creativity_strength is not None:
        if similarity_strength > creativity_strength:
            strength_lines.append(
                "Conflict resolution: Reference similarity takes priority over creative "
                "freedom; apply creative changes only where they do not weaken reference "
                "fidelity."
            )
        elif creativity_strength > similarity_strength:
            strength_lines.append(
                "Conflict resolution: Creative freedom takes priority for composition, "
                "styling, lighting, and environment; keep referenced subjects recognizable."
            )
        else:
            strength_lines.append(
                "Conflict resolution: At equal strengths, preserve reference-defining "
                "subject identity while applying creativity only to composition, styling, "
                "lighting, and non-identity details."
            )
    if strength_lines:
        parts.append("Reference strength controls:\n" + "\n".join(strength_lines))

    negative_lines = []
    master_negative_text = str(master_negative_prompt or "").strip()
    if master_negative_text:
        negative_lines.append(f"- Task-wide: {master_negative_text}")
    negative_text = str(negative_prompt or "").strip()
    if negative_text:
        negative_lines.append(f"- Image-specific: {negative_text}")
    if negative_lines:
        parts.append(
            "Avoid the following where possible; this is soft guidance, not a hard "
            "constraint:\n"
            + "\n".join(negative_lines)
        )

    return "\n\n".join(parts)


def _validate_source_storage_key(tenant_id: str, storage_key: str) -> str:
    safe_tenant_id = _safe_task_id(tenant_id)
    if (
        not storage_key.startswith(f"tenants/{safe_tenant_id}/")
        or ".." in storage_key
        or "\\" in storage_key
        or not _SOURCE_IMAGE_STORAGE_KEY_RE.match(storage_key)
    ):
        raise ValueError(f"unsafe source_storage_key: {storage_key!r}")
    return storage_key


def _write_temp_image_bytes(
    image_bytes: bytes,
    *,
    suffix: str,
    temp_paths: list[Path],
) -> Path:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(image_bytes)
        path = Path(handle.name)
    temp_paths.append(path)
    return path


def _source_storage_keys(params: Mapping[str, Any], tenant_id: str) -> list[str]:
    raw_keys = params.get("source_storage_keys")
    if raw_keys is not None:
        if not isinstance(raw_keys, list | tuple):
            raise ValueError("source_storage_keys must be a list.")
        if raw_keys:
            return [_validate_source_storage_key(tenant_id, str(key)) for key in raw_keys]
    image_keys = params.get("image_keys")
    if image_keys:
        if not isinstance(image_keys, list | tuple):
            raise ValueError("image_keys must be a list.")
        return [_tenant_upload_storage_key(tenant_id, str(key)) for key in image_keys]
    if params.get("source_storage_key"):
        return [_validate_source_storage_key(tenant_id, str(params["source_storage_key"]))]
    return []


def _resolve_bound_image_provider(db, *, task: VideoTask, params: Mapping[str, Any]):
    durable_params = dict(task.params or {})
    if "image_provider" in durable_params:
        raw_provider_name = durable_params["image_provider"]
        if not isinstance(raw_provider_name, str) or not raw_provider_name.strip():
            raise ProviderResolutionError("Image provider binding is invalid.")
        provider_name = raw_provider_name.strip()
        provider = resolve_named_provider(
            db,
            tenant_id=task.tenant_id,
            capability="image",
            provider=provider_name,
        )
    else:
        selection = resolve_with_name(
            db,
            tenant_id=task.tenant_id,
            capability="image",
        )
        provider_name = selection.name
        provider = selection.provider

    request_params = {**params, **durable_params}
    validate_image_provider_request(provider, request_params)
    if "image_provider" not in durable_params:
        task.params = {**durable_params, "image_provider": provider_name}
    return provider


def _input_image_mime_type(storage_key: str, image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if len(image_bytes) >= 12 and image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return _IMAGE_MIME_BY_SUFFIX.get(Path(storage_key).suffix.lower(), "image/png")


def _apimart_safe_input_image_bytes(
    image_bytes: bytes,
    mime_type: str,
    *,
    max_bytes: int | None = None,
) -> tuple[bytes, str]:
    safe_bytes_limit = min(
        _APIMART_INPUT_IMAGE_SAFE_BYTES,
        max_bytes if max_bytes is not None else _APIMART_INPUT_IMAGE_SAFE_BYTES,
    )
    if len(image_bytes) <= safe_bytes_limit:
        return image_bytes, mime_type

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            working = ImageOps.exif_transpose(image).copy()
    except UnidentifiedImageError as exc:
        raise ValueError("Input image is too large and cannot be compressed.") from exc

    if working.mode != "RGB":
        if "A" in working.getbands():
            rgba = working.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            working = background
        else:
            working = working.convert("RGB")

    max_edge = min(_APIMART_INPUT_IMAGE_MAX_EDGE, max(working.size))
    while max_edge >= 32:
        candidate = working.copy()
        candidate.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        for quality in (85, 75, 65, 55, 45):
            buffer = BytesIO()
            candidate.save(buffer, format="JPEG", quality=quality, optimize=True)
            compressed = buffer.getvalue()
            if len(compressed) <= safe_bytes_limit:
                return compressed, "image/jpeg"
        max_edge = int(max_edge * 0.75)

    raise ValueError("Input image is too large after compression.")


def _input_image_data_uri(
    storage_key: str,
    image_bytes: bytes,
    *,
    max_bytes: int | None = None,
) -> str:
    mime_type = _input_image_mime_type(storage_key, image_bytes)
    safe_bytes, safe_mime_type = _apimart_safe_input_image_bytes(
        image_bytes,
        mime_type,
        max_bytes=max_bytes,
    )
    encoded = base64.b64encode(safe_bytes).decode("ascii")
    return f"data:{safe_mime_type};base64,{encoded}"


def _input_image_data_uris(input_images: list[tuple[str, bytes]]) -> list[str]:
    if not input_images:
        return []
    per_image_budget = min(
        _APIMART_INPUT_IMAGE_SAFE_BYTES,
        _APIMART_INPUT_IMAGES_TOTAL_SAFE_BYTES // len(input_images),
    )
    return [
        _input_image_data_uri(storage_key, image_bytes, max_bytes=per_image_budget)
        for storage_key, image_bytes in input_images
    ]


def _requested_image_aspect_ratio(params: Mapping[str, Any]) -> str:
    requested = str(params.get("aspect_ratio") or "").strip()
    if requested:
        return requested
    return image_aspect_ratio_from_legacy_size(str(params.get("image_size") or ""))


def _image_size_evidence(
    *,
    requested_aspect_ratio: str,
    resolved_size: str,
    actual_width: int | None,
    actual_height: int | None,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "requested_aspect_ratio": requested_aspect_ratio,
        "resolved_aspect_ratio": aspect_ratio_from_provider_size(resolved_size),
        "resolved_size": resolved_size,
    }
    if actual_width is not None and actual_height is not None:
        evidence.update(
            {
                "actual_width": actual_width,
                "actual_height": actual_height,
                "actual_size": f"{actual_width}x{actual_height}",
                "actual_aspect_ratio": resolve_image_aspect_ratio(
                    "auto",
                    width=actual_width,
                    height=actual_height,
                ),
            }
        )
    return evidence


def _image_bytes(result: Mapping[str, Any]) -> bytes:
    raw = result.get("image_bytes")
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return base64.b64decode(raw)
    b64_json = result.get("b64_json")
    if isinstance(b64_json, str):
        return base64.b64decode(b64_json)
    raise ValueError("Image provider returned no image bytes.")


def _apply_synthetic_image_label(
    db,
    *,
    tenant_id: str,
    task_id: str,
    image_bytes: bytes,
    visible: bool,
) -> tuple[bytes, dict[str, object]]:
    label_settings, meta, payload = synthetic_label_context(
        db,
        tenant_id=tenant_id,
        content_id=task_id,
        visible=visible,
    )
    return (
        label_artifact_bytes(
            image_bytes,
            kind="image",
            settings=label_settings,
            meta=meta,
            suffix=".png",
            visible=visible,
        ),
        payload,
    )


def _png_from_image(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _poster_font(size: int):
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


def _wrap_poster_text(text: str, *, max_width: int, draw: ImageDraw.ImageDraw, font) -> list[str]:
    units = text.split() if any(char.isspace() for char in text) else list(text)
    separator = " " if any(char.isspace() for char in text) else ""
    if not units:
        return [text]
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


def _draw_poster_text(
    draw: ImageDraw.ImageDraw,
    *,
    box: tuple[int, int, int, int],
    text: str,
    font_size: int,
    fill: tuple[int, int, int],
    stroke_fill: tuple[int, int, int] | None = None,
) -> None:
    if not text:
        return
    font = _poster_font(font_size)
    left, top, right, bottom = box
    max_width = right - left
    lines = _wrap_poster_text(text, max_width=max_width, draw=draw, font=font)
    line_gap = max(6, int(font_size * 0.18))
    text_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=2) for line in lines]
    text_height = sum(item[3] - item[1] for item in text_boxes) + line_gap * max(
        0, len(lines) - 1
    )
    y = top + max(0, (bottom - top - text_height) // 2)
    for line, text_box in zip(lines, text_boxes, strict=True):
        line_width = text_box[2] - text_box[0]
        x = left + max(0, (max_width - line_width) // 2)
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=2 if stroke_fill else 0,
            stroke_fill=stroke_fill or fill,
        )
        y += text_box[3] - text_box[1] + line_gap


def _fit_product_to_box(product: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = box
    max_width = right - left
    max_height = bottom - top
    product_rgba = product.convert("RGBA")
    scale = min(max_width / product_rgba.width, max_height / product_rgba.height)
    size = (
        max(1, int(product_rgba.width * scale)),
        max(1, int(product_rgba.height * scale)),
    )
    return product_rgba.resize(size, Image.Resampling.LANCZOS)


def _compose_ecom_poster(
    source_image_bytes: bytes,
    *,
    template_id: str,
    title: str,
    subtitle: str,
) -> bytes:
    template = _POSTER_TEMPLATES.get(template_id)
    if template is None:
        raise ValueError(f"Unknown poster template: {template_id}")
    try:
        with Image.open(BytesIO(source_image_bytes)) as source_image:
            canvas = Image.new("RGB", _POSTER_SIZE, template["background"])
            draw = ImageDraw.Draw(canvas)
            product_box = template["product_box"]
            panel_margin = 34
            panel_box = (
                product_box[0] - panel_margin,
                product_box[1] - panel_margin,
                product_box[2] + panel_margin,
                product_box[3] + panel_margin,
            )
            draw.rounded_rectangle(panel_box, radius=42, fill=template["panel"])
            draw.rounded_rectangle(
                (84, 1048, 996, 1214),
                radius=30,
                fill=template["accent"],
            )
            product = _fit_product_to_box(source_image, product_box)
            x = product_box[0] + (product_box[2] - product_box[0] - product.width) // 2
            y = product_box[1] + (product_box[3] - product_box[1] - product.height) // 2
            canvas.paste(product, (x, y), product)
            _draw_poster_text(
                draw,
                box=template["title_box"],
                text=title,
                font_size=76,
                fill=template["title"],
                stroke_fill=(80, 20, 20),
            )
            _draw_poster_text(
                draw,
                box=template["subtitle_box"],
                text=subtitle,
                font_size=42,
                fill=template["subtitle"],
            )
            return _png_from_image(canvas)
    except UnidentifiedImageError as exc:
        raise ValueError("Poster source image is invalid.") from exc


def _remove_uniform_background_alpha(rgba: Image.Image) -> Image.Image:
    background_color = rgba.getpixel((0, 0))[:3]
    background = Image.new("RGB", rgba.size, background_color)
    diff = ImageChops.difference(rgba.convert("RGB"), background).convert("L")
    alpha = diff.point(lambda value: 0 if value <= 18 else 255)
    result = rgba.copy()
    result.putalpha(alpha)
    alpha_min, alpha_max = result.getchannel("A").getextrema()
    if alpha_min >= 255 or alpha_max <= 0:
        raise TransparentAlphaMissingError(
            "Transparent cutout output must include transparent alpha pixels."
        )
    return result


def _normalize_ecom_cutout_image(image_bytes: bytes, *, background: str) -> bytes:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if background == "transparent":
                rgba = image.convert("RGBA")
                alpha_min, _alpha_max = rgba.getchannel("A").getextrema()
                if alpha_min >= 255:
                    rgba = _remove_uniform_background_alpha(rgba)
                return _png_from_image(rgba)

            rgba = image.convert("RGBA")
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            white.alpha_composite(rgba)
            return _png_from_image(white.convert("RGB"))
    except UnidentifiedImageError as exc:
        raise ValueError("Image provider returned invalid PNG bytes.") from exc


def _update_progress(db, store, *, tenant_id: str, task: VideoTask, stage: str, progress: int):
    task.status = "done" if stage == "done" else "running"
    task.progress = progress
    db.commit()
    store.update(
        scoped_task_id(tenant_id, task.id),
        status=task.status,
        stage=stage,
        progress=progress,
    )


def _failure_message(exc: Exception) -> str:
    cause = exc.__cause__
    if cause is not None and str(cause):
        return str(cause)
    return str(exc) or exc.__class__.__name__


def _mark_failed(
    *,
    tenant_id: str,
    task_id: str,
    error_message: str,
    error_code: str,
    store,
    storage: ObjectStorage,
) -> None:
    with SessionLocal() as db:
        task = db.get(VideoTask, task_id)
        if task is None or task.tenant_id != tenant_id:
            return
        task.status = "failed"
        task.progress = max(int(task.progress or 0), 1)
        task.error = error_message
        task.error_code = error_code
        task.error_message = error_message
        task.finished_at = datetime.now(UTC)
        release_reserved_quota(db, tenant_id=tenant_id, video_task_id=task_id)
        db.commit()
        prune_video_history_best_effort(
            db,
            tenant_id=tenant_id,
            mode="photo",
            storage=storage,
            keep=20,
        )
    store.update(
        scoped_task_id(tenant_id, task_id),
        status="failed",
        stage="failed",
        error=error_message,
        error_code=error_code,
        error_message=error_message,
    )


def run_image_generation(params: dict[str, Any]) -> dict[str, Any]:
    tenant_id = str(params["tenant_id"])
    task_id = _safe_task_id(str(params["video_task_id"]))

    started = time.monotonic()
    store = build_progress_store(settings.redis_url)
    storage = create_object_storage(settings)
    temp_paths: list[Path] = []

    try:
        with SessionLocal() as db:
            task = db.get(VideoTask, task_id)
            if task is None or task.tenant_id != tenant_id:
                raise ValueError("Video task not found for image generation.")
            durable_params = {
                key: value
                for key, value in (task.params or {}).items()
                if key not in {"image_size", "image_quality"}
            }
            params = {**params, **durable_params}
            # Only the durable task may select the local, unbilled poster path.
            # A queue-only kind must not bypass provider validation or quota settlement.
            ecom_poster = _is_ecom_poster_request(durable_params)
            prompt = str(params.get("script") or params.get("topic") or "").strip()
            if not prompt and not ecom_poster:
                raise ValueError("Image prompt is required.")
            prompt = build_photo_prompt(
                prompt,
                master_prompt=params.get("master_prompt"),
                master_negative_prompt=params.get("master_negative_prompt"),
                negative_prompt=params.get("negative_prompt"),
                similarity_strength=params.get("similarity_strength"),
                creativity_strength=params.get("creativity_strength"),
                subject_strength=params.get("subject_strength"),
            )
            ecom_cutout = _is_ecom_cutout_request(params)
            ecom_model = _is_ecom_model_request(params)
            ecom_background = _ecom_cutout_background(params)
            if ecom_cutout:
                prompt = _ecom_cutout_prompt(prompt, background=ecom_background)
            provider = (
                None
                if ecom_poster
                else _resolve_bound_image_provider(db, task=task, params=params)
            )
            visible_label = bool(
                (task.params or {}).get(
                    "apply_visible_label",
                    params.get("apply_visible_label", False),
                )
            )

            task.started_at = datetime.now(UTC)
            _update_progress(
                db,
                store,
                tenant_id=tenant_id,
                task=task,
                stage="running",
                progress=10,
            )

            input_path = None
            primary_input_bytes: bytes | None = None
            input_image_urls: list[str] = []
            if not ecom_poster:
                source_storage_keys = _source_storage_keys(params, tenant_id)
                if source_storage_keys:
                    input_storage_key = source_storage_keys[0]
                    input_images = [
                        (
                            storage_key,
                            get_tenant_storage_bytes(
                                storage,
                                tenant_id=tenant_id,
                                storage_key=storage_key,
                            ),
                        )
                        for storage_key in source_storage_keys
                    ]
                    primary_input_bytes = input_images[0][1]
                    input_path = _write_temp_image_bytes(
                        primary_input_bytes,
                        suffix=Path(input_storage_key).suffix or ".png",
                        temp_paths=temp_paths,
                    )
                    input_image_urls = _input_image_data_uris(input_images)
                elif params.get("image_key"):
                    input_storage_key = _tenant_upload_storage_key(
                        tenant_id,
                        str(params["image_key"]),
                    )
                    image_bytes = get_tenant_storage_bytes(
                        storage,
                        tenant_id=tenant_id,
                        storage_key=input_storage_key,
                    )
                    primary_input_bytes = image_bytes
                    input_path = _write_temp_image_bytes(
                        image_bytes,
                        suffix=Path(input_storage_key).suffix or ".png",
                        temp_paths=temp_paths,
                    )
                    input_image_urls = [_input_image_data_uri(input_storage_key, image_bytes)]

            _update_progress(
                db,
                store,
                tenant_id=tenant_id,
                task=task,
                stage="composing" if ecom_poster else "generating",
                progress=30,
            )
            provider_payload: dict[str, Any] = {}
            if ecom_poster:
                source_storage_key = _validate_source_storage_key(
                    tenant_id,
                    str(params["source_storage_key"]),
                )
                image_bytes = _compose_ecom_poster(
                    get_tenant_storage_bytes(
                        storage,
                        tenant_id=tenant_id,
                        storage_key=source_storage_key,
                    ),
                    template_id=str(params.get("template_id") or ""),
                    title=str(params.get("title") or ""),
                    subtitle=str(params.get("subtitle") or ""),
                )
                result: Mapping[str, Any] = {
                    "model": "local-pil",
                    "size": f"{_POSTER_SIZE[0]}x{_POSTER_SIZE[1]}",
                    "quality": "local",
                    "mode": "compose",
                }
            else:
                assert provider is not None
                requested_aspect_ratio = _requested_image_aspect_ratio(params)
                input_width, input_height = (
                    _image_dimensions(primary_input_bytes)
                    if primary_input_bytes is not None
                    else (None, None)
                )
                resolved_aspect_ratio = resolve_image_aspect_ratio(
                    requested_aspect_ratio,
                    width=input_width,
                    height=input_height,
                )
                provider_payload = {
                    "prompt": prompt,
                    "size": resolved_aspect_ratio,
                    "resolution": str(params.get("image_resolution") or "1k"),
                    "quality": "high",
                    "n": 1,
                }
                if input_path is not None:
                    provider_payload["input_image_path"] = str(input_path)
                    if input_image_urls:
                        provider_payload["input_image_url"] = input_image_urls[0]
                        provider_payload["image_urls"] = input_image_urls
                if ecom_cutout and ecom_background == "white":
                    provider_payload["background"] = "opaque"
                with generation_heartbeat(
                    store,
                    task_id=scoped_task_id(tenant_id, task_id),
                ):
                    result = asyncio.run(
                        invoke(
                            db,
                            tenant_id=tenant_id,
                            capability="image",
                            provider=provider.__class__.__name__,
                            operation=lambda: provider.generate_image(provider_payload),
                            timeout_seconds=settings.engine_image_provider_timeout_seconds,
                        )
                    )
                image_bytes = _image_bytes(result)
                if ecom_cutout:
                    image_bytes = _normalize_ecom_cutout_image(
                        image_bytes,
                        background=ecom_background,
                    )
            _update_progress(db, store, tenant_id=tenant_id, task=task, stage="got", progress=80)

            output_key = _photo_storage_key(tenant_id, task_id)
            _update_progress(
                db,
                store,
                tenant_id=tenant_id,
                task=task,
                stage="uploading",
                progress=90,
            )
            metadata = {
                "model": result.get("model") or settings.engine_apimart_image_model,
                "size": result.get("size") or provider_payload["size"],
                "quality": result.get("quality") or provider_payload["quality"],
                "mode": result.get("mode") or ("edit" if input_path else "generate"),
            }
            if _is_cover_request(params):
                metadata["purpose"] = "cover"
                metadata["kind"] = "cover"
            if ecom_cutout:
                metadata["kind"] = _ECOM_CUTOUT_KIND
                metadata["background"] = ecom_background
                if params.get("source_asset_id"):
                    metadata["source_asset_id"] = str(params["source_asset_id"])
            if ecom_model:
                metadata["kind"] = _ECOM_MODEL_KIND
                if params.get("gender"):
                    metadata["gender"] = str(params["gender"])
                if params.get("style_id"):
                    metadata["style_id"] = str(params["style_id"])
                if params.get("extra_prompt"):
                    metadata["extra_prompt"] = str(params["extra_prompt"])
                if params.get("source_asset_id"):
                    metadata["source_asset_id"] = str(params["source_asset_id"])
            if ecom_poster:
                metadata["kind"] = _ECOM_POSTER_KIND
                if params.get("template_id"):
                    metadata["template_id"] = str(params["template_id"])
                if params.get("title"):
                    metadata["title"] = str(params["title"])
                if params.get("subtitle"):
                    metadata["subtitle"] = str(params["subtitle"])
                if params.get("source_asset_id"):
                    metadata["source_asset_id"] = str(params["source_asset_id"])

            image_bytes, label_metadata = _apply_synthetic_image_label(
                db,
                tenant_id=tenant_id,
                task_id=task_id,
                image_bytes=image_bytes,
                visible=visible_label,
            )
            metadata["synthetic_label"] = label_metadata

            actual_width, actual_height = _image_dimensions(image_bytes)
            if not ecom_poster:
                resolved_size = str(result.get("size") or provider_payload["size"])
                size_evidence = _image_size_evidence(
                    requested_aspect_ratio=requested_aspect_ratio,
                    resolved_size=resolved_size,
                    actual_width=actual_width,
                    actual_height=actual_height,
                )
                metadata.update(size_evidence)
                task.params = {**(task.params or {}), **size_evidence}

            put_tenant_storage_bytes(
                storage,
                tenant_id=tenant_id,
                storage_key=output_key,
                content=image_bytes,
                content_type="image/png",
            )

            asset = Asset(
                tenant_id=tenant_id,
                type="generated_image",
                source="generated",
                provider=str(result.get("provider") or ("local" if ecom_poster else "apimart")),
                storage_key=output_key,
                mime_type="image/png",
                size_bytes=len(image_bytes),
                width=actual_width,
                height=actual_height,
                status="ready",
                metadata_=metadata,
            )
            db.add(asset)
            db.flush()
            db.add(TaskAsset(video_task_id=task_id, asset_id=asset.id, role="output_image"))

            task.status = "done"
            task.progress = 100
            task.storage_bucket = storage.bucket
            task.storage_key = output_key
            task.thumbnail_key = output_key
            task.content_type = "image/png"
            task.size_bytes = len(image_bytes)
            task.duration_sec = round(time.monotonic() - started, 3)
            task.finished_at = datetime.now(UTC)
            task.error = None
            task.error_code = None
            task.error_message = None
            if not ecom_poster:
                cost_cents = apimart_cost_cents_from_result(result)
                settle_reserved_quota(
                    db,
                    tenant_id=tenant_id,
                    video_task_id=task_id,
                    actual_seconds=1,
                    cost_cents=cost_cents,
                    provider=str(result.get("provider") or "").strip() or None,
                    model=str(result.get("model") or "").strip() or None,
                )
            db.commit()
            prune_video_history_best_effort(
                db,
                tenant_id=tenant_id,
                mode="photo",
                storage=storage,
                keep=20,
            )

            playback_url = presign_tenant_storage_key(
                storage,
                tenant_id=tenant_id,
                storage_key=output_key,
                expires_in=settings.engine_s3_presign_ttl,
            )
            download_url = presign_tenant_storage_key(
                storage,
                tenant_id=tenant_id,
                storage_key=output_key,
                expires_in=settings.engine_s3_presign_ttl,
                download_filename=f"{task_id}.png",
            )
            store.update(
                scoped_task_id(tenant_id, task_id),
                status="done",
                stage="done",
                progress=100,
                playback_url=playback_url,
                download_url=download_url,
                thumbnail_url=playback_url,
            )
            return {
                "status": "SUCCESS",
                "storage_key": output_key,
                "playback_url": playback_url,
                "download_url": download_url,
            }
    except Exception as exc:
        error_message = _failure_message(exc)
        error_code = classify_image_error(exc)
        logger.warning(
            "image_generation_failed",
            tenant_id=tenant_id,
            task_id=task_id,
            error_code=error_code,
            error=error_message,
        )
        _mark_failed(
            tenant_id=tenant_id,
            task_id=task_id,
            error_message=error_message,
            error_code=error_code,
            store=store,
            storage=storage,
        )
        return {
            "status": "FAILURE",
            "error_code": error_code,
            "error_message": error_message,
        }
    finally:
        for path in temp_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("image_generation_temp_cleanup_failed", path=str(path))


def _ecom_replicate_storage_key(tenant_id: str, job_id: str, output_index: int) -> str:
    return (
        f"tenants/{_safe_task_id(tenant_id)}/ecom-replicate/"
        f"{_safe_task_id(job_id)}/{output_index:02d}.png"
    )


def _image_dimensions(image_bytes: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            corrected = ImageOps.exif_transpose(image)
            corrected.load()
            return int(corrected.width), int(corrected.height)
    except UnidentifiedImageError:
        return None, None


def _ecom_replicate_result_cost_cents(result: Mapping[str, Any]) -> int:
    return apimart_cost_cents_from_result(result) or int(
        round(float(settings.engine_ecom_replicate_render_cny_per_image) * 100)
    )


def _ecom_replicate_provider_payload(
    *,
    output: EcomReplicateOutput,
    reference: Asset,
    product: Asset,
    storage: ObjectStorage,
) -> dict[str, Any]:
    reference_bytes = get_tenant_storage_bytes(
        storage,
        tenant_id=output.tenant_id,
        storage_key=reference.storage_key,
    )
    product_bytes = get_tenant_storage_bytes(
        storage,
        tenant_id=output.tenant_id,
        storage_key=product.storage_key,
    )
    return {
        "prompt": output.prompt or "",
        "size": output.requested_size,
        "quality": settings.engine_ecom_replicate_quality,
        "n": 1,
        "image_urls": [
            _input_image_data_uri(reference.storage_key, reference_bytes),
            _input_image_data_uri(product.storage_key, product_bytes),
        ],
    }


def _max_ecom_replicate_attempts() -> int:
    max_retry = max(0, int(getattr(settings, "engine_ecom_replicate_max_retry", 2) or 0))
    return max_retry + 1


class EcomReplicateProductMismatch(RuntimeError):
    pass


def _ecom_replicate_product_identity(output: EcomReplicateOutput) -> dict[str, str]:
    mapping = output.template_mapping_json or {}
    raw = mapping.get("product_identity") if isinstance(mapping, Mapping) else None
    if not isinstance(raw, Mapping):
        raise ValueError("Replicate output product identity is missing.")
    identity = {
        str(key): str(value).strip()
        for key, value in raw.items()
        if str(key).strip() and str(value).strip()
    }
    if not identity.get("main_color"):
        raise ValueError("Replicate output product main color is missing.")
    return identity


def _record_ecom_replicate_validation(
    output: EcomReplicateOutput,
    result: Mapping[str, Any],
    *,
    attempt: int,
) -> dict[str, Any]:
    previous = output.validation_json or {}
    attempts = list(previous.get("attempts") or []) if isinstance(previous, Mapping) else []
    record = {
        "attempt": attempt,
        "status": "passed" if result.get("passed") is True else "failed",
        "passed": result.get("passed") is True,
        "checks": dict(result.get("checks") or {}),
        "reason": str(result.get("reason") or "").strip(),
        "provider": str(result.get("provider") or "apimart"),
        "model": str(result.get("model") or settings.engine_apimart_reverse_prompt_model),
    }
    attempts.append(record)
    output.validation_json = {**record, "attempts": attempts}
    return record


def _render_ecom_replicate_output_once(
    db,
    *,
    job: EcomReplicateJob,
    output: EcomReplicateOutput,
    provider,
    validator,
    storage: ObjectStorage,
    attempt: int,
) -> None:
    reference = db.get(Asset, output.reference_asset_id)
    product = db.get(Asset, output.product_asset_id)
    if reference is None or product is None:
        raise ValueError("Replicate output source asset is missing.")

    provider_payload = _ecom_replicate_provider_payload(
        output=output,
        reference=reference,
        product=product,
        storage=storage,
    )
    result = asyncio.run(
        invoke(
            db,
            tenant_id=job.tenant_id,
            capability="image",
            provider=provider.__class__.__name__,
            operation=lambda payload=provider_payload: provider.generate_image(payload),
            timeout_seconds=settings.engine_image_provider_timeout_seconds,
        )
    )
    result_dict = dict(result)
    image_bytes = _image_bytes(result_dict)
    width, height = _image_dimensions(image_bytes)
    cost_cents = _ecom_replicate_result_cost_cents(result_dict)
    record_render_cost(
        db,
        tenant_id=job.tenant_id,
        result=result_dict,
        fallback_cost_cents=cost_cents,
    )

    product_identity = _ecom_replicate_product_identity(output)
    product_bytes = get_tenant_storage_bytes(
        storage,
        tenant_id=job.tenant_id,
        storage_key=product.storage_key,
    )
    validation_result = asyncio.run(
        invoke(
            db,
            tenant_id=job.tenant_id,
            capability="reverse_prompt",
            provider=validator.__class__.__name__,
            operation=lambda: validator.validate_product_fidelity(
                {
                    "product_image_url": _input_image_data_uri(
                        product.storage_key,
                        product_bytes,
                    ),
                    "rendered_image_url": _input_image_data_uri(
                        "rendered-output.png",
                        image_bytes,
                    ),
                    "product_identity": product_identity,
                }
            ),
            timeout_seconds=settings.engine_image_provider_timeout_seconds,
        )
    )
    validation_dict = dict(validation_result)
    record_analysis_cost(db, tenant_id=job.tenant_id, result=validation_dict)
    validation = _record_ecom_replicate_validation(
        output,
        validation_dict,
        attempt=attempt,
    )
    if not validation["passed"]:
        raise EcomReplicateProductMismatch(
            validation["reason"] or "Rendered product does not match the user's product."
        )

    output_key = _ecom_replicate_storage_key(job.tenant_id, job.id, output.index)
    put_tenant_storage_bytes(
        storage,
        tenant_id=job.tenant_id,
        storage_key=output_key,
        content=image_bytes,
        content_type="image/png",
    )

    asset = Asset(
        tenant_id=job.tenant_id,
        type="generated_image",
        source="generated",
        provider=str(result_dict.get("provider") or "apimart"),
        storage_key=output_key,
        mime_type="image/png",
        size_bytes=len(image_bytes),
        width=width,
        height=height,
        status="ready",
        metadata_={
            "kind": "ecom_replicate",
            "job_id": job.id,
            "output_id": output.id,
            "output_index": output.index,
            "theme": output.theme,
            "requested_size": output.requested_size,
            "requested_aspect": output.requested_aspect,
            "actual_width": width,
            "actual_height": height,
            "provider_raw_saved": True,
        },
    )
    db.add(asset)
    db.flush()
    output.status = "succeeded"
    output.asset_id = asset.id
    output.storage_key = output_key
    output.actual_width = width
    output.actual_height = height
    output.validation_json = {
        **dict(output.validation_json or {}),
        "raw_provider_bytes_saved": True,
        "requested_size": output.requested_size,
        "actual_width": width,
        "actual_height": height,
    }
    output.error_code = None
    output.error_message = None
    output.updated_at = datetime.now(UTC)


def _mark_ecom_replicate_job_finished(
    db,
    job: EcomReplicateJob,
    outputs: list[EcomReplicateOutput],
) -> None:
    succeeded = sum(1 for output in outputs if output.status == "succeeded")
    failed = sum(1 for output in outputs if output.status == "failed")
    if failed == 0 and succeeded == len(outputs):
        job.status = "completed"
        job.error_code = None
        job.error_message = None
    elif succeeded > 0:
        job.status = "partial_failed"
        job.error_code = "ECOM_REPLICATE_PARTIAL_FAILED"
        job.error_message = f"{failed} outputs failed."
    else:
        job.status = "failed"
        job.error_code = "ECOM_REPLICATE_FAILED"
        job.error_message = "All replicate outputs failed."
    job.finished_at = datetime.now(UTC)
    job.updated_at = datetime.now(UTC)
    db.flush()


def run_ecom_replicate_generation(job_id: str, output_index: int | None = None) -> dict[str, Any]:
    storage = create_object_storage(settings)
    store = build_progress_store(settings.redis_url)
    with SessionLocal() as db:
        job = db.get(EcomReplicateJob, job_id)
        if job is None:
            raise ValueError("E-commerce replicate job not found.")
        if job.status != "generating":
            return {"status": "SKIPPED", "job_id": job_id, "job_status": job.status}
        job.status = "generating"
        job.started_at = job.started_at or datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        db.flush()

        provider = resolve_named_provider(
            db,
            tenant_id=job.tenant_id,
            capability="image",
            provider="apimart",
        )
        validator = resolve_named_provider(
            db,
            tenant_id=job.tenant_id,
            capability="reverse_prompt",
            provider="apimart-gemini",
        )
        outputs_query = select(EcomReplicateOutput).where(EcomReplicateOutput.job_id == job.id)
        if output_index is not None:
            outputs_query = outputs_query.where(EcomReplicateOutput.index == output_index)
        outputs = list(
            db.scalars(
                outputs_query.order_by(EcomReplicateOutput.index.asc())
            )
        )
        if not outputs:
            job.status = "failed"
            job.error_code = "ECOM_REPLICATE_NO_OUTPUTS"
            job.error_message = "Replicate job has no outputs."
            job.finished_at = datetime.now(UTC)
            db.commit()
            return {"status": "FAILURE", "job_id": job.id, "error_code": job.error_code}

        for output in outputs:
            if output.status == "succeeded":
                continue
            last_error: Exception | None = None
            max_attempts = _max_ecom_replicate_attempts()
            for attempt in range(max_attempts):
                attempt_started_at = datetime.now(UTC)
                output.status = "generating"
                output.updated_at = attempt_started_at
                job.updated_at = attempt_started_at
                # Publish a durable lease before a provider call that can run for 1500s.
                db.commit()
                try:
                    with generation_heartbeat(
                        store,
                        task_id=scoped_task_id(job.tenant_id, job.id),
                    ):
                        _render_ecom_replicate_output_once(
                            db,
                            job=job,
                            output=output,
                            provider=provider,
                            validator=validator,
                            storage=storage,
                            attempt=attempt + 1,
                        )
                    db.commit()
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    output.retry_count = int(output.retry_count or 0) + 1
                    output.error_code = (
                        "ECOM_REPLICATE_PRODUCT_MISMATCH"
                        if isinstance(exc, EcomReplicateProductMismatch)
                        else "ECOM_REPLICATE_RENDER_FAILED"
                    )
                    output.error_message = _failure_message(exc)
                    output.updated_at = datetime.now(UTC)
                    logger.warning(
                        "ecom_replicate_output_attempt_failed",
                        tenant_id=job.tenant_id,
                        job_id=job.id,
                        output_id=output.id,
                        output_index=output.index,
                        attempt=attempt + 1,
                        error=output.error_message,
                    )
                    if attempt + 1 >= max_attempts:
                        output.status = "failed"
                        db.commit()
                    else:
                        output.status = "planned"
                        db.flush()
            if last_error is not None and output.status != "failed":
                output.status = "failed"
                output.error_message = _failure_message(last_error)
                db.commit()

        outputs = list(
            db.scalars(
                select(EcomReplicateOutput)
                .where(EcomReplicateOutput.job_id == job.id)
                .order_by(EcomReplicateOutput.index.asc())
            )
        )
        _mark_ecom_replicate_job_finished(db, job, outputs)
        db.commit()
        return {
            "status": "SUCCESS" if job.status == "completed" else "PARTIAL_FAILURE",
            "job_id": job.id,
            "job_status": job.status,
        }


@celery_app.task(bind=True, name="app.workers.image_gen.generate")
def generate_image_task(self, params: dict[str, Any]) -> dict[str, Any]:
    payload = dict(params)
    payload.setdefault("video_task_id", self.request.id)
    claim = claim_video_task_for_worker(
        tenant_id=str(payload["tenant_id"]),
        task_id=str(payload["video_task_id"]),
        session_factory=SessionLocal,
    )
    if not claim.claimed:
        return {"task_id": str(payload["video_task_id"]), "status": claim.status}
    logger.info(
        "image_generation.queued",
        task_id=payload.get("video_task_id"),
        tenant_id=payload.get("tenant_id"),
        visible_label=bool(payload.get("apply_visible_label", False)),
    )
    return run_image_generation(payload)


@celery_app.task(bind=True, name="app.workers.image_gen.generate_ecom_replicate")
def generate_ecom_replicate_task(
    self,
    job_id: str,
    output_index: int | None = None,
) -> dict[str, Any]:
    task_job_id = str(job_id or self.request.id)
    claim = claim_ecom_replicate_job_for_worker(
        job_id=task_job_id,
        session_factory=SessionLocal,
    )
    if not claim.claimed:
        return {"job_id": task_job_id, "status": claim.status}
    logger.info(
        "ecom_replicate_generation.queued",
        job_id=task_job_id,
        output_index=output_index,
    )
    return run_ecom_replicate_generation(task_job_id, output_index=output_index)
