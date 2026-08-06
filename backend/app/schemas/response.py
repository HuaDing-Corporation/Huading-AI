from typing import Generic, Literal, TypeVar

from fastapi import Request
from pydantic import BaseModel

T = TypeVar("T")


class OperationOutcome(BaseModel):
    operation: str
    status: Literal["succeeded", "failed"]


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    detail: object | None = None
    details: list[object] | None = None
    outcome: OperationOutcome | None = None


class ApiResponse(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorDetail | None = None
    request_id: str | None = None


def ok(request: Request, data: T) -> ApiResponse[T]:
    return ApiResponse(data=data, request_id=getattr(request.state, "request_id", None))
