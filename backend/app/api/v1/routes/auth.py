from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUserDependency,
    DbSessionDependency,
    get_current_tenant,
    require_permission,
    session_permissions_for_user,
)
from app.core.exceptions import AppError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import Organization, Role, Tenant, User
from app.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    TenantRead,
    TenantRegisterRequest,
    TenantRegisterResponse,
    TokenResponse,
    UserRead,
)
from app.schemas.response import ApiResponse, ok
from app.services.plan_access import is_platform_tenant_slug
from app.services.subscription import create_default_subscription

router = APIRouter()
CurrentTenantDependency = Depends(get_current_tenant)
AdminPermissionDependency = Depends(require_permission("tenant:admin"))


def _tenant_slug_taken_error() -> AppError:
    return AppError(
        "Tenant slug is already taken.",
        code="tenant_slug_taken",
        status_code=status.HTTP_409_CONFLICT,
    )


def _token_for(user: User) -> TokenResponse:
    token = create_access_token(user_id=user.id, tenant_id=user.tenant_id, role=user.role)
    return TokenResponse(
        access_token=token,
        tenant_id=user.tenant_id,
        user_id=user.id,
        role=Role(user.role),
    )


@router.post(
    "/register-tenant",
    response_model=ApiResponse[TenantRegisterResponse],
    status_code=status.HTTP_201_CREATED,
)
def register_tenant(
    request: Request,
    payload: TenantRegisterRequest,
    db: Session = DbSessionDependency,
) -> ApiResponse[TenantRegisterResponse]:
    # Fast, friendly path for the common (sequential) duplicate: a clear 409 without
    # hitting the DB constraint. This only narrows the race window — correctness does
    # NOT depend on it; the try/except below closes a genuine concurrent race (P1-①).
    if is_platform_tenant_slug(payload.tenant_slug) or db.scalar(
        select(Tenant).where(Tenant.slug == payload.tenant_slug)
    ) is not None:
        raise _tenant_slug_taken_error()

    # The whole write — tenant/user/org/subscription create + flush + commit — runs in
    # ONE try so an IntegrityError from EITHER flush (the tenant INSERT) or commit is
    # caught and mapped to the unified-envelope 409 (P1-①). The only unique constraint
    # reachable here is tenants.slug (global): email is unique per-tenant
    # (uq_users_tenant_email), so a brand-new tenant's first user never collides, and
    # the same email may register under different tenants — one person, many orgs (P1-②).
    try:
        tenant = Tenant(slug=payload.tenant_slug, name=payload.tenant_name)
        db.add(tenant)
        db.flush()

        user = User(
            tenant_id=tenant.id,
            email=payload.email,
            password_hash=hash_password(payload.password),
            full_name=payload.full_name,
            role=Role.ADMIN.value,
        )
        db.add(user)
        db.add(Organization(tenant_id=tenant.id, name=payload.tenant_name))
        # New tenants get an active subscription from the default plan, same
        # transaction (P0-B). If no plan is configured it logs a warning and returns
        # None (registration still succeeds) rather than 500.
        create_default_subscription(db, tenant.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # A concurrent register won the race between our pre-check and flush/commit —
        # the slug is taken (only tenants.slug can violate here; see above).
        raise _tenant_slug_taken_error() from exc

    db.refresh(tenant)
    db.refresh(user)
    data = TenantRegisterResponse(tenant=tenant, user=user, token=_token_for(user))
    return ok(request, data)


@router.post("/login", response_model=ApiResponse[TokenResponse])
def login(
    request: Request,
    payload: LoginRequest,
    db: Session = DbSessionDependency,
) -> ApiResponse[TokenResponse]:
    query = (
        select(User)
        .join(Tenant)
        .where(
            User.email == payload.email,
            User.is_active.is_(True),
            Tenant.slug == payload.tenant_slug,
        )
    )
    users = db.scalars(query).all()
    user = next(
        (
            candidate
            for candidate in users
            if verify_password(payload.password, candidate.password_hash)
        ),
        None,
    )
    if user is None:
        raise AppError(
            "Invalid email or password.",
            code="INVALID_CREDENTIALS",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return ok(request, _token_for(user))


@router.get("/me", response_model=ApiResponse[CurrentUserResponse])
def me(
    request: Request,
    user: User = CurrentUserDependency,
    tenant: Tenant = CurrentTenantDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[CurrentUserResponse]:
    data = CurrentUserResponse(
        tenant=TenantRead.model_validate(tenant),
        user=UserRead.model_validate(user),
        permissions=sorted(session_permissions_for_user(db, user=user)),
    )
    return ok(request, data)


@router.get("/admin-check", response_model=ApiResponse[CurrentUserResponse])
def admin_check(
    request: Request,
    user: User = AdminPermissionDependency,
    tenant: Tenant = CurrentTenantDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[CurrentUserResponse]:
    data = CurrentUserResponse(
        tenant=TenantRead.model_validate(tenant),
        user=UserRead.model_validate(user),
        permissions=sorted(session_permissions_for_user(db, user=user)),
    )
    return ok(request, data)
