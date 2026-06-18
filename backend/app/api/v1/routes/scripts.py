import asyncio

from fastapi import APIRouter, Request
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency
from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import User
from app.providers.base import invoke, resolve
from app.schemas.response import ApiResponse, ok
from app.schemas.scripts import ScriptGenerateRequest, ScriptGenerateResponse

router = APIRouter()


@router.post("/generate", response_model=ApiResponse[ScriptGenerateResponse])
def generate_script(
    request: Request,
    payload: ScriptGenerateRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ScriptGenerateResponse]:
    topic = payload.topic.strip()
    if not (
        settings.engine_llm_api_key
        and settings.engine_llm_base_url
        and settings.engine_llm_model
    ):
        raise AppError(
            "DeepSeek is not configured.",
            code="LLM_NOT_CONFIGURED",
            status_code=503,
        )
    provider = resolve(db, tenant_id=user.tenant_id, capability="llm")
    result = asyncio.run(
        invoke(
            db,
            tenant_id=user.tenant_id,
            capability="llm",
            provider=provider.__class__.__name__,
            operation=lambda: provider.generate_text({"topic": topic}),
            timeout_seconds=30.0,
        )
    )
    script = str(result.get("text") or "").strip()
    if not script:
        raise AppError(
            "DeepSeek returned an empty script.",
            code="LLM_EMPTY_RESULT",
            status_code=502,
        )
    return ok(request, ScriptGenerateResponse(script=script))
