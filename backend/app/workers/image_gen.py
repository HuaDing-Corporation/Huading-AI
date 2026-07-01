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
from PIL import Image, ImageChops, ImageDraw, ImageFont, UnidentifiedImageError

from app.api.deps import scoped_task_id
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Asset, TaskAsset, VideoTask
from app.db.session import SessionLocal
from app.providers.base import invoke, resolve
from app.services.history import prune_video_history_best_effort
from app.services.progress import build_progress_store
from app.services.quota import release_reserved_quota, settle_reserved_quota
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage
from app.services.synthetic_label import (
    label_artifact_bytes,
    synthetic_label_context,
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
_SOURCE_IMAGE_STORAGE_KEY_RE = re.compile(
    r"^tenants/[A-Za-z0-9_-]+/[A-Za-z0-9_./-]+\.(?:jpg|jpeg|png|webp)$"
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


def _write_temp_source_image(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    source_storage_key: str,
    temp_paths: list[Path],
) -> Path:
    storage_key = _validate_source_storage_key(tenant_id, source_storage_key)
    suffix = Path(storage_key).suffix or ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(storage.get_bytes(storage_key))
        path = Path(handle.name)
    temp_paths.append(path)
    return path


def _write_temp_input_image(
    storage: ObjectStorage,
    *,
    tenant_id: str,
    image_key: str,
    temp_paths: list[Path],
) -> Path:
    storage_key = _tenant_upload_storage_key(tenant_id, image_key)
    suffix = Path(image_key).suffix or ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(storage.get_bytes(storage_key))
        path = Path(handle.name)
    temp_paths.append(path)
    return path


def _presigned_input_url(storage: ObjectStorage, storage_key: str) -> str:
    return storage.presign_get_url(
        storage_key,
        expires_in=settings.engine_s3_presign_ttl,
    )


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
) -> tuple[bytes, dict[str, object]]:
    label_settings, meta, payload = synthetic_label_context(
        db,
        tenant_id=tenant_id,
        content_id=task_id,
    )
    return (
        label_artifact_bytes(
            image_bytes,
            kind="image",
            settings=label_settings,
            meta=meta,
            suffix=".png",
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
        ecom_poster = _is_ecom_poster_request(params)
        prompt = str(params.get("script") or params.get("topic") or "").strip()
        if not prompt and not ecom_poster:
            raise ValueError("Image prompt is required.")
        ecom_cutout = _is_ecom_cutout_request(params)
        ecom_model = _is_ecom_model_request(params)
        ecom_background = _ecom_cutout_background(params)
        if ecom_cutout:
            prompt = _ecom_cutout_prompt(prompt, background=ecom_background)

        with SessionLocal() as db:
            task = db.get(VideoTask, task_id)
            if task is None or task.tenant_id != tenant_id:
                raise ValueError("Video task not found for image generation.")

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
            input_image_url = None
            if not ecom_poster:
                if params.get("source_storage_key"):
                    input_storage_key = _validate_source_storage_key(
                        tenant_id,
                        str(params["source_storage_key"]),
                    )
                    input_path = _write_temp_source_image(
                        storage,
                        tenant_id=tenant_id,
                        source_storage_key=input_storage_key,
                        temp_paths=temp_paths,
                    )
                    input_image_url = _presigned_input_url(storage, input_storage_key)
                elif params.get("image_key"):
                    input_storage_key = _tenant_upload_storage_key(
                        tenant_id,
                        str(params["image_key"]),
                    )
                    input_path = _write_temp_input_image(
                        storage,
                        tenant_id=tenant_id,
                        image_key=str(params["image_key"]),
                        temp_paths=temp_paths,
                    )
                    input_image_url = _presigned_input_url(storage, input_storage_key)

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
                    storage.get_bytes(source_storage_key),
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
                provider = resolve(db, tenant_id=tenant_id, capability="image")
                provider_payload = {
                    "prompt": prompt,
                    "size": params.get("image_size") or "1024x1024",
                    "quality": params.get("image_quality") or "medium",
                    "n": 1,
                }
                if input_path is not None:
                    provider_payload["input_image_path"] = str(input_path)
                    if input_image_url:
                        provider_payload["input_image_url"] = input_image_url
                        provider_payload["image_urls"] = [input_image_url]
                if ecom_cutout and ecom_background == "white":
                    provider_payload["background"] = "opaque"
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
            )
            metadata["synthetic_label"] = label_metadata

            storage.put_bytes(output_key, image_bytes, content_type="image/png")

            asset = Asset(
                tenant_id=tenant_id,
                type="generated_image",
                source="generated",
                provider=str(result.get("provider") or ("local" if ecom_poster else "apimart")),
                storage_key=output_key,
                mime_type="image/png",
                size_bytes=len(image_bytes),
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
                settle_reserved_quota(
                    db,
                    tenant_id=tenant_id,
                    video_task_id=task_id,
                    actual_seconds=1,
                    cost_cents=0,
                )
            db.commit()
            prune_video_history_best_effort(
                db,
                tenant_id=tenant_id,
                mode="photo",
                storage=storage,
                keep=20,
            )

            playback_url = storage.presign_get_url(
                output_key,
                expires_in=settings.engine_s3_presign_ttl,
            )
            download_url = storage.presign_get_url(
                output_key,
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


@celery_app.task(bind=True, name="app.workers.image_gen.generate")
def generate_image_task(self, params: dict[str, Any]) -> dict[str, Any]:
    payload = dict(params)
    payload.setdefault("video_task_id", self.request.id)
    return run_image_generation(payload)
