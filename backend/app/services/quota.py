from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import CreditRate, Subscription, UsageRecord
from app.services import provider_costs

_SCRIPT_CPS = Decimal("5")
_MIN_SECONDS = Decimal("3")
_MAX_SECONDS = Decimal("60")
_SEEDANCE_I2V_DEFAULT_SECONDS = 15
_SEEDANCE_I2V_CLIP_SECONDS = 5
_SEEDANCE_I2V_MIN_SECONDS = 5
_SEEDANCE_I2V_MAX_SECONDS = 120
_VIDEO_GEN_DURATIONS = {5, 10, 15}
_VIDEO_GEN_RESOLUTION_MULTIPLIERS = {
    "480p": Decimal("1.0000"),
    "720p": Decimal("1.5000"),
    "1080p": Decimal("2.2500"),
}
_IMAGE_QUALITY_MULTIPLIERS = {
    "low": Decimal("1"),
    "medium": Decimal("4"),
    "high": Decimal("15"),
}


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


def remaining_credits(subscription: Subscription) -> int:
    return (
        subscription.quota_credits_total
        - subscription.quota_credits_used
        - subscription.quota_credits_reserved
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
        default=Decimal("1.0000"),
    )
    tts_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="tts",
        unit="second",
        default=Decimal("0.2000"),
    )
    credits = (Decimal(seconds) * (avatar_rate + tts_rate)).quantize(Decimal("0.01"))
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
) -> QuotaEstimate:
    seconds = (
        estimated_seconds if estimated_seconds is not None else estimate_seconds(script, speed)
    )
    video_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="video",
        unit="second",
        default=Decimal("2.0000"),
    )
    credits = (Decimal(seconds) * video_rate).quantize(Decimal("0.01"))
    return QuotaEstimate(
        estimated_seconds=seconds,
        estimated_credits=credits,
        reservation_units=_credit_units(credits),
        capability="video",
        unit="second",
    )


def video_gen_billable_seconds(value: int | float | None) -> int:
    seconds = int(value or 5)
    if seconds not in _VIDEO_GEN_DURATIONS:
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
    multiplier = _VIDEO_GEN_RESOLUTION_MULTIPLIERS.get(resolution)
    if multiplier is None:
        raise AppError("Invalid video_gen resolution.", code="VALIDATION_ERROR", status_code=422)
    video_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="video_gen",
        unit="second",
        default=Decimal("2.0000"),
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
    quality: str,
    n: int = 1,
) -> QuotaEstimate:
    multiplier = _IMAGE_QUALITY_MULTIPLIERS.get(quality)
    if multiplier is None:
        raise AppError("Invalid image quality.", code="VALIDATION_ERROR", status_code=422)
    count = max(1, int(n))
    image_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="image",
        unit="image",
        default=Decimal("5.0000"),
    )
    credits = (Decimal(count) * image_rate * multiplier).quantize(Decimal("0.01"))
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
) -> QuotaEstimate:
    clone_rate = _rate(
        db,
        tenant_id=tenant_id,
        capability="voice_clone",
        unit="call",
        default=Decimal("30.0000"),
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


def charge_copy_quota(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
    model: str | None = None,
    llm_usage: provider_costs.DeepSeekUsageCost | None = None,
) -> UsageRecord:
    subscription = active_subscription(db, tenant_id)
    estimate = estimate_copy_quota(db, tenant_id=tenant_id)
    if remaining_credits(subscription) < estimate.reservation_units:
        raise AppError(
            "Insufficient tenant quota.",
            code="TENANT_QUOTA_EXCEEDED",
            status_code=403,
        )
    subscription.quota_credits_used += estimate.reservation_units
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


def charge_voice_clone_quota(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
    model: str | None = None,
) -> UsageRecord:
    subscription = active_subscription(db, tenant_id)
    estimate = estimate_voice_clone_quota(db, tenant_id=tenant_id)
    if remaining_credits(subscription) < estimate.reservation_units:
        raise AppError(
            "Insufficient tenant quota.",
            code="TENANT_QUOTA_EXCEEDED",
            status_code=403,
        )
    subscription.quota_credits_used += estimate.reservation_units
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
    subscription = active_subscription(db, tenant_id)
    estimate = estimate_avatar_talk_quota(
        db,
        tenant_id=tenant_id,
        script=script,
        speed=speed,
    )
    if remaining_credits(subscription) < estimate.reservation_units:
        raise AppError(
            "Insufficient tenant quota.",
            code="TENANT_QUOTA_EXCEEDED",
            status_code=403,
        )
    subscription.quota_credits_reserved += estimate.reservation_units
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


def reserve_seedance_i2v_quota(
    db: Session,
    *,
    tenant_id: str,
    video_task_id: str,
    script: str,
    speed: Decimal | float | int,
    estimated_seconds: int | None = None,
) -> Reservation:
    subscription = active_subscription(db, tenant_id)
    estimate = estimate_seedance_i2v_quota(
        db,
        tenant_id=tenant_id,
        script=script,
        speed=speed,
        estimated_seconds=estimated_seconds,
    )
    if remaining_credits(subscription) < estimate.reservation_units:
        raise AppError(
            "Insufficient tenant quota.",
            code="TENANT_QUOTA_EXCEEDED",
            status_code=403,
        )
    subscription.quota_credits_reserved += estimate.reservation_units
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=video_task_id,
        capability="video",
        provider="seedance",
        model=settings.engine_seedance_model,
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
    subscription = active_subscription(db, tenant_id)
    estimate = estimate_video_gen_quota(
        db,
        tenant_id=tenant_id,
        duration_sec=duration_sec,
        resolution=resolution,
    )
    if remaining_credits(subscription) < estimate.reservation_units:
        raise AppError(
            "Insufficient tenant quota.",
            code="TENANT_QUOTA_EXCEEDED",
            status_code=403,
        )
    subscription.quota_credits_reserved += estimate.reservation_units
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
    quality: str,
    n: int = 1,
) -> Reservation:
    subscription = active_subscription(db, tenant_id)
    estimate = estimate_image_generation_quota(
        db,
        tenant_id=tenant_id,
        quality=quality,
        n=n,
    )
    if remaining_credits(subscription) < estimate.reservation_units:
        raise AppError(
            "Insufficient tenant quota.",
            code="TENANT_QUOTA_EXCEEDED",
            status_code=403,
        )
    subscription.quota_credits_reserved += estimate.reservation_units
    usage_record = UsageRecord(
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        video_task_id=video_task_id,
        capability="image",
        provider="apimart",
        model=settings.engine_apimart_image_model,
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


def _reserved_record(db: Session, *, tenant_id: str, video_task_id: str) -> UsageRecord | None:
    return db.scalar(
        select(UsageRecord).where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.video_task_id == video_task_id,
            UsageRecord.status == "reserved",
        )
    )


def release_reserved_quota(db: Session, *, tenant_id: str, video_task_id: str) -> None:
    record = _reserved_record(db, tenant_id=tenant_id, video_task_id=video_task_id)
    if record is None or record.subscription_id is None:
        return
    subscription = db.get(Subscription, record.subscription_id)
    if subscription is None:
        return
    subscription.quota_credits_reserved = max(
        0,
        subscription.quota_credits_reserved - _credit_units(Decimal(record.credits)),
    )
    record.status = "released"
    record.settled_at = datetime.now(UTC)


def settle_reserved_quota(
    db: Session,
    *,
    tenant_id: str,
    video_task_id: str,
    actual_seconds: int,
    cost_cents: int,
) -> None:
    record = _reserved_record(db, tenant_id=tenant_id, video_task_id=video_task_id)
    if record is None or record.subscription_id is None:
        return
    subscription = db.get(Subscription, record.subscription_id)
    if subscription is None:
        return

    actual_quantity = Decimal(actual_seconds).quantize(Decimal("0.001"))
    reserved_units = _credit_units(Decimal(record.credits))
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
    record.status = "settled"
    record.settled_at = datetime.now(UTC)
