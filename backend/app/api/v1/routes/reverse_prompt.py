from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import DbSessionDependency, get_object_storage, require_permission
from app.core.exceptions import AppError
from app.db.models import ReversePromptJob, User
from app.schemas.response import ApiResponse, ok
from app.schemas.reverse_prompt import (
    ReversePromptCreateRequest,
    ReversePromptJobRead,
    ReversePromptSavedResponse,
)
from app.services.reverse_prompt import (
    create_reverse_prompt_job,
    fail_reverse_prompt_video_dispatch,
    job_to_read,
    regenerate_reverse_prompt_job,
    reverse_prompt_job_or_404,
    save_reverse_prompt_job,
)
from app.services.storage.base import ObjectStorage
from app.workers.reverse_prompt import generate_reverse_prompt_video_task

router = APIRouter()
ReversePromptPermissionDependency = Depends(require_permission("video:create"))


def _object_storage_dependency() -> ObjectStorage:
    return get_object_storage()


ObjectStorageDependency = Depends(_object_storage_dependency)


def _enqueue_reverse_prompt_video(db: Session, job: ReversePromptJob) -> None:
    try:
        generate_reverse_prompt_video_task.apply_async(
            args=[job.id],
            task_id=job.id,
            queue="image",
        )
    except Exception as exc:
        fail_reverse_prompt_video_dispatch(
            db,
            job=job,
            message="Reverse prompt video could not be queued.",
        )
        raise AppError(
            "Reverse prompt video could not be queued.",
            code="REVERSE_PROMPT_QUEUE_FAILED",
            status_code=503,
        ) from exc


@router.post(
    "",
    response_model=ApiResponse[ReversePromptJobRead],
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_202_ACCEPTED: {"description": "Video analysis queued."}},
)
def reverse_prompt_sync(
    request: Request,
    payload: ReversePromptCreateRequest,
    response: Response,
    user: User = ReversePromptPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[ReversePromptJobRead]:
    job = create_reverse_prompt_job(
        db,
        user=user,
        source_asset_id=payload.source_asset_id,
        target_format=payload.target_format,
        storage=storage,
    )
    if job.source_kind == "video":
        _enqueue_reverse_prompt_video(db, job)
        response.status_code = status.HTTP_202_ACCEPTED
    return ok(request, ReversePromptJobRead.model_validate(job_to_read(job)))


@router.post(
    "/jobs",
    response_model=ApiResponse[ReversePromptJobRead],
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_202_ACCEPTED: {"description": "Video analysis queued."}},
)
def create_reverse_prompt_job_endpoint(
    request: Request,
    payload: ReversePromptCreateRequest,
    response: Response,
    user: User = ReversePromptPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[ReversePromptJobRead]:
    return reverse_prompt_sync(
        request,
        payload,
        response=response,
        user=user,
        db=db,
        storage=storage,
    )


@router.get("/jobs/{job_id}", response_model=ApiResponse[ReversePromptJobRead])
def get_reverse_prompt_job(
    request: Request,
    job_id: str,
    user: User = ReversePromptPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ReversePromptJobRead]:
    job = reverse_prompt_job_or_404(db, tenant_id=user.tenant_id, job_id=job_id)
    return ok(request, ReversePromptJobRead.model_validate(job_to_read(job)))


@router.post(
    "/jobs/{job_id}/regenerate",
    response_model=ApiResponse[ReversePromptJobRead],
    responses={status.HTTP_202_ACCEPTED: {"description": "Video analysis re-queued."}},
)
def regenerate_reverse_prompt(
    request: Request,
    job_id: str,
    response: Response,
    user: User = ReversePromptPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[ReversePromptJobRead]:
    job = regenerate_reverse_prompt_job(db, user=user, job_id=job_id, storage=storage)
    if job.source_kind == "video":
        _enqueue_reverse_prompt_video(db, job)
        response.status_code = status.HTTP_202_ACCEPTED
    return ok(request, ReversePromptJobRead.model_validate(job_to_read(job)))


@router.post("/jobs/{job_id}/save", response_model=ApiResponse[ReversePromptSavedResponse])
def save_reverse_prompt(
    request: Request,
    job_id: str,
    user: User = ReversePromptPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ReversePromptSavedResponse]:
    job = save_reverse_prompt_job(db, tenant_id=user.tenant_id, job_id=job_id)
    return ok(
        request,
        ReversePromptSavedResponse(
            id=job.id,
            saved_at=job.saved_at,
        ),
    )
