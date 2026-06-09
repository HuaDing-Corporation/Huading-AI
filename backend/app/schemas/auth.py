from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import Role


class TenantRegisterRequest(BaseModel):
    tenant_slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    tenant_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=200)

    model_config = ConfigDict(extra="forbid")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("invalid email")
        return normalized


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    tenant_slug: str = Field(min_length=2, max_length=80)

    model_config = ConfigDict(extra="forbid")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("invalid email")
        return normalized


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tenant_id: str
    user_id: str
    role: Role


class TenantRead(BaseModel):
    id: str
    slug: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class UserRead(BaseModel):
    id: str
    tenant_id: str
    email: str
    full_name: str | None = None
    role: Role

    model_config = ConfigDict(from_attributes=True)


class TenantRegisterResponse(BaseModel):
    tenant: TenantRead
    user: UserRead
    token: TokenResponse


class CurrentUserResponse(BaseModel):
    tenant: TenantRead
    user: UserRead
    permissions: list[str]
