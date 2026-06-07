from typing import Generic, TypeVar

from fastapi import Request
from pydantic import BaseModel

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str
    detail: object | None = None


class ApiResponse(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorDetail | None = None
    request_id: str | None = None


def ok(request: Request, data: T) -> ApiResponse[T]:
    return ApiResponse(data=data, request_id=getattr(request.state, "request_id", None))
