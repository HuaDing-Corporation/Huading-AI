from collections.abc import Generator

import redis
from fastapi import Depends, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import decode_access_token
from app.db.models import Role, Tenant, User
from app.db.session import SessionLocal
from app.services.progress import ProgressStore, build_progress_store
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage


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


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
DbSessionDependency = Depends(get_db_session)
TokenDependency = Depends(oauth2_scheme)

_ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN: {"tenant:admin", "content:operate", "video:create", "video:review", "dev:access"},
    Role.OPS: {"content:operate", "video:review"},
    Role.CREATOR: {"video:create"},
    Role.REVIEWER: {"video:review"},
    Role.DEVELOPER: {"dev:access", "video:create"},
}


def permissions_for_role(role: Role | str) -> set[str]:
    role_value = Role(role)
    return _ROLE_PERMISSIONS[role_value]


def get_current_user(
    request: Request,
    token: str = TokenDependency,
    db: Session = DbSessionDependency,
) -> User:
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not isinstance(user_id, str) or not isinstance(tenant_id, str):
        raise AppError(
            "Invalid token payload.",
            code="INVALID_TOKEN",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    header_tenant_id = getattr(request.state, "tenant_id", None)
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
    request.state.tenant_id = tenant_id
    request.state.user_id = user.id
    request.state.role = user.role
    request.state.current_user = user
    return user


CurrentUserDependency = Depends(get_current_user)


def get_current_tenant(
    db: Session = DbSessionDependency,
    user: User = CurrentUserDependency,
) -> Tenant:
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None:
        raise AppError(
            "Tenant does not exist.",
            code="TENANT_NOT_FOUND",
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
