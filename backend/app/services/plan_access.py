from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.utils import normalize_tenant_slug
from app.db.models import Plan, Role, Subscription, Tenant, User

_HUADING_PLAN_CODE = "huading"
_DOUBAO_VOICE_CLONE_PROVIDERS = {"doubao", "doubao-voice-clone"}


@dataclass(frozen=True)
class TenantPlanAccess:
    is_platform: bool
    has_huading: bool


@dataclass(frozen=True)
class AnalyticsScope:
    tenant_id: str | None


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


def is_platform_tenant(db: Session, *, tenant_id: str) -> bool:
    if not configured_platform_tenant_slugs():
        return False
    tenant_slug = db.scalar(select(Tenant.slug).where(Tenant.id == tenant_id))
    return is_platform_tenant_slug(tenant_slug)


def is_authorized_platform_admin(
    db: Session,
    *,
    user: User | None,
    expected_tenant_id: str,
) -> bool:
    """Pure request/service predicate for the existing platform-admin semantics."""
    if (
        user is None
        or user.tenant_id != expected_tenant_id
        or not user.is_active
        or user.role != Role.ADMIN.value
    ):
        return False
    tenant = db.get(Tenant, expected_tenant_id)
    return bool(
        tenant is not None
        and tenant.status == "active"
        and tenant.deleted_at is None
        and is_platform_tenant(db, tenant_id=expected_tenant_id)
    )


def configured_platform_tenant_slugs() -> set[str]:
    return {
        normalized
        for slug in settings.engine_platform_tenant_slugs
        if (normalized := normalize_tenant_slug(str(slug)))
    }


def is_platform_tenant_slug(tenant_slug: str | None) -> bool:
    normalized = normalize_tenant_slug(tenant_slug)
    return bool(normalized) and normalized in configured_platform_tenant_slugs()


def platform_tenant_configuration_issues(db: Session) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    for slug in sorted(configured_platform_tenant_slugs()):
        tenant = db.scalar(
            select(Tenant)
            .where(func.lower(Tenant.slug) == slug)
            .order_by(Tenant.created_at.asc())
            .limit(1)
        )
        if tenant is None:
            issues.append((slug, "tenant_missing"))
        elif tenant.deleted_at is not None or tenant.status != "active":
            issues.append((slug, "tenant_inactive"))
        elif not has_active_plan(
            db,
            tenant_id=tenant.id,
            plan_code=_HUADING_PLAN_CODE,
        ):
            issues.append((slug, "huading_plan_missing"))
    return issues


def tenant_plan_access(db: Session, *, tenant_id: str) -> TenantPlanAccess:
    platform = is_platform_tenant(db, tenant_id=tenant_id)
    return TenantPlanAccess(
        is_platform=platform,
        has_huading=platform
        or has_active_plan(
            db,
            tenant_id=tenant_id,
            plan_code=_HUADING_PLAN_CODE,
        ),
    )


def has_huading_access(db: Session, *, tenant_id: str) -> bool:
    return tenant_plan_access(db, tenant_id=tenant_id).has_huading


def tenant_entitlements(
    db: Session,
    *,
    tenant_id: str,
    role: Role | str | None = None,
) -> set[str]:
    access = tenant_plan_access(db, tenant_id=tenant_id)
    entitlements: set[str] = set()
    if access.has_huading:
        entitlements.update({"voice_clone_vip", "analytics_view"})
    if access.is_platform:
        entitlements.add("analytics_platform")
        if role is not None and Role(role) is Role.ADMIN:
            entitlements.add("admin_console")
    return entitlements


def analytics_scope_for_tenant(
    db: Session,
    *,
    tenant_id: str,
) -> AnalyticsScope | None:
    access = tenant_plan_access(db, tenant_id=tenant_id)
    if not access.has_huading:
        return None
    return AnalyticsScope(tenant_id=None if access.is_platform else tenant_id)


def require_doubao_voice_clone_access(
    db: Session,
    *,
    tenant_id: str,
) -> None:
    if has_huading_access(db, tenant_id=tenant_id):
        return
    raise AppError(
        "Doubao premium voice cloning requires the Huading plan.",
        code="VOICE_CLONE_PLAN_REQUIRED",
        status_code=403,
    )
