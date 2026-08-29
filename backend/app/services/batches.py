from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import tenant_storage_key
from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import Asset, BatchJob, BgmLibraryTrack, BrandVoice, User, VideoTask, Voice
from app.providers.url_guard import ProviderUrlError, ensure_public_https_url
from app.schemas.batches import BatchRequest
from app.services.bgm_library import ensure_default_bgm_tracks
from app.services.quota import (
    QuotaEstimate,
    active_subscription,
    estimate_seedance_i2v_quota,
    estimate_video_gen_quota,
    remaining_credits,
    seedance_i2v_billable_seconds,
    seedance_i2v_target_seconds,
)
from app.services.storage.base import ObjectStorage
from app.services.storage.keys import (
    presign_tenant_storage_key,
    put_tenant_storage_bytes,
)
from app.services.voices import resolve_narration_voice

_IMAGE_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_VIDEO_GEN_IMAGE_TYPES = {"avatar_image", "product_image", "generated_image", "cover"}
_TERMINAL_TASK_STATUSES = {"done", "failed", "cancelled"}
_MAX_IMAGE_REDIRECTS = 3
_DOWNLOAD_CHUNK_SIZE = 64 * 1024


class BatchImageDownloadError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class EcomBatchRow:
    product_name: str
    selling_points: str
    image_asset_id: str | None
    image_url: str | None


@dataclass(frozen=True)
class PromptBatchRow:
    prompt: str
    image_asset_id: str | None


def ecom_rows_or_raise(payload: BatchRequest) -> list[EcomBatchRow]:
    rows: list[EcomBatchRow] = []
    for index, raw in enumerate(payload.rows):
        product_name = str(raw.get("product_name") or "").strip()
        selling_points = str(raw.get("selling_points") or "").strip()
        image_asset_id = str(raw.get("image_asset_id") or "").strip() or None
        image_url = str(raw.get("image_url") or "").strip() or None
        if not product_name:
            raise _row_error(index, "product_name is required")
        if not selling_points:
            raise _row_error(index, "selling_points is required")
        if bool(image_asset_id) == bool(image_url):
            raise _row_error(index, "exactly one of image_asset_id or image_url is required")
        rows.append(
            EcomBatchRow(
                product_name=product_name,
                selling_points=selling_points,
                image_asset_id=image_asset_id,
                image_url=image_url,
            )
        )
    return rows


def prompt_rows_or_raise(payload: BatchRequest) -> list[PromptBatchRow]:
    rows: list[PromptBatchRow] = []
    for index, raw in enumerate(payload.rows):
        prompt = str(raw.get("prompt") or "").strip()
        image_asset_id = str(raw.get("image_asset_id") or "").strip() or None
        if not prompt:
            raise _row_error(index, "prompt is required")
        rows.append(PromptBatchRow(prompt=prompt, image_asset_id=image_asset_id))
    return rows


def _row_error(index: int, message: str) -> AppError:
    return AppError(
        f"Invalid batch row {index}: {message}.",
        code="BATCH_ROW_INVALID",
        status_code=422,
    )


def estimate_batch(
    db: Session,
    *,
    tenant_id: str,
    payload: BatchRequest,
) -> tuple[QuotaEstimate, int, int]:
    if payload.kind == "ecom_table":
        rows = ecom_rows_or_raise(payload)
        target_seconds = seedance_i2v_target_seconds(payload.common.duration_sec)
        estimates = [
            estimate_seedance_i2v_quota(
                db,
                tenant_id=tenant_id,
                script=ecom_topic(row),
                speed=payload.common.speed,
                estimated_seconds=seedance_i2v_billable_seconds(target_seconds),
                resolution=payload.common.resolution,
            )
            for row in rows
        ]
        per_row = max(estimates, key=lambda estimate: estimate.reservation_units)
        total_units = sum(estimate.reservation_units for estimate in estimates)
    else:
        prompt_rows_or_raise(payload)
        per_row = estimate_video_gen_quota(
            db,
            tenant_id=tenant_id,
            duration_sec=int(payload.common.duration_sec or 5),
            resolution=payload.common.resolution,
        )
        total_units = per_row.reservation_units * len(payload.rows)
    subscription = active_subscription(db, tenant_id)
    return per_row, total_units, remaining_credits(subscription)


def ecom_topic(row: EcomBatchRow) -> str:
    return f"{row.product_name}\n{row.selling_points}".strip()


def image_asset_or_raise(db: Session, *, tenant_id: str, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if (
        asset is None
        or asset.tenant_id != tenant_id
        or asset.status != "ready"
        or asset.deleted_at is not None
        or asset.type not in _VIDEO_GEN_IMAGE_TYPES
        or not str(asset.mime_type or "image/").startswith("image/")
    ):
        raise AppError("Image asset not found.", code="IMAGE_ASSET_NOT_FOUND", status_code=404)
    return asset


def validate_voice_or_raise(
    db: Session,
    *,
    user: User,
    voice_id: str | None,
    requested_at: datetime | None = None,
) -> tuple[Voice | None, BrandVoice | None]:
    return resolve_narration_voice(
        db,
        user=user,
        voice_id=voice_id,
        requested_at=requested_at,
    )


def tenant_relative_upload_key(asset: Asset, *, tenant_id: str) -> str:
    prefix = f"tenants/{tenant_id}/"
    if (
        not asset.storage_key.startswith(prefix)
        or ".." in asset.storage_key
        or "\\" in asset.storage_key
    ):
        raise AppError(
            "Image asset storage key is invalid.",
            code="IMAGE_ASSET_INVALID",
            status_code=422,
        )
    relative = asset.storage_key.removeprefix(prefix)
    if not relative.startswith("uploads/"):
        raise AppError(
            "Image asset must be stored under uploads.",
            code="IMAGE_ASSET_INVALID",
            status_code=422,
        )
    return relative


def video_gen_reference_assets_or_raise(
    db: Session,
    *,
    tenant_id: str,
    asset_ids: list[str],
) -> list[Asset]:
    assets = list(
        db.scalars(
            select(Asset).where(
                Asset.id.in_(asset_ids),
                Asset.tenant_id == tenant_id,
                Asset.status == "ready",
                Asset.deleted_at.is_(None),
            )
        )
    )
    by_id = {asset.id: asset for asset in assets}
    ordered = [by_id.get(asset_id) for asset_id in asset_ids]
    if any(asset is None for asset in ordered):
        raise AppError(
            "Reference image not found.",
            code="REFERENCE_IMAGE_NOT_FOUND",
            status_code=404,
        )
    resolved = [asset for asset in ordered if asset is not None]
    if any(
        asset.type not in _VIDEO_GEN_IMAGE_TYPES
        or not str(asset.mime_type or "image/").startswith("image/")
        for asset in resolved
    ):
        raise AppError(
            "Reference image not found.",
            code="REFERENCE_IMAGE_NOT_FOUND",
            status_code=404,
        )
    return resolved


def bgm_asset_or_track_or_raise(
    db: Session,
    *,
    tenant_id: str,
    bgm: dict[str, str] | None,
    storage: ObjectStorage,
) -> Asset | None:
    if not bgm:
        return None
    if bgm.get("source") == "upload":
        asset = db.get(Asset, str(bgm.get("asset_id") or ""))
        if (
            asset is None
            or asset.tenant_id != tenant_id
            or asset.type not in {"audio", "bgm"}
            or asset.status != "ready"
            or asset.deleted_at is not None
        ):
            raise AppError("BGM asset not found.", code="BGM_ASSET_NOT_FOUND", status_code=404)
        return asset
    if bgm.get("source") == "library":
        ensure_default_bgm_tracks(db, storage=storage)
        track = db.get(BgmLibraryTrack, str(bgm.get("track_id") or ""))
        if track is None or not track.is_active:
            raise AppError("BGM track not found.", code="BGM_TRACK_NOT_FOUND", status_code=404)
        return None
    raise AppError("Invalid BGM source.", code="VALIDATION_ERROR", status_code=422)


def download_image_url_to_asset(
    db: Session,
    *,
    tenant_id: str,
    image_url: str,
    storage: ObjectStorage,
) -> Asset:
    final_url, response = _get_public_image_response(image_url)
    try:
        content_type = str(response.headers.get("content-type") or "").split(";")[0].lower()
        extension = _IMAGE_MIME_EXTENSIONS.get(content_type)
        if extension is None:
            raise BatchImageDownloadError("类型不符")
        content = _read_limited_response(response, max_bytes=settings.upload_max_bytes)
        if not content:
            raise BatchImageDownloadError("空文件")
    finally:
        _close_response(response)
    storage_key = tenant_storage_key(tenant_id, f"uploads/{uuid4().hex}{extension}")
    put_tenant_storage_bytes(
        storage,
        tenant_id=tenant_id,
        storage_key=storage_key,
        content=content,
        content_type=content_type,
    )
    asset = Asset(
        tenant_id=tenant_id,
        type="product_image",
        source="upload",
        provider="external_url",
        storage_key=storage_key,
        mime_type=content_type,
        size_bytes=len(content),
        status="ready",
        metadata_={"source_url_host": urlparse(final_url).hostname or ""},
    )
    db.add(asset)
    db.flush()
    return asset


def _get_public_image_response(image_url: str) -> tuple[str, requests.Response]:
    current_url = image_url
    redirects = 0
    while True:
        _ensure_batch_public_url(current_url)
        try:
            response = requests.get(
                current_url,
                timeout=settings.engine_apimart_request_timeout_seconds,
                stream=True,
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise BatchImageDownloadError("超时") from exc
        except requests.RequestException as exc:
            raise BatchImageDownloadError("下载失败") from exc

        if not _is_redirect(response):
            try:
                response.raise_for_status()
            except requests.Timeout as exc:
                _close_response(response)
                raise BatchImageDownloadError("超时") from exc
            except requests.RequestException as exc:
                _close_response(response)
                raise BatchImageDownloadError("下载失败") from exc
            return current_url, response

        location = response.headers.get("location")
        _close_response(response)
        if not location:
            raise BatchImageDownloadError("下载失败")
        if redirects >= _MAX_IMAGE_REDIRECTS:
            raise BatchImageDownloadError("重定向过多")
        current_url = urljoin(current_url, location)
        _ensure_batch_public_url(current_url)
        redirects += 1


def _ensure_batch_public_url(url: str) -> None:
    try:
        ensure_public_https_url(url)
    except ProviderUrlError as exc:
        raise BatchImageDownloadError("地址不安全") from exc


def _is_redirect(response: requests.Response) -> bool:
    return response.status_code in {301, 302, 303, 307, 308}


def _read_limited_response(response: requests.Response, *, max_bytes: int) -> bytes:
    declared_length = str(response.headers.get("content-length") or "").strip()
    if declared_length:
        try:
            if int(declared_length) > max_bytes:
                raise BatchImageDownloadError("超大小")
        except ValueError:
            pass
    content = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > max_bytes:
                raise BatchImageDownloadError("超大小")
    except requests.Timeout as exc:
        raise BatchImageDownloadError("超时") from exc
    except requests.RequestException as exc:
        raise BatchImageDownloadError("下载失败") from exc
    return bytes(content)


def _close_response(response: requests.Response) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


def refresh_batch_job(
    db: Session,
    *,
    batch_id: str | None,
    tenant_id: str | None = None,
) -> BatchJob | None:
    if not batch_id:
        return None
    db.flush()
    batch_query = select(BatchJob).where(BatchJob.id == batch_id)
    if tenant_id is not None:
        batch_query = batch_query.where(BatchJob.tenant_id == tenant_id)
    batch = db.scalar(batch_query.with_for_update())
    if batch is None:
        return None
    rows = list(
        db.execute(
            select(VideoTask.status, func.count())
            .where(
                VideoTask.batch_id == batch_id,
                VideoTask.tenant_id == batch.tenant_id,
            )
            .group_by(VideoTask.status)
        )
    )
    counts = {str(status): int(count) for status, count in rows}
    succeeded = counts.get("done", 0)
    failed = counts.get("failed", 0) + counts.get("cancelled", 0)
    terminal = sum(counts.get(status, 0) for status in _TERMINAL_TASK_STATUSES)
    total = int(batch.total or sum(counts.values()) or 0)
    batch.total = total
    batch.succeeded = succeeded
    batch.failed = failed
    if total <= 0:
        batch.status = "failed"
    elif terminal < total:
        batch.status = "running"
    elif counts.get("cancelled", 0) == total:
        batch.status = "cancelled"
    elif succeeded == total:
        batch.status = "completed"
    elif succeeded == 0:
        batch.status = "failed"
    else:
        batch.status = "partial_failed"
    batch.updated_at = datetime.now(UTC)
    return batch


def batch_row_index(task: VideoTask) -> int:
    value = (task.params or {}).get("batch_row_index", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def output_video_url(task: VideoTask, *, storage: ObjectStorage) -> str | None:
    if task.status != "done" or not task.storage_key:
        return None
    return presign_tenant_storage_key(
        storage,
        tenant_id=task.tenant_id,
        storage_key=task.storage_key,
        expires_in=settings.engine_s3_presign_ttl,
        download_filename=None,
    )
