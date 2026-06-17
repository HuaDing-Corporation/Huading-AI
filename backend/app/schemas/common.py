from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[object] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
