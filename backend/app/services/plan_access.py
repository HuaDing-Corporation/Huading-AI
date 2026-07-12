from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models import Plan, Role, Subscription

_HUADING_PLAN_CODE = "huading"
_DOUBAO_VOICE_CLONE_PROVIDERS = {"doubao", "doubao-voice-clone"}


def uses_doubao_voice_clone(provider: str | None) -> bool:
    return str(provider or "").strip().lower() in _DOUBAO_VOICE_CLONE_PROVIDERS


def has_active_plan(db: Session, *, tenant_id: str, plan_code: str) -> bool:
    now = datetime.now(UTC)
    subscription_id = db.scalar(
        select(Subscription.id)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
            Subscription.period_start <= now,
            Subscription.period_end >= now,
            Plan.code == plan_code,
        )
        .limit(1)
    )
    return subscription_id is not None


def has_huading_access(db: Session, *, tenant_id: str, role: Role | str) -> bool:
    role_value = role.value if isinstance(role, Role) else str(role)
    return role_value == Role.ADMIN.value or has_active_plan(
        db,
        tenant_id=tenant_id,
        plan_code=_HUADING_PLAN_CODE,
    )


def require_doubao_voice_clone_access(
    db: Session,
    *,
    tenant_id: str,
    role: Role | str,
) -> None:
    if has_huading_access(db, tenant_id=tenant_id, role=role):
        return
    raise AppError(
        "Doubao premium voice cloning requires the Huading plan.",
        code="VOICE_CLONE_PLAN_REQUIRED",
        status_code=403,
    )
