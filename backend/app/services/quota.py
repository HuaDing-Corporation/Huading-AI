from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import (
    Asset,
    CreditRate,
    ReversePromptJob,
    Subscription,
    UsageRecord,
    VideoTask,
)
from app.services import provider_costs

_SCRIPT_CPS = Decimal("5")
_MIN_SECONDS = Decimal("3")
_MAX_SECONDS = Decimal("60")
_SEEDANCE_I2V_DEFAULT_SECONDS = 15
_SEEDANCE_I2V_CLIP_SECONDS = 5
_SEEDANCE_I2V_MIN_SECONDS = 5
_SEEDANCE_I2V_MAX_SECONDS = 120
_VIDEO_GEN_MIN_DURATION_SEC = 4
_VIDEO_GEN_MAX_DURATION_SEC = 15
_VIDEO_GEN_RESOLUTION_MULTIPLIERS = {
    "480p": Decimal("1.0000"),
    "720p": Decimal("1.6250"),
    "1080p": Decimal("3.5000"),
}
_COSYVOICE_CLONE_PROVIDER = "cosyvoice-voice-clone"
_VOICE_CLONE_DEFAULT_CREDITS = Decimal("30000.0000")
_REVERSE_PROMPT_VIDEO_SHORT_MAX_DURATION_MS = 60_000
_REVERSE_PROMPT_VIDEO_MAX_DURATION_MS = 180_000


@dataclass(frozen=True)
class QuotaEstimate:
    estimated_seconds: int
    estimated_credits: Decimal
    reservation_units: int
    capability: str
    unit: str


@dataclass(frozen=True)
class Reservation:
    subscription: Subscription
    usage_record: UsageRecord
    estimated_seconds: int
    estimated_credits: Decimal


def active_subscription(db: Session, tenant_id: str) -> Subscription:
    now = datetime.now(UTC)
    subscription = db.scalar(
        select(Subscription)
        .where(
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
            Subscription.period_start <= now,
            Subscription.period_end >= now,
        )
        .order_by(Subscription.period_end.desc())
    )
    if subscription is None:
        raise AppError(
            "Active subscription not found.",
            code="SUBSCRIPTION_NOT_FOUND",
            status_code=404,
        )
    return subscription


# Lock-order audit: a transaction may continue from Subscription to BatchJob
# (settlement followed by refresh_batch_job). No BatchJob -> Subscription edge is
# currently known, so acyclicity is an audit conclusion, not a structural guarantee.
def _active_subscription_for_update(db: Session, tenant_id: str) -> Subscription:
    now = datetime.now(UTC)
    subscription = db.scalar(
        select(Subscription)
        .where(
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
            Subscription.period_start <= now,
            Subscription.period_end >= now,
        )
        .order_by(Subscription.period_end.desc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if subscription is None:
        raise AppError(
            "Active subscription not found.",
            code="SUBSCRIPTION_NOT_FOUND",
            status_code=404,
        )
    return subscription


def lock_active_subscription(db: Session, *, tenant_id: str) -> Subscription:
    return _active_subscription_for_update(db, tenant_id)


def _subscription_for_update(
    db: Session,
    subscription_id: str,
) -> Subscription | None:
    return db.scalar(
        select(Subscription)
        .where(Subscription.id == subscription_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _lock_video_task_for_quota(
    db: Session,
    *,
    tenant_id: str,
    video_task_id: str,
) -> None:
    db.scalar(
        select(VideoTask.id)
        .where(
            VideoTask.id == video_task_id,
            VideoTask.tenant_id == tenant_id,
        )
        .with_for_update()
    )


def _lock_reverse_prompt_job_for_quota(
    db: Session,
    *,
    tenant_id: str,
    reverse_prompt_job_id: str,
) -> str | None:
    return db.scalar(
        select(ReversePromptJob.source_asset_id)
        .where(
            ReversePromptJob.id == reverse_prompt_job_id,
            ReversePromptJob.tenant_id == tenant_id,
        )
        .with_for_update()
    )


def remaining_credits(subscription: Subscription) -> int:
    return (
        subscription.quota_credits_total
        - subscription.quota_credits_used
        - subscription.quota_credits_reserved
    )


def _apply_active_quota_delta(
    db: Session,
    *,
    tenant_id: str,
    used_credits: int = 0,
    reserved_credits: int = 0,
) -> Subscription:
    subscription = _active_subscription_for_update(db, tenant_id)
    required_credits = used_credits + reserved_credits
    if remaining_credits(subscription) < required_credits:
        raise AppError(
            "Insufficient tenant quota.",
            code="TENANT_QUOTA_EXCEEDED",
            status_code=403,
        )
    subscription.quota_credits_used += used_credits
    subscription.quota_credits_reserved += reserved_credits
    # A later quota call in this transaction refreshes the locked row from SQL.
    db.flush([subscription])
    return subscription


def consume_active_quota(
    db: Session,
    *,
    tenant_id: str,
    credits: int,
) -> Subscription:
    return _apply_active_quota_delta(
        db,
        tenant_id=tenant_id,
        used_credits=credits,
    )


def _reserve_active_quota(
    db: Session,
    *,
    tenant_id: str,
    credits: int,
) -> Subscription:
    return _apply_active_quota_delta(
        db,
        tenant_id=tenant_id,
        reserved_credits=credits,
    )


def quota_payload(subscription: Subscription) -> dict[str, int]:
    return {
        "total": subscription.quota_credits_total,
        "used": subscription.quota_credits_used,
        "reserved": subscription.quota_credits_reserved,
        "remaining": remaining_credits(subscription),
    }


def _rate(
    db: Session,
    *,
    tenant_id: str,
    capability: str,
    unit: str,
    default: Decimal,
) -> Decimal:
    tenant_rate = db.scalar(
        select(CreditRate).where(
            CreditRate.tenant_id == tenant_id,
            CreditRate.capability == capability,
            CreditRate.unit == unit,
            CreditRate.is_active.is_(True),
        )
    )
    if tenant_rate is not None:
        return Decimal(tenant_rate.credits_per_unit)
    platform_rate = db.scalar(
        select(CreditRate).where(
            CreditRate.tenant_id.is_(None),
            CreditRate.capability == capability,
            CreditRate.unit == unit,
            CreditRate.is_active.is_(True),
        )
    )
    if platform_rate is not None:
        return Decimal(platform_rate.credits_per_unit)
    return default


def estimate_seconds(script: str, speed: Decimal | float | int = Decimal("1.0")) -> int:
    safe_speed = Decimal(str(speed or "1.0"))
    if safe_speed <= 0:
        safe_speed = Decimal("1.0")
    raw = (Decimal(max(1, len(script))) / _SCRIPT_CPS / safe_speed).to_integral_value(
        rounding=ROUND_CEILING
    )
    clamped = max(_MIN_SECONDS, min(_MAX_SECONDS, raw))
    return int(clamped)


def seedance_i2v_target_seconds(value: int | float | None) -> int:
    if value is None:
        return _SEEDANCE_I2V_DEFAULT_SECONDS
    return max(_SEEDANCE_I2V_MIN_SECONDS, min(_SEEDANCE_I2V_MAX_SECONDS, int(value)))


def seedance_i2v_billable_seconds(value: int | float | None) -> int:
    target = seedance_i2v_target_seconds(value)
    scenes = max(1, int((target + _SEEDANCE_I2V_CLIP_SECONDS - 1) // _SEEDANCE_I2V_CLIP_SECONDS))
    return scenes * _SEEDANCE_I2V_CLIP_SECONDS


def _credit_units(value: Decimal) -> int:
    return int(Decimal(value).to_integral_value(rounding=ROUND_CEILING))


def _video_resolution_multiplier(resolution: str) -> Decimal:
    multiplier = _VIDEO_GEN_RESOLUTION_MULTIPLIERS.get(resolution)
    if multiplier is None:
        raise AppError("Invalid video resolution.", code="VALIDATION_ERROR", status_code=422)
    return multiplier


def estimate_avatar_talk_quota(
    db: Session,
    *,
    tenant_id: str,
    script: str,
    speed: Decimal | float | int,
) -> QuotaEstimate:
    seconds = estimate_seconds(script, speed)
    avatar_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="avatar",
        unit="second",
        default=Decimal("150.0000"),
    )
    tts_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="tts",
        unit="character",
        default=Decimal("0.1000"),
    )
    credits = (
        Decimal(seconds) * avatar_rate + Decimal(len(script or "")) * tts_rate
    ).quantize(Decimal("0.01"))
    return QuotaEstimate(
        estimated_seconds=seconds,
        estimated_credits=credits,
        reservation_units=_credit_units(credits),
        capability="avatar",
        unit="second",
    )


def estimate_seedance_i2v_quota(
    db: Session,
    *,
    tenant_id: str,
    script: str,
    speed: Decimal | float | int,
    estimated_seconds: int | None = None,
    resolution: str = "720p",
) -> QuotaEstimate:
    seconds = (
        estimated_seconds if estimated_seconds is not None else estimate_seconds(script, speed)
    )
    video_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="video",
        unit="second",
        default=Decimal("80.0000"),
    )
    tts_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="tts",
        unit="character",
        default=Decimal("0.1000"),
    )
    credits = (
        Decimal(seconds) * video_rate * _video_resolution_multiplier(resolution)
        + Decimal(len(script or "")) * tts_rate
    ).quantize(Decimal("0.01"))
    return QuotaEstimate(
        estimated_seconds=seconds,
        estimated_credits=credits,
        reservation_units=_credit_units(credits),
        capability="video",
        unit="second",
    )


def video_gen_billable_seconds(value: int | float | None) -> int:
    seconds = int(value or 5)
    if not (_VIDEO_GEN_MIN_DURATION_SEC <= seconds <= _VIDEO_GEN_MAX_DURATION_SEC):
        raise AppError("Invalid video_gen duration.", code="VALIDATION_ERROR", status_code=422)
    return seconds


def estimate_video_gen_quota(
    db: Session,
    *,
    tenant_id: str,
    duration_sec: int,
    resolution: str,
) -> QuotaEstimate:
    seconds = video_gen_billable_seconds(duration_sec)
    multiplier = _video_resolution_multiplier(resolution)
    video_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="video_gen",
        unit="second",
        default=Decimal("80.0000"),
    )
    credits = (Decimal(seconds) * video_rate * multiplier).quantize(Decimal("0.01"))
    return QuotaEstimate(
        estimated_seconds=seconds,
        estimated_credits=credits,
        reservation_units=_credit_units(credits),
        capability="video_gen",
        unit="second",
    )


def estimate_image_generation_quota(
    db: Session,
    *,
    tenant_id: str,
    n: int = 1,
) -> QuotaEstimate:
    count = max(1, int(n))
    image_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="image",
        unit="image",
        default=Decimal("10.0000"),
    )
    credits = (Decimal(count) * image_rate).quantize(Decimal("0.01"))
    return QuotaEstimate(
        estimated_seconds=count,
        estimated_credits=credits,
        reservation_units=_credit_units(credits),
        capability="image",
        unit="image",
    )


def estimate_voice_clone_quota(
    db: Session,
    *,
    tenant_id: str,
    provider: str = "doubao-voice-clone",
) -> QuotaEstimate:
    if provider == _COSYVOICE_CLONE_PROVIDER:
        credits = Decimal("0.00")
    else:
        clone_rate = _rate(
            db,
            tenant_id=tenant_id,
            capability="voice_clone",
            unit="call",
            default=_VOICE_CLONE_DEFAULT_CREDITS,
        )
        credits = clone_rate.quantize(Decimal("0.01"))
    return QuotaEstimate(
        estimated_seconds=1,
        estimated_credits=credits,
        reservation_units=_credit_units(credits),
        capability="voice_clone",
        unit="call",
    )


def estimate_copy_quota(
    db: Session,
    *,
    tenant_id: str,
    count: int = 1,
) -> QuotaEstimate:
    safe_count = max(1, int(count))
    copy_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="llm",
        unit="call",
        default=Decimal("1.0000"),
    )
    credits = (Decimal(safe_count) * copy_rate).quantize(Decimal("0.01"))
    return QuotaEstimate(
        estimated_seconds=safe_count,
        estimated_credits=credits,
        reservation_units=_credit_units(credits),
        capability="llm",
        unit="call",
    )


def estimate_reverse_prompt_quota(
    db: Session,
    *,
    tenant_id: str,
) -> QuotaEstimate:
    reverse_prompt_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="reverse_prompt",
        unit="call",
        default=Decimal("1.0000"),
    )
    credits = reverse_prompt_rate.quantize(Decimal("0.01"))
    return QuotaEstimate(
        estimated_seconds=1,
        estimated_credits=credits,
        reservation_units=_credit_units(credits),
        capability="reverse_prompt",
        unit="call",
    )


def estimate_reverse_prompt_video_quota(
    db: Session,
    *,
    tenant_id: str,
    duration_ms: int,
) -> QuotaEstimate:
    tier = reverse_prompt_video_tier(duration_ms)
    if tier == "video_short":
        reverse_prompt_rate = _rate(
            db,
            tenant_id=tenant_id,
            capability="reverse_prompt_video",
            unit="call",
            default=Decimal(str(settings.engine_reverse_prompt_video_credits)),
        )
    else:
        reverse_prompt_rate = Decimal(
            str(settings.engine_reverse_prompt_video_long_credits)
        )
    credits = reverse_prompt_rate.quantize(Decimal("0.01"))
    return QuotaEstimate(
        estimated_seconds=1,
        estimated_credits=credits,
        reservation_units=_credit_units(credits),
        capability="reverse_prompt_video",
        unit="call",
    )


def reverse_prompt_video_tier(duration_ms: int) -> str:
    duration = int(duration_ms)
    if duration < 1_000 or duration > _REVERSE_PROMPT_VIDEO_MAX_DURATION_MS:
        raise ValueError("Reverse prompt video duration must be between 1 and 180 seconds.")
    if duration <= _REVERSE_PROMPT_VIDEO_SHORT_MAX_DURATION_MS:
        return "video_short"
    return "video_long"


def ensure_reverse_prompt_quota_available(db: Session, *, tenant_id: str) -> None:
    subscription = active_subscription(db, tenant_id)
    estimate = estimate_reverse_prompt_quota(db, tenant_id=tenant_id)
    if remaining_credits(subscription) < estimate.reservation_units:
        raise AppError(
            "Insufficient tenant quota.",
            code="TENANT_QUOTA_EXCEEDED",
            status_code=403,
        )


def charge_copy_quota(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
    model: str | None = None,
    llm_usage: provider_costs.DeepSeekUsageCost | None = None,
) -> UsageRecord:
    estimate = estimate_copy_quota(db, tenant_id=tenant_id)
    subscription = consume_active_quota(
        db,
        tenant_id=tenant_id,
        credits=estimate.reservation_units,
    )
    unit = "call"
    quantity = Decimal("1.000")
    cost_cents = 0
    if llm_usage is not None:
        provider = llm_usage.provider
        model = llm_usage.model or model
        unit = "token"
        quantity = Decimal(llm_usage.total_tokens)
        cost_cents = llm_usage.cost_cents
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=None,
        capability="llm",
        provider=provider,
        model=model,
        unit=unit,
        quantity=quantity,
        credits=estimate.estimated_credits,
        cost_cents=cost_cents,
        status="settled",
        settled_at=datetime.now(UTC),
    )
    db.add(usage_record)
    return usage_record


def charge_reverse_prompt_quota(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
    model: str | None,
    total_tokens: int,
    cost_cents: int,
) -> UsageRecord:
    estimate = estimate_reverse_prompt_quota(db, tenant_id=tenant_id)
    subscription = consume_active_quota(
        db,
        tenant_id=tenant_id,
        credits=estimate.reservation_units,
    )
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=None,
        capability="reverse_prompt",
        provider=provider,
        model=model,
        unit="token",
        quantity=Decimal(total_tokens),
        credits=estimate.estimated_credits,
        cost_cents=cost_cents,
        status="settled",
        settled_at=datetime.now(UTC),
    )
    db.add(usage_record)
    return usage_record


def charge_voice_clone_quota(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
    model: str | None = None,
) -> UsageRecord:
    estimate = estimate_voice_clone_quota(db, tenant_id=tenant_id, provider=provider)
    subscription = consume_active_quota(
        db,
        tenant_id=tenant_id,
        credits=estimate.reservation_units,
    )
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=None,
        capability="voice_clone",
        provider=provider,
        model=model,
        unit="call",
        quantity=Decimal("1.000"),
        credits=estimate.estimated_credits,
        cost_cents=0,
        status="settled",
        settled_at=datetime.now(UTC),
    )
    db.add(usage_record)
    return usage_record


def reserve_avatar_talk_quota(
    db: Session,
    *,
    tenant_id: str,
    video_task_id: str,
    script: str,
    speed: Decimal | float | int,
) -> Reservation:
    estimate = estimate_avatar_talk_quota(
        db,
        tenant_id=tenant_id,
        script=script,
        speed=speed,
    )
    _lock_video_task_for_quota(
        db,
        tenant_id=tenant_id,
        video_task_id=video_task_id,
    )
    subscription = _reserve_active_quota(
        db,
        tenant_id=tenant_id,
        credits=estimate.reservation_units,
    )
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=video_task_id,
        capability="avatar",
        provider="omnihuman",
        model="jimeng_realman_avatar_picture_omni_v15",
        unit="second",
        quantity=Decimal(estimate.estimated_seconds),
        credits=estimate.estimated_credits,
        cost_cents=0,
        status="reserved",
    )
    db.add(usage_record)
    return Reservation(
        subscription,
        usage_record,
        estimate.estimated_seconds,
        estimate.estimated_credits,
    )


def reserve_reverse_prompt_video_quota(
    db: Session,
    *,
    tenant_id: str,
    reverse_prompt_job_id: str,
) -> Reservation:
    source_asset_id = _lock_reverse_prompt_job_for_quota(
        db,
        tenant_id=tenant_id,
        reverse_prompt_job_id=reverse_prompt_job_id,
    )
    source = db.get(Asset, source_asset_id) if source_asset_id is not None else None
    if (
        source is None
        or source.tenant_id != tenant_id
        or source.duration_ms is None
    ):
        raise AppError(
            "Reverse prompt source duration is unavailable.",
            code="REVERSE_PROMPT_SOURCE_INVALID",
            status_code=422,
        )
    estimate = estimate_reverse_prompt_video_quota(
        db,
        tenant_id=tenant_id,
        duration_ms=source.duration_ms,
    )
    subscription = _reserve_active_quota(
        db,
        tenant_id=tenant_id,
        credits=estimate.reservation_units,
    )
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        reverse_prompt_job_id=reverse_prompt_job_id,
        capability="reverse_prompt_video",
        provider="apimart",
        model=(
            settings.engine_apimart_reverse_prompt_video_model
            if settings.engine_reverse_prompt_video_analysis_mode == "native"
            else settings.engine_apimart_reverse_prompt_model
        ),
        unit="call",
        quantity=Decimal("1.000"),
        credits=estimate.estimated_credits,
        cost_cents=0,
        status="reserved",
    )
    db.add(usage_record)
    return Reservation(
        subscription,
        usage_record,
        estimate.estimated_seconds,
        estimate.estimated_credits,
    )


def reserve_seedance_i2v_quota(
    db: Session,
    *,
    tenant_id: str,
    video_task_id: str,
    script: str,
    speed: Decimal | float | int,
    estimated_seconds: int | None = None,
    resolution: str = "720p",
) -> Reservation:
    estimate = estimate_seedance_i2v_quota(
        db,
        tenant_id=tenant_id,
        script=script,
        speed=speed,
        estimated_seconds=estimated_seconds,
        resolution=resolution,
    )
    _lock_video_task_for_quota(
        db,
        tenant_id=tenant_id,
        video_task_id=video_task_id,
    )
    subscription = _reserve_active_quota(
        db,
        tenant_id=tenant_id,
        credits=estimate.reservation_units,
    )
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=video_task_id,
        capability="video",
        provider="apimart",
        model=settings.engine_apimart_video_model,
        unit="second",
        quantity=Decimal(estimate.estimated_seconds),
        credits=estimate.estimated_credits,
        cost_cents=0,
        status="reserved",
    )
    db.add(usage_record)
    return Reservation(
        subscription,
        usage_record,
        estimate.estimated_seconds,
        estimate.estimated_credits,
    )


def reserve_video_gen_quota(
    db: Session,
    *,
    tenant_id: str,
    video_task_id: str,
    duration_sec: int,
    resolution: str,
) -> Reservation:
    estimate = estimate_video_gen_quota(
        db,
        tenant_id=tenant_id,
        duration_sec=duration_sec,
        resolution=resolution,
    )
    _lock_video_task_for_quota(
        db,
        tenant_id=tenant_id,
        video_task_id=video_task_id,
    )
    subscription = _reserve_active_quota(
        db,
        tenant_id=tenant_id,
        credits=estimate.reservation_units,
    )
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=video_task_id,
        capability="video_gen",
        provider="apimart",
        model=settings.engine_apimart_video_model,
        unit="second",
        quantity=Decimal(estimate.estimated_seconds),
        credits=estimate.estimated_credits,
        cost_cents=0,
        status="reserved",
    )
    db.add(usage_record)
    return Reservation(
        subscription,
        usage_record,
        estimate.estimated_seconds,
        estimate.estimated_credits,
    )


def reserve_image_generation_quota(
    db: Session,
    *,
    tenant_id: str,
    video_task_id: str,
    n: int = 1,
    provider: str = "apimart",
) -> Reservation:
    estimate = estimate_image_generation_quota(
        db,
        tenant_id=tenant_id,
        n=n,
    )
    _lock_video_task_for_quota(
        db,
        tenant_id=tenant_id,
        video_task_id=video_task_id,
    )
    subscription = _reserve_active_quota(
        db,
        tenant_id=tenant_id,
        credits=estimate.reservation_units,
    )
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=video_task_id,
        capability="image",
        provider=provider,
        model=settings.engine_apimart_image_model if provider == "apimart" else None,
        unit="image",
        quantity=Decimal(estimate.estimated_seconds),
        credits=estimate.estimated_credits,
        cost_cents=0,
        status="reserved",
    )
    db.add(usage_record)
    return Reservation(
        subscription,
        usage_record,
        estimate.estimated_seconds,
        estimate.estimated_credits,
    )


def reserve_released_video_task_quota(
    db: Session,
    *,
    tenant_id: str,
    video_task_id: str,
) -> Reservation | None:
    _lock_video_task_for_quota(
        db,
        tenant_id=tenant_id,
        video_task_id=video_task_id,
    )
    settled = db.scalar(
        select(UsageRecord.id).where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.video_task_id == video_task_id,
            UsageRecord.status == "settled",
            UsageRecord.credits > 0,
        )
    )
    if settled is not None:
        return None
    released = db.scalar(
        select(UsageRecord)
        .where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.video_task_id == video_task_id,
            UsageRecord.status == "released",
            UsageRecord.credits > 0,
        )
        .order_by(UsageRecord.created_at.desc(), UsageRecord.id.desc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if released is None:
        return None

    credits = Decimal(released.credits)
    reservation_units = _credit_units(credits)
    subscription = _reserve_active_quota(
        db,
        tenant_id=tenant_id,
        credits=reservation_units,
    )
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=video_task_id,
        capability=released.capability,
        provider=released.provider,
        model=released.model,
        unit=released.unit,
        quantity=Decimal(released.quantity),
        credits=credits,
        cost_cents=0,
        status="reserved",
    )
    db.add(usage_record)
    return Reservation(
        subscription=subscription,
        usage_record=usage_record,
        estimated_seconds=max(1, int(Decimal(released.quantity))),
        estimated_credits=credits,
    )


def _reserved_record(db: Session, *, tenant_id: str, video_task_id: str) -> UsageRecord | None:
    _lock_video_task_for_quota(
        db,
        tenant_id=tenant_id,
        video_task_id=video_task_id,
    )
    return db.scalar(
        select(UsageRecord)
        .where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.video_task_id == video_task_id,
            UsageRecord.status == "reserved",
        )
        .order_by(UsageRecord.created_at.desc(), UsageRecord.id.desc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _reserved_reverse_prompt_record(
    db: Session,
    *,
    tenant_id: str,
    reverse_prompt_job_id: str,
) -> UsageRecord | None:
    _lock_reverse_prompt_job_for_quota(
        db,
        tenant_id=tenant_id,
        reverse_prompt_job_id=reverse_prompt_job_id,
    )
    return db.scalar(
        select(UsageRecord)
        .where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.reverse_prompt_job_id == reverse_prompt_job_id,
            UsageRecord.capability == "reverse_prompt_video",
            UsageRecord.status == "reserved",
        )
        .order_by(UsageRecord.created_at.desc(), UsageRecord.id.desc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def release_reverse_prompt_video_quota(
    db: Session,
    *,
    tenant_id: str,
    reverse_prompt_job_id: str,
) -> None:
    record = _reserved_reverse_prompt_record(
        db,
        tenant_id=tenant_id,
        reverse_prompt_job_id=reverse_prompt_job_id,
    )
    if record is None or record.status != "reserved" or record.subscription_id is None:
        return
    subscription = _subscription_for_update(db, record.subscription_id)
    if subscription is None:
        return
    subscription.quota_credits_reserved = max(
        0,
        subscription.quota_credits_reserved - _credit_units(Decimal(record.credits)),
    )
    record.status = "released"
    record.settled_at = datetime.now(UTC)


def settle_reverse_prompt_video_quota(
    db: Session,
    *,
    tenant_id: str,
    reverse_prompt_job_id: str,
    provider: str,
    model: str | None,
    total_tokens: int,
    cost_cents: int,
) -> None:
    record = _reserved_reverse_prompt_record(
        db,
        tenant_id=tenant_id,
        reverse_prompt_job_id=reverse_prompt_job_id,
    )
    if record is None or record.status != "reserved" or record.subscription_id is None:
        return
    subscription = _subscription_for_update(db, record.subscription_id)
    if subscription is None:
        return
    reserved_units = _credit_units(Decimal(record.credits))
    subscription.quota_credits_reserved = max(
        0,
        subscription.quota_credits_reserved - reserved_units,
    )
    subscription.quota_credits_used += reserved_units
    record.provider = provider
    record.model = model
    record.unit = "token"
    record.quantity = Decimal(max(0, total_tokens)).quantize(Decimal("0.001"))
    record.cost_cents = max(0, int(cost_cents))
    record.status = "settled"
    record.settled_at = datetime.now(UTC)


def release_reserved_quota(db: Session, *, tenant_id: str, video_task_id: str) -> None:
    record = _reserved_record(db, tenant_id=tenant_id, video_task_id=video_task_id)
    if record is None or record.status != "reserved" or record.subscription_id is None:
        return
    subscription = _subscription_for_update(db, record.subscription_id)
    if subscription is None:
        return
    subscription.quota_credits_reserved = max(
        0,
        subscription.quota_credits_reserved - _credit_units(Decimal(record.credits)),
    )
    record.status = "released"
    record.settled_at = datetime.now(UTC)


def _script_chars(task: VideoTask | None) -> int:
    if task is None:
        return 0
    return len(task.script or task.topic or "")


def _settled_componentized_credits(
    db: Session,
    *,
    tenant_id: str,
    record: UsageRecord,
    task: VideoTask | None,
    actual_quantity: Decimal,
) -> Decimal | None:
    if task is None:
        return None
    reserved_quantity = Decimal(record.quantity or 0)
    if reserved_quantity <= 0:
        return None

    tts_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="tts",
        unit="character",
        default=Decimal("0.1000"),
    )
    tts_credits = Decimal(_script_chars(task)) * tts_rate
    if record.capability == "avatar":
        timed_rate = _rate(
            db,
            tenant_id=tenant_id,
            capability="avatar",
            unit="second",
            default=Decimal("150.0000"),
        )
    elif record.capability == "video" and (task.video_mode or task.mode) == "seedance_i2v":
        resolution = str((task.params or {}).get("resolution") or "720p")
        timed_rate = (
            _rate(
                db,
                tenant_id=tenant_id,
                capability="video",
                unit="second",
                default=Decimal("80.0000"),
            )
            * _video_resolution_multiplier(resolution)
        )
    else:
        return None

    expected_reserved = (reserved_quantity * timed_rate + tts_credits).quantize(
        Decimal("0.01")
    )
    if expected_reserved != Decimal(record.credits).quantize(Decimal("0.01")):
        return None
    return (actual_quantity * timed_rate + tts_credits).quantize(Decimal("0.01"))


def settle_reserved_quota(
    db: Session,
    *,
    tenant_id: str,
    video_task_id: str,
    actual_seconds: int,
    cost_cents: int,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    record = _reserved_record(db, tenant_id=tenant_id, video_task_id=video_task_id)
    if record is None or record.status != "reserved" or record.subscription_id is None:
        return
    subscription = _subscription_for_update(db, record.subscription_id)
    if subscription is None:
        return

    actual_quantity = Decimal(actual_seconds).quantize(Decimal("0.001"))
    reserved_units = _credit_units(Decimal(record.credits))
    task = db.get(VideoTask, video_task_id)
    actual_credits = _settled_componentized_credits(
        db,
        tenant_id=tenant_id,
        record=record,
        task=task,
        actual_quantity=actual_quantity,
    )
    if actual_credits is None:
        per_second = Decimal(record.credits) / Decimal(record.quantity or 1)
        actual_credits = (actual_quantity * per_second).quantize(Decimal("0.01"))
    actual_units = _credit_units(actual_credits)

    subscription.quota_credits_reserved = max(
        0,
        subscription.quota_credits_reserved - reserved_units,
    )
    subscription.quota_credits_used += actual_units
    record.quantity = actual_quantity
    record.credits = actual_credits
    record.cost_cents = cost_cents
    if provider:
        record.provider = provider
    if model:
        record.model = model
    record.status = "settled"
    record.settled_at = datetime.now(UTC)
