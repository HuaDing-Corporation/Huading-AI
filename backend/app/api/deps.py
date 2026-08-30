from collections.abc import Generator
from typing import Annotated
from uuid import UUID

import redis
import structlog
from fastapi import Depends, Header, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import decode_access_token
from app.db.models import Role, Tenant, User
from app.db.session import SessionLocal
from app.services.plan_access import (
    AnalyticsScope,
    analytics_scope_for_tenant,
    is_authorized_platform_admin,
    tenant_entitlements,
)
from app.services.progress import ProgressStore, build_progress_store
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage


class BillingSubmissionHeaders(BaseModel):
    idempotency_key: UUID
    quote_token: str


def _validated_quote_token(quote_token: str) -> str:
    from app.services.billing_quotes import QUOTE_TOKEN_MAX_BYTES

    try:
        encoded = quote_token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise AppError(
            "报价凭证格式无效",
            code="QUOTE_TOKEN_INVALID",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    if len(encoded) > QUOTE_TOKEN_MAX_BYTES:
        raise AppError(
            "报价凭证过长",
            code="QUOTE_TOKEN_TOO_LARGE",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return quote_token


def require_billing_submission_headers(
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    quote_token: Annotated[str, Header(alias="X-Huading-Quote", min_length=1)],
) -> BillingSubmissionHeaders:
    return BillingSubmissionHeaders(
        idempotency_key=idempotency_key,
        quote_token=_validated_quote_token(quote_token),
    )


def optional_billing_submission_headers(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    quote_token: Annotated[str | None, Header(alias="X-Huading-Quote")] = None,
) -> BillingSubmissionHeaders | None:
    if idempotency_key is None and quote_token is None:
        return None
    if idempotency_key is None or quote_token is None or not quote_token:
        raise AppError(
            "Idempotency-Key and X-Huading-Quote must be provided together.",
            code="BILLING_HEADERS_REQUIRED",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    try:
        parsed_idempotency_key = UUID(idempotency_key)
    except ValueError as exc:
        raise AppError(
            "Idempotency-Key is invalid.",
            code="INVALID_IDEMPOTENCY_KEY",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    return BillingSubmissionHeaders(
        idempotency_key=parsed_idempotency_key,
        quote_token=_validated_quote_token(quote_token),
    )


def get_settings_dependency() -> Settings:
    return get_settings()


def get_db_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_redis_client() -> redis.Redis:
    settings = get_settings()
    # Socket timeouts so an unreachable Redis degrades fast (#003-FIX P2).
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_socket_connect_timeout,
        socket_timeout=settings.redis_socket_timeout,
    )


def get_object_storage() -> ObjectStorage:
    settings = get_settings()
    return create_object_storage(settings)


def get_progress_store() -> ProgressStore:
    settings = get_settings()
    return build_progress_store(settings.redis_url)


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
DbSessionDependency = Depends(get_db_session)
TokenDependency = Depends(oauth2_scheme)

_ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN: {
        "tenant:admin",
        "content:operate",
        "video:create",
        "video:review",
        "dev:access",
        "aibrain:chat",
    },
    Role.OPS: {"content:operate", "video:review"},
    Role.CREATOR: {"video:create", "aibrain:chat"},
    Role.REVIEWER: {"video:review"},
    Role.DEVELOPER: {"dev:access", "video:create", "aibrain:chat"},
}


def permissions_for_role(role: Role | str) -> set[str]:
    role_value = Role(role)
    return _ROLE_PERMISSIONS[role_value]


def session_permissions_for_user(db: Session, *, user: User) -> set[str]:
    permissions = set(permissions_for_role(user.role))
    permissions.update(
        tenant_entitlements(db, tenant_id=user.tenant_id, role=user.role)
    )
    return permissions


def get_current_user(
    request: Request,
    token: str | None = TokenDependency,
    db: Session = DbSessionDependency,
) -> User:
    if not token:
        raise AppError(
            "Missing access token.",
            code="UNAUTHORIZED",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not isinstance(user_id, str) or not isinstance(tenant_id, str):
        raise AppError(
            "Invalid token payload.",
            code="INVALID_TOKEN",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    header_tenant_id = getattr(request.state, "requested_tenant_id", None)
    if header_tenant_id and header_tenant_id != tenant_id:
        raise AppError(
            "Authenticated user does not belong to the requested tenant.",
            code="TENANT_MISMATCH",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    user = db.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
    if user is None or not user.is_active:
        raise AppError(
            "User is inactive or does not exist.",
            code="USER_NOT_FOUND",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != "active" or tenant.deleted_at is not None:
        raise AppError(
            "Tenant is inactive or does not exist.",
            code="TENANT_INACTIVE",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    request.state.tenant_id = tenant_id
    request.state.user_id = user.id
    request.state.role = user.role
    request.state.current_user = user
    structlog.contextvars.bind_contextvars(tenant_id=tenant_id, user_id=user.id)
    return user


CurrentUserDependency = Depends(get_current_user)


def get_current_tenant(
    db: Session = DbSessionDependency,
    user: User = CurrentUserDependency,
) -> Tenant:
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None or tenant.status != "active" or tenant.deleted_at is not None:
        raise AppError(
            "Tenant is inactive or does not exist.",
            code="TENANT_INACTIVE",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return tenant


def require_roles(*roles: Role):
    allowed = {role.value for role in roles}

    def dependency(user: User = CurrentUserDependency) -> User:
        if user.role not in allowed:
            raise AppError(
                "Insufficient role.",
                code="FORBIDDEN",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return user

    return dependency


require_role = require_roles


def require_permission(permission: str):
    def dependency(user: User = CurrentUserDependency) -> User:
        if permission not in permissions_for_role(user.role):
            raise AppError(
                "Insufficient permission.",
                code="FORBIDDEN",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return user

    return dependency


def require_admin(user: User = CurrentUserDependency) -> User:
    if "tenant:admin" not in permissions_for_role(user.role):
        raise AppError(
            "Insufficient permission.",
            code="FORBIDDEN",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return user


def require_platform_admin(
    db: Session = DbSessionDependency,
    user: User = CurrentUserDependency,
) -> User:
    if is_authorized_platform_admin(
        db,
        user=user,
        expected_tenant_id=user.tenant_id,
    ):
        return user
    raise AppError(
        "Platform administrator access is required.",
        code="PLATFORM_ADMIN_REQUIRED",
        status_code=status.HTTP_403_FORBIDDEN,
    )


def require_analytics_access(
    db: Session = DbSessionDependency,
    user: User = CurrentUserDependency,
) -> AnalyticsScope:
    scope = analytics_scope_for_tenant(db, tenant_id=user.tenant_id)
    if scope is not None:
        return scope
    raise AppError(
        "Analytics access requires the Huading plan.",
        code="ANALYTICS_PLAN_REQUIRED",
        status_code=status.HTTP_403_FORBIDDEN,
    )


def scoped_task_id(tenant_id: str, task_id: str) -> str:
    return f"{tenant_id}:{task_id}"


def tenant_storage_key(tenant_id: str, key: str) -> str:
    return f"tenants/{tenant_id}/{key}"


def ensure_same_tenant(entity_tenant_id: str | None, current_tenant_id: str) -> None:
    if entity_tenant_id != current_tenant_id:
        raise AppError(
            "Resource does not belong to the current tenant.",
            code="TENANT_RESOURCE_MISMATCH",
            status_code=status.HTTP_403_FORBIDDEN,
        )
