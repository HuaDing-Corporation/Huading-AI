from fastapi import APIRouter, Request

from app.api.deps import CurrentUserDependency
from app.schemas.oral import SubtitleTemplateList
from app.schemas.response import ApiResponse, ok
from app.services.subtitle_styles import subtitle_templates

router = APIRouter()


@router.get("/subtitle-templates", response_model=ApiResponse[SubtitleTemplateList])
def get_subtitle_templates(
    request: Request,
    _user=CurrentUserDependency,
) -> ApiResponse[SubtitleTemplateList]:
    return ok(request, SubtitleTemplateList(templates=subtitle_templates()))
