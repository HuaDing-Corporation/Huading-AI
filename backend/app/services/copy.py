import asyncio
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import CopyDraft
from app.providers.base import invoke, resolve
from app.schemas.copy import (
    CopyDraftCreateRequest,
    CopyRewriteRequest,
    CopyTitlesRequest,
    CopyTopicsRequest,
)
from app.workers.avatar_talk import build_script_payload, clean_spoken_script

_CHAINABLE_VIDEO_MODES = {"avatar_talk", "seedance_i2v"}


def _ensure_llm_configured() -> None:
    if not (
        settings.engine_llm_api_key
        and settings.engine_llm_base_url
        and settings.engine_llm_model
    ):
        raise AppError(
            "DeepSeek is not configured.",
            code="LLM_NOT_CONFIGURED",
            status_code=503,
        )


def _duration_budget(source_text: str, payload: CopyRewriteRequest) -> dict[str, Any]:
    if payload.video_mode not in _CHAINABLE_VIDEO_MODES:
        return {"topic": source_text}
    budget = build_script_payload(
        source_text,
        video_mode="seedance_i2v",
        duration_sec=payload.duration_sec,
    )
    budget["video_mode"] = payload.video_mode
    return budget


def _rewrite_instruction(payload: CopyRewriteRequest) -> str:
    if payload.mode == "custom":
        return str(payload.instruction or "").strip()
    if payload.mode == "auto":
        return f"生成{payload.n}条风格不同的候选改写。"
    return "智能改写，保留核心卖点，提升表达吸引力。"


def _rewrite_payload(payload: CopyRewriteRequest) -> dict[str, Any]:
    source_text = payload.source_text.strip()
    provider_payload = _duration_budget(source_text, payload)
    target_chars = ""
    if "target_chars_min" in provider_payload and "target_chars_max" in provider_payload:
        target_chars = (
            f"字数控制在{provider_payload['target_chars_min']}-"
            f"{provider_payload['target_chars_max']}字。"
        )
    platform = f"目标平台：{payload.target_platform}。\n" if payload.target_platform else ""
    provider_payload.update(
        {
            "topic": source_text,
            "system_prompt": "你是短视频文案改写助手，只输出用户可直接使用的中文文案。",
            "user_prompt": (
                f"{platform}"
                f"请改写以下文案。{_rewrite_instruction(payload)}"
                f"{target_chars}"
                "不要输出解释、标题、编号或 Markdown。\n"
                f"原文案：{source_text}"
            ),
            "candidate_count": payload.n if payload.mode == "auto" else 1,
        }
    )
    return provider_payload


def _clean_candidate(text: str, *, clean_for_video: bool = False) -> str:
    candidate = re.sub(r"^\s*(?:[-+*]|\d+[.)、])\s*", "", text).strip()
    if clean_for_video:
        candidate = clean_spoken_script(candidate)
    return candidate.strip()


def _result_text(result: Any) -> str:
    if not isinstance(result, dict):
        raise AppError("Copy generation failed.", code="COPY_GEN_FAILED", status_code=502)
    return str(result.get("text") or "").strip()


def _rewrite_results(result: Any, payload: CopyRewriteRequest) -> list[str]:
    text = _result_text(result)
    clean_for_video = payload.video_mode in _CHAINABLE_VIDEO_MODES
    if payload.mode != "auto":
        candidate = _clean_candidate(text, clean_for_video=clean_for_video)
        if not candidate:
            raise AppError("Copy generation failed.", code="COPY_GEN_FAILED", status_code=502)
        return [candidate]

    candidates = [
        _clean_candidate(line, clean_for_video=clean_for_video)
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    candidates = [candidate for candidate in candidates if candidate][: payload.n]
    if not candidates:
        raise AppError("Copy generation failed.", code="COPY_GEN_FAILED", status_code=502)
    return candidates


def _candidate_lines(text: str, *, limit: int) -> list[str]:
    candidates = [
        _clean_candidate(line)
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    return [candidate for candidate in candidates if candidate][:limit]


def _generate_text(db: Session, *, tenant_id: str, provider_payload: dict[str, Any]) -> Any:
    _ensure_llm_configured()
    provider = resolve(db, tenant_id=tenant_id, capability="llm")
    try:
        return asyncio.run(
            invoke(
                db,
                tenant_id=tenant_id,
                capability="llm",
                provider=provider.__class__.__name__,
                operation=lambda: provider.generate_text(provider_payload),
                timeout_seconds=30.0,
            )
        )
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            "Copy generation failed.",
            code="COPY_GEN_FAILED",
            status_code=502,
        ) from exc


def rewrite_copy(
    db: Session,
    *,
    tenant_id: str,
    payload: CopyRewriteRequest,
) -> list[str]:
    result = _generate_text(db, tenant_id=tenant_id, provider_payload=_rewrite_payload(payload))
    return _rewrite_results(result, payload)


def _title_payload(payload: CopyTitlesRequest) -> dict[str, Any]:
    source_text = payload.source_text.strip()
    style = payload.style or "短句"
    return {
        "topic": source_text,
        "candidate_count": payload.n,
        "system_prompt": "你是短视频标题生成助手，只输出标题候选列表。",
        "user_prompt": (
            f"请基于以下文案生成{payload.n}个短视频标题，标题风格：{style}。"
            "每行一个标题，不要解释，不要 Markdown。\n"
            f"文案：{source_text}"
        ),
    }


def generate_titles(db: Session, *, tenant_id: str, payload: CopyTitlesRequest) -> list[str]:
    result = _generate_text(db, tenant_id=tenant_id, provider_payload=_title_payload(payload))
    titles = _candidate_lines(_result_text(result), limit=payload.n)
    if not titles:
        raise AppError("Copy generation failed.", code="COPY_GEN_FAILED", status_code=502)
    return titles


def _topic_payload(payload: CopyTopicsRequest) -> dict[str, Any]:
    source_text = payload.source_text.strip()
    return {
        "topic": source_text,
        "candidate_count": payload.n,
        "system_prompt": "你是短视频话题标签生成助手，只输出话题候选列表。",
        "user_prompt": (
            f"请基于以下文案生成{payload.n}个短视频话题标签。"
            "每行一个话题，尽量简短，适合社媒发布，不要解释，不要 Markdown。\n"
            f"文案：{source_text}"
        ),
    }


def _normalize_topic(topic: str) -> str:
    text = topic.strip().lstrip("#＃").strip()
    return f"#{text}" if text else ""


def generate_topics(db: Session, *, tenant_id: str, payload: CopyTopicsRequest) -> list[str]:
    result = _generate_text(db, tenant_id=tenant_id, provider_payload=_topic_payload(payload))
    candidates = _candidate_lines(_result_text(result), limit=payload.n)
    topics = [_normalize_topic(topic) for topic in candidates]
    topics = [topic for topic in topics if topic]
    if not topics:
        raise AppError("Copy generation failed.", code="COPY_GEN_FAILED", status_code=502)
    return topics


def create_draft(db: Session, *, tenant_id: str, payload: CopyDraftCreateRequest) -> CopyDraft:
    draft = CopyDraft(
        tenant_id=tenant_id,
        source_text=payload.source_text,
        result_text=payload.result_text,
        titles=payload.titles,
        topics=payload.topics,
        mode=payload.mode,
        target_platform=payload.target_platform,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def list_drafts(
    db: Session,
    *,
    tenant_id: str,
    limit: int,
    offset: int,
) -> tuple[list[CopyDraft], int]:
    query = select(CopyDraft).where(
        CopyDraft.tenant_id == tenant_id,
        CopyDraft.deleted_at.is_(None),
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(
        db.scalars(query.order_by(CopyDraft.created_at.desc()).offset(offset).limit(limit))
    )
    return items, int(total)


def get_draft(db: Session, *, tenant_id: str, draft_id: str) -> CopyDraft:
    draft = db.scalar(
        select(CopyDraft).where(
            CopyDraft.id == draft_id,
            CopyDraft.tenant_id == tenant_id,
            CopyDraft.deleted_at.is_(None),
        )
    )
    if draft is None:
        raise AppError("Copy draft not found.", code="COPY_DRAFT_NOT_FOUND", status_code=404)
    return draft


def delete_draft(db: Session, *, tenant_id: str, draft_id: str) -> CopyDraft:
    draft = get_draft(db, tenant_id=tenant_id, draft_id=draft_id)
    draft.deleted_at = datetime.now(UTC)
    db.commit()
    db.refresh(draft)
    return draft
