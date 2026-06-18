import asyncio

from fastapi import APIRouter, Request

from app.api.deps import CurrentUserDependency
from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import User
from app.providers.llm.deepseek import DeepSeekProvider
from app.schemas.response import ApiResponse, ok
from app.schemas.scripts import ScriptGenerateRequest, ScriptGenerateResponse

router = APIRouter()


@router.post("/generate", response_model=ApiResponse[ScriptGenerateResponse])
def generate_script(
    request: Request,
    payload: ScriptGenerateRequest,
    _user: User = CurrentUserDependency,
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
    provider = DeepSeekProvider(
        api_key=settings.engine_llm_api_key,
        base_url=settings.engine_llm_base_url,
        model=settings.engine_llm_model,
    )
    result = asyncio.run(provider.generate_text({"topic": topic}))
    script = str(result.get("text") or "").strip()
    if not script:
        raise AppError(
            "DeepSeek returned an empty script.",
            code="LLM_EMPTY_RESULT",
            status_code=502,
        )
    return ok(request, ScriptGenerateResponse(script=script))
