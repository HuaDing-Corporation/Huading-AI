from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import PublishRecord, VideoTask
from app.services.copy import generate_publish_copy
from app.services.quota import charge_copy_quota
from app.services.storage.base import ObjectStorage
from app.services.storage.keys import presign_tenant_storage_key

_DEFAULT_PLATFORMS: list[dict[str, object]] = [
    {
        "id": "douyin",
        "name": "抖音",
        "title_max": 55,
        "body_max": 1000,
        "hashtag_min": 3,
        "hashtag_max": 5,
        "publish_url": "https://creator.douyin.com/",
        "cover_ratio": "3:4",
        "notes": "标题控制在55字以内，建议3-5个话题。",
    },
    {
        "id": "kuaishou",
        "name": "快手",
        "title_max": 50,
        "body_max": 1000,
        "hashtag_min": 3,
        "hashtag_max": 5,
        "publish_url": "https://cp.kuaishou.com/",
        "cover_ratio": "3:4",
        "notes": "标题简短直接，建议3-5个话题。",
    },
    {
        "id": "wxchannels",
        "name": "视频号",
        "title_max": 30,
        "body_max": 1000,
        "hashtag_min": 1,
        "hashtag_max": 3,
        "publish_url": "https://channels.weixin.qq.com/",
        "cover_ratio": "3:4",
        "notes": "短标题、少量话题，表达克制。",
    },
    {
        "id": "xiaohongshu",
        "name": "小红书",
        "title_max": 20,
        "body_max": 1000,
        "hashtag_min": 5,
        "hashtag_max": 10,
        "publish_url": "https://creator.xiaohongshu.com/",
        "cover_ratio": "3:4",
        "notes": "标题20字以内，正文更完整，话题更丰富。",
    },
    {
        "id": "bilibili",
        "name": "B站",
        "title_max": 80,
        "body_max": 2000,
        "hashtag_min": 1,
        "hashtag_max": 5,
        "publish_url": "https://member.bilibili.com/platform/upload/video/frame",
        "cover_ratio": "16:9",
        "notes": "标题可更完整，正文适合补充分区和简介信息。",
    },
]

_VALID_PLATFORM_IDS = {str(item["id"]) for item in _DEFAULT_PLATFORMS}


def publish_platforms() -> list[dict[str, object]]:
    if not settings.publish_platforms_json.strip():
        return [dict(item) for item in _DEFAULT_PLATFORMS]
    try:
        items = json.loads(settings.publish_platforms_json)
    except json.JSONDecodeError as exc:
        raise AppError(
            "Publish platform config is invalid.",
            code="PUBLISH_PLATFORM_CONFIG_INVALID",
            status_code=500,
        ) from exc
    if not isinstance(items, list):
        raise AppError(
            "Publish platform config is invalid.",
            code="PUBLISH_PLATFORM_CONFIG_INVALID",
            status_code=500,
        )
    return [dict(item) for item in items]


def platform_by_id(platform_id: str) -> dict[str, object]:
    for platform in publish_platforms():
        if platform.get("id") == platform_id:
            return platform
    raise AppError(
        "Publish platform not found.",
        code="PUBLISH_PLATFORM_NOT_FOUND",
        status_code=422,
    )


def _source_text(task: VideoTask) -> str:
    return (task.script or task.topic or "发布素材").strip() or "发布素材"


def _source_matches_kind(task: VideoTask, source_kind: str) -> bool:
    content_type = str(task.content_type or "")
    is_image = (
        content_type.startswith("image/")
        or task.mode == "photo"
        or task.video_mode == "photo"
    )
    if source_kind == "image":
        return is_image
    return not is_image


def source_task_or_404(
    db: Session,
    *,
    tenant_id: str,
    source_kind: str,
    source_task_id: str,
) -> VideoTask:
    task = db.scalar(
        select(VideoTask).where(
            VideoTask.id == source_task_id,
            VideoTask.tenant_id == tenant_id,
            VideoTask.deleted_at.is_(None),
        )
    )
    if (
        task is None
        or task.status != "done"
        or not task.storage_key
        or not _source_matches_kind(task, source_kind)
    ):
        raise AppError(
            "Publish source not found.",
            code="PUBLISH_SOURCE_NOT_FOUND",
            status_code=404,
        )
    return task


def _source_urls(
    storage: ObjectStorage,
    task: VideoTask,
    *,
    source_kind: str,
) -> tuple[str, str]:
    media_url = presign_tenant_storage_key(
        storage,
        tenant_id=task.tenant_id,
        storage_key=task.storage_key,
        expires_in=settings.engine_s3_presign_ttl,
    )
    cover_key = task.thumbnail_key or (task.storage_key if source_kind == "image" else None)
    cover_url = (
        presign_tenant_storage_key(
            storage,
            tenant_id=task.tenant_id,
            storage_key=cover_key,
            expires_in=settings.engine_s3_presign_ttl,
        )
        if cover_key
        else media_url
    )
    return media_url, cover_url


def create_publish_draft(
    db: Session,
    *,
    tenant_id: str,
    source_kind: str,
    source_task_id: str,
    platform_ids: list[str],
    storage: ObjectStorage,
) -> PublishRecord:
    task = source_task_or_404(
        db,
        tenant_id=tenant_id,
        source_kind=source_kind,
        source_task_id=source_task_id,
    )
    media_url, cover_url = _source_urls(storage, task, source_kind=source_kind)
    items: list[dict[str, Any]] = []
    source_text = _source_text(task)
    for platform_id in platform_ids:
        platform = platform_by_id(platform_id)
        copy_payload = generate_publish_copy(
            db,
            tenant_id=tenant_id,
            source_text=source_text,
            platform=platform,
        )
        charge_copy_quota(
            db,
            tenant_id=tenant_id,
            provider="deepseek",
            model=settings.engine_llm_model or None,
            llm_usage=copy_payload.get("_llm_usage"),
        )
        items.append(
            {
                "platform_id": platform_id,
                "title": copy_payload["title"],
                "body": copy_payload["body"],
                "hashtags": copy_payload["hashtags"],
                "cover_url": cover_url,
                "media_url": media_url,
                "publish_url": str(platform["publish_url"]),
            }
        )

    record = PublishRecord(
        tenant_id=tenant_id,
        source_kind=source_kind,
        source_task_id=task.id,
        items=items,
        platforms=[
            {"platform_id": platform_id, "status": "draft"} for platform_id in platform_ids
        ],
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def list_publish_records(
    db: Session,
    *,
    tenant_id: str,
    limit: int,
    offset: int,
) -> tuple[list[PublishRecord], int]:
    query = select(PublishRecord).where(
        PublishRecord.tenant_id == tenant_id,
        PublishRecord.deleted_at.is_(None),
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(
        db.scalars(query.order_by(PublishRecord.created_at.desc()).offset(offset).limit(limit))
    )
    return items, int(total)


def get_publish_record(db: Session, *, tenant_id: str, record_id: str) -> PublishRecord:
    record = db.scalar(
        select(PublishRecord).where(
            PublishRecord.id == record_id,
            PublishRecord.tenant_id == tenant_id,
            PublishRecord.deleted_at.is_(None),
        )
    )
    if record is None:
        raise AppError(
            "Publish record not found.",
            code="PUBLISH_RECORD_NOT_FOUND",
            status_code=404,
        )
    return record


def mark_platform_published(
    db: Session,
    *,
    tenant_id: str,
    record_id: str,
    platform_id: str,
) -> PublishRecord:
    record = get_publish_record(db, tenant_id=tenant_id, record_id=record_id)
    platforms = [dict(item) for item in record.platforms]
    matched = False
    for item in platforms:
        if item.get("platform_id") == platform_id:
            item["status"] = "published"
            matched = True
            break
    if not matched:
        raise AppError(
            "Publish platform not found.",
            code="PUBLISH_PLATFORM_NOT_FOUND",
            status_code=404,
        )
    record.platforms = platforms
    record.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(record)
    return record


def delete_publish_record(db: Session, *, tenant_id: str, record_id: str) -> PublishRecord:
    record = get_publish_record(db, tenant_id=tenant_id, record_id=record_id)
    record.deleted_at = datetime.now(UTC)
    record.updated_at = record.deleted_at
    db.commit()
    db.refresh(record)
    return record


def validate_platform_config_for_public_catalog() -> None:
    for platform in publish_platforms():
        platform_id = str(platform.get("id") or "")
        if platform_id not in _VALID_PLATFORM_IDS:
            raise AppError(
                "Publish platform config is invalid.",
                code="PUBLISH_PLATFORM_CONFIG_INVALID",
                status_code=500,
            )
        url = str(platform.get("publish_url") or "")
        if not url.startswith("https://"):
            raise AppError(
                "Publish platform config is invalid.",
                code="PUBLISH_PLATFORM_CONFIG_INVALID",
                status_code=500,
            )
