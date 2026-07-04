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
from app.services import provider_costs
from app.workers.avatar_talk import build_script_payload, clean_spoken_script

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
            operation=lambda: provider.generate_text(
                build_script_payload(
                    topic,
                    video_mode=payload.video_mode,
                    duration_sec=payload.duration_sec,
                )
            ),
            timeout_seconds=30.0,
        )
    )
    provider_costs.record_deepseek_usage(
        db,
        tenant_id=user.tenant_id,
        result=result,
    )
    script = clean_spoken_script(str(result.get("text") or ""))
    if not script:
        raise AppError(
            "DeepSeek returned an empty script.",
            code="LLM_EMPTY_RESULT",
            status_code=502,
        )
    db.commit()
    return ok(request, ScriptGenerateResponse(script=script))
