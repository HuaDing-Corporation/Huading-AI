from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import DbSessionDependency, get_object_storage, require_permission
from app.db.models import User
from app.schemas.response import ApiResponse, ok
from app.schemas.reverse_prompt import (
    ReversePromptCreateRequest,
    ReversePromptJobRead,
    ReversePromptSavedResponse,
)
from app.services.reverse_prompt import (
    create_reverse_prompt_job,
    job_to_read,
    regenerate_reverse_prompt_job,
    reverse_prompt_job_or_404,
    save_reverse_prompt_job,
)
from app.services.storage.base import ObjectStorage

router = APIRouter()
ReversePromptPermissionDependency = Depends(require_permission("video:create"))


def _object_storage_dependency() -> ObjectStorage:
    return get_object_storage()


ObjectStorageDependency = Depends(_object_storage_dependency)


@router.post(
    "",
    response_model=ApiResponse[ReversePromptJobRead],
    status_code=status.HTTP_201_CREATED,
)
def reverse_prompt_sync(
    request: Request,
    payload: ReversePromptCreateRequest,
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
    return ok(request, ReversePromptJobRead.model_validate(job_to_read(job)))


@router.post(
    "/jobs",
    response_model=ApiResponse[ReversePromptJobRead],
    status_code=status.HTTP_201_CREATED,
)
def create_reverse_prompt_job_endpoint(
    request: Request,
    payload: ReversePromptCreateRequest,
    user: User = ReversePromptPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[ReversePromptJobRead]:
    return reverse_prompt_sync(request, payload, user=user, db=db, storage=storage)


@router.get("/jobs/{job_id}", response_model=ApiResponse[ReversePromptJobRead])
def get_reverse_prompt_job(
    request: Request,
    job_id: str,
    user: User = ReversePromptPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ReversePromptJobRead]:
    job = reverse_prompt_job_or_404(db, tenant_id=user.tenant_id, job_id=job_id)
    return ok(request, ReversePromptJobRead.model_validate(job_to_read(job)))


@router.post("/jobs/{job_id}/regenerate", response_model=ApiResponse[ReversePromptJobRead])
def regenerate_reverse_prompt(
    request: Request,
    job_id: str,
    user: User = ReversePromptPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[ReversePromptJobRead]:
    job = regenerate_reverse_prompt_job(db, user=user, job_id=job_id, storage=storage)
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
