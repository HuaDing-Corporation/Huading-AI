import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.response import ApiResponse, ErrorDetail

logger = structlog.get_logger(__name__)


class AppError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "APP_ERROR",
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    detail: object | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    details = detail if isinstance(detail, list) else None
    payload = ApiResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=request_id,
            detail=detail,
            details=details,
        ),
        request_id=request_id,
    )
    # Attach X-Request-ID here so every error response carries it — including the
    # 500 handler, whose response is produced by ServerErrorMiddleware (outside
    # RequestIdMiddleware, which therefore can't add the header) (#003-FIX P2).
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code="HTTP_ERROR",
        message=str(exc.detail),
    )


def _validation_message(errors: list[dict[str, object]]) -> str:
    for error in errors:
        error_type = error.get("type")
        message = error.get("msg")
        if (
            isinstance(error_type, str)
            and error_type.startswith("friendly_")
            and isinstance(message, str)
            and message
        ):
            return message
    return "Request validation failed."


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    # jsonable_encoder makes the errors JSON-safe: custom validators raise
    # ValueError, which pydantic puts in ctx as a non-serializable object.
    errors = jsonable_encoder(exc.errors())
    return _error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message=_validation_message(errors),
        detail=errors,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_exception",
        error=str(exc),
        request_id=_request_id(request),
        exc_info=True,
    )
    return _error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_SERVER_ERROR",
        message="Internal server error.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
