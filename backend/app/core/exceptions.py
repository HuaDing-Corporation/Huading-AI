import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.response import ApiResponse, ErrorDetail, OperationOutcome

logger = structlog.get_logger(__name__)
_COPY_GENERATION_OPERATION_SUFFIXES = {
    "/copy/rewrite": "rewrite",
    "/copy/titles": "titles",
    "/copy/topics": "topics",
}


class AppError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "APP_ERROR",
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: object | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _copy_generation_error_outcome(request: Request) -> OperationOutcome | None:
    path = request.url.path.rstrip("/")
    operation = next(
        (
            operation
            for suffix, operation in _COPY_GENERATION_OPERATION_SUFFIXES.items()
            if path.endswith(suffix)
        ),
        None,
    )
    if operation is None:
        return None
    return OperationOutcome(operation=operation, status="failed")


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
    outcome = _copy_generation_error_outcome(request)
    payload = ApiResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=request_id,
            detail=detail,
            details=details,
            outcome=outcome,
        ),
        request_id=request_id,
    )
    # Attach X-Request-ID here so every error response carries it — including the
    # 500 handler, whose response is produced by ServerErrorMiddleware (outside
    # RequestIdMiddleware, which therefore can't add the header) (#003-FIX P2).
    headers = {"X-Request-ID": request_id} if request_id else None
    content = payload.model_dump(mode="json")
    if outcome is None and isinstance(content.get("error"), dict):
        content["error"].pop("outcome", None)
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=headers,
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        detail=exc.detail,
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


def _validation_code(errors: list[dict[str, object]]) -> str:
    friendly_codes = {
        "friendly_video_gen_prompt_too_long": "VIDEO_GEN_PROMPT_TOO_LONG",
        "friendly_video_gen_reference_media_conflict": (
            "VIDEO_GEN_REFERENCE_MEDIA_CONFLICT"
        ),
        "friendly_video_gen_reference_video_count_invalid": (
            "VIDEO_GEN_REFERENCE_VIDEO_COUNT_INVALID"
        ),
        "friendly_video_gen_reference_video_duplicate": (
            "VIDEO_GEN_REFERENCE_VIDEO_DUPLICATE"
        ),
    }
    for error in errors:
        code = friendly_codes.get(str(error.get("type") or ""))
        if code is not None:
            return code
    return "VALIDATION_ERROR"


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
        code=_validation_code(errors),
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
