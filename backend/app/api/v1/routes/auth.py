from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUserDependency,
    DbSessionDependency,
    get_current_tenant,
    permissions_for_role,
    require_permission,
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

router = APIRouter()
CurrentTenantDependency = Depends(get_current_tenant)
AdminPermissionDependency = Depends(require_permission("tenant:admin"))


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
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "Tenant slug or user email already exists.",
            code="TENANT_ALREADY_EXISTS",
            status_code=status.HTTP_409_CONFLICT,
        ) from exc

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
    query = select(User).where(User.email == payload.email, User.is_active.is_(True))
    if payload.tenant_slug:
        query = query.join(Tenant).where(Tenant.slug == payload.tenant_slug)
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
) -> ApiResponse[CurrentUserResponse]:
    data = CurrentUserResponse(
        tenant=TenantRead.model_validate(tenant),
        user=UserRead.model_validate(user),
        permissions=sorted(permissions_for_role(user.role)),
    )
    return ok(request, data)


@router.get("/admin-check", response_model=ApiResponse[CurrentUserResponse])
def admin_check(
    request: Request,
    user: User = AdminPermissionDependency,
    tenant: Tenant = CurrentTenantDependency,
) -> ApiResponse[CurrentUserResponse]:
    data = CurrentUserResponse(
        tenant=TenantRead.model_validate(tenant),
        user=UserRead.model_validate(user),
        permissions=sorted(permissions_for_role(user.role)),
    )
    return ok(request, data)
