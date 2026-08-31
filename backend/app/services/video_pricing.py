from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models import BrandVoice, User, Voice
from app.schemas.billing import BillingQuote
from app.schemas.videos import (
    DeferredUnpricedEstimate,
    LegacyVideoEstimate,
    VideoGenerateRequest,
)
from app.services.billing_quotes import issue_quote, request_sha256
from app.services.plan_access import (
    require_doubao_voice_clone_access,
    uses_doubao_voice_clone,
)
from app.services.pricing import (
    PRICING_POLICIES,
    PricingDraft,
    build_composite_pricing,
    build_simple_pricing,
    resolve_rate,
    video_create_parent_policy,
)
from app.services.quota import (
    estimate_avatar_talk_quota,
    estimate_image_generation_quota,
    estimate_seconds,
    estimate_seedance_i2v_quota,
    estimate_video_gen_quota,
    seedance_i2v_billable_seconds,
    seedance_i2v_target_seconds,
)
from app.services.voices import resolve_narration_voice

PricingContract = Literal["billing_quote", "legacy_estimate", "deferred_unpriced"]
_ESTIMATE_NOTE = "Estimated reservation; final settlement uses actual generated duration."
_BRAND_VOICE_PROVIDERS = {"doubao-voice-clone", "cosyvoice-voice-clone"}


@dataclass(frozen=True)
class VideoPricingContext:
    effective_video_mode: str
    pricing_contract: PricingContract
    voice: Voice | None = None
    brand_voice: BrandVoice | None = None
    billable_tts_text: str | None = None
    pricing_draft: PricingDraft | None = None
    base_provider: str | None = None
    tts_provider: str | None = None
    base_quantity: Decimal | None = None


def resolve_effective_video_mode(payload: VideoGenerateRequest) -> str:
    """Resolve the one video mode used by validation, pricing, persistence, and work."""
    literal_mode = payload.video_mode
    if literal_mode in {"photo", "seedance_i2v", "video_gen"}:
        return literal_mode
    if literal_mode == "avatar_talk" or any(
        (payload.voice_id, payload.avatar_asset_id, payload.avatar_video_asset_id)
    ):
        return "avatar_talk"
    return literal_mode


def normalize_billable_tts_text(payload: VideoGenerateRequest) -> str:
    text = (payload.script or "").strip()
    if not text:
        raise AppError(
            "使用品牌音色前请先生成或填写口播文案",
            code="BILLABLE_TEXT_REQUIRED",
            status_code=422,
        )
    return text


def normalized_video_pricing_request(payload: VideoGenerateRequest) -> dict[str, object]:
    normalized = payload.model_dump(mode="json")
    normalized.pop("video_mode", None)
    normalized["effective_video_mode"] = resolve_effective_video_mode(payload)
    return normalized


def video_pricing_request_hash(payload: VideoGenerateRequest) -> str:
    return request_sha256(normalized_video_pricing_request(payload))


def _base_quantity(payload: VideoGenerateRequest, *, mode: str, text: str) -> Decimal:
    if mode == "seedance_i2v":
        return Decimal(
            seedance_i2v_billable_seconds(seedance_i2v_target_seconds(payload.duration_sec))
        )
    return Decimal(estimate_seconds(text, payload.speed))


def _brand_pricing_draft(
    db: Session,
    *,
    tenant_id: str,
    payload: VideoGenerateRequest,
    mode: str,
    brand_voice: BrandVoice,
    text: str,
    requested_at: datetime | None,
) -> tuple[PricingDraft, Decimal]:
    quantity = _base_quantity(payload, mode=mode, text=text)
    video_policy = video_create_parent_policy(mode)
    base = build_simple_pricing(
        policy=video_policy,
        rate=resolve_rate(
            db,
            tenant_id=tenant_id,
            policy=video_policy,
            now=requested_at,
        ),
        quantity=quantity,
    )
    lines = [base.pricing_lines[0]]
    if brand_voice.provider == "cosyvoice-voice-clone":
        tts_policy = PRICING_POLICIES["cosyvoice_brand_tts"]
        tts = build_simple_pricing(
            policy=tts_policy,
            rate=resolve_rate(
                db,
                tenant_id=tenant_id,
                policy=tts_policy,
                now=requested_at,
            ),
            quantity=Decimal(len(text)),
        )
        lines.append(tts.pricing_lines[0])
    return (
        build_composite_pricing(
            operation="video_create",
            lines=lines,
        ),
        quantity,
    )


def resolve_video_pricing_context(
    db: Session,
    *,
    user: User,
    payload: VideoGenerateRequest,
    requested_at: datetime | None = None,
) -> VideoPricingContext:
    mode = resolve_effective_video_mode(payload)
    if mode in {"static_template", "seedance_t2v"}:
        return VideoPricingContext(mode, "deferred_unpriced")
    if mode not in {"avatar_talk", "seedance_i2v"}:
        return VideoPricingContext(mode, "legacy_estimate")
    if mode == "avatar_talk" and (
        int(bool(payload.avatar_asset_id)) + int(bool(payload.avatar_video_asset_id)) != 1
    ):
        raise AppError(
            "avatar_talk requires exactly one of avatar_asset_id or avatar_video_asset_id.",
            code="VALIDATION_ERROR",
            status_code=422,
        )

    voice, brand_voice = resolve_narration_voice(
        db,
        user=user,
        voice_id=payload.voice_id,
        requested_at=requested_at,
    )
    if brand_voice is None:
        return VideoPricingContext(mode, "legacy_estimate", voice=voice)
    if brand_voice.provider not in _BRAND_VOICE_PROVIDERS:
        raise AppError("Voice not found.", code="VOICE_NOT_FOUND", status_code=404)
    if uses_doubao_voice_clone(brand_voice.provider):
        require_doubao_voice_clone_access(db, tenant_id=user.tenant_id)
    text = normalize_billable_tts_text(payload)
    draft, base_quantity = _brand_pricing_draft(
        db,
        tenant_id=user.tenant_id,
        payload=payload,
        mode=mode,
        brand_voice=brand_voice,
        text=text,
        requested_at=requested_at,
    )
    return VideoPricingContext(
        mode,
        "billing_quote",
        brand_voice=brand_voice,
        billable_tts_text=text,
        pricing_draft=draft,
        base_provider="omnihuman" if mode == "avatar_talk" else "apimart",
        tts_provider=("cosyvoice-tts" if brand_voice.provider == "cosyvoice-voice-clone" else None),
        base_quantity=base_quantity,
    )


def _legacy_estimate(
    db: Session,
    *,
    user: User,
    payload: VideoGenerateRequest,
    context: VideoPricingContext,
) -> LegacyVideoEstimate:
    mode = context.effective_video_mode
    if mode == "photo":
        estimate = estimate_image_generation_quota(
            db,
            tenant_id=user.tenant_id,
            n=1,
            resolution=payload.image_resolution or "1k",
        )
    elif mode == "video_gen":
        estimate = estimate_video_gen_quota(
            db,
            tenant_id=user.tenant_id,
            duration_sec=int(payload.duration_sec or 5),
            resolution=payload.resolution,
        )
    elif mode == "seedance_i2v":
        target_seconds = seedance_i2v_target_seconds(payload.duration_sec)
        estimate = estimate_seedance_i2v_quota(
            db,
            tenant_id=user.tenant_id,
            script=payload.script or payload.topic or "",
            speed=payload.speed,
            estimated_seconds=seedance_i2v_billable_seconds(target_seconds),
            resolution=payload.resolution,
        )
    else:
        estimate = estimate_avatar_talk_quota(
            db,
            tenant_id=user.tenant_id,
            script=payload.script or payload.topic or "",
            speed=payload.speed,
        )
    return LegacyVideoEstimate(
        estimated_credits=estimate.reservation_units,
        unit="credits",
        note=_ESTIMATE_NOTE,
    )


def build_video_estimate(
    db: Session,
    *,
    user: User,
    payload: VideoGenerateRequest,
) -> BillingQuote | LegacyVideoEstimate | DeferredUnpricedEstimate:
    context = resolve_video_pricing_context(db, user=user, payload=payload)
    if context.pricing_contract == "deferred_unpriced":
        return DeferredUnpricedEstimate(
            note="Pricing is deferred until the generated script and duration are known."
        )
    if context.pricing_contract == "legacy_estimate":
        return _legacy_estimate(db, user=user, payload=payload, context=context)
    if context.pricing_draft is None:
        raise RuntimeError("billing quote context is missing its pricing draft")
    return issue_quote(
        tenant_id=user.tenant_id,
        user_id=user.id,
        request_hash=video_pricing_request_hash(payload),
        draft=context.pricing_draft,
    )
