from fastapi import APIRouter, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency
from app.db.models import User
from app.schemas.copy import (
    CopyDraftClearResponse,
    CopyDraftCreateRequest,
    CopyDraftDeletedResponse,
    CopyDraftListResponse,
    CopyDraftRead,
    CopyGenerationOutcome,
    CopyRewriteRequest,
    CopyRewriteResponse,
    CopyRewriteResult,
    CopyTitlesRequest,
    CopyTitlesResponse,
    CopyTopicsRequest,
    CopyTopicsResponse,
)
from app.schemas.response import ApiResponse, ok
from app.services.copy import (
    clear_drafts,
    create_draft,
    delete_draft,
    generate_titles,
    generate_topics,
    get_draft,
    list_drafts,
    rewrite_copy,
)

router = APIRouter()


@router.post("/rewrite", response_model=ApiResponse[CopyRewriteResponse])
def rewrite(
    request: Request,
    payload: CopyRewriteRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[CopyRewriteResponse]:
    results = rewrite_copy(db, tenant_id=user.tenant_id, payload=payload)
    response = CopyRewriteResponse(
        results=[CopyRewriteResult(text=text) for text in results],
        outcome=CopyGenerationOutcome(operation="rewrite", status="succeeded"),
    )
    return ok(request, response)


@router.post("/titles", response_model=ApiResponse[CopyTitlesResponse])
def titles(
    request: Request,
    payload: CopyTitlesRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[CopyTitlesResponse]:
    title_items = generate_titles(db, tenant_id=user.tenant_id, payload=payload)
    return ok(
        request,
        CopyTitlesResponse(
            titles=title_items,
            outcome=CopyGenerationOutcome(operation="titles", status="succeeded"),
        ),
    )


@router.post("/topics", response_model=ApiResponse[CopyTopicsResponse])
def topics(
    request: Request,
    payload: CopyTopicsRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[CopyTopicsResponse]:
    topic_items = generate_topics(db, tenant_id=user.tenant_id, payload=payload)
    return ok(
        request,
        CopyTopicsResponse(
            topics=topic_items,
            outcome=CopyGenerationOutcome(operation="topics", status="succeeded"),
        ),
    )


@router.post(
    "/drafts",
    response_model=ApiResponse[CopyDraftRead],
    status_code=status.HTTP_201_CREATED,
)
def save_draft(
    request: Request,
    payload: CopyDraftCreateRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[CopyDraftRead]:
    draft = create_draft(db, tenant_id=user.tenant_id, payload=payload)
    return ok(request, CopyDraftRead.model_validate(draft))


@router.get("/drafts", response_model=ApiResponse[CopyDraftListResponse])
def draft_list(
    request: Request,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[CopyDraftListResponse]:
    items, total = list_drafts(db, tenant_id=user.tenant_id, limit=limit, offset=offset)
    return ok(
        request,
        CopyDraftListResponse(
            items=[CopyDraftRead.model_validate(item) for item in items],
            total=total,
        ),
    )


@router.get("/drafts/{draft_id}", response_model=ApiResponse[CopyDraftRead])
def draft_detail(
    request: Request,
    draft_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[CopyDraftRead]:
    draft = get_draft(db, tenant_id=user.tenant_id, draft_id=draft_id)
    return ok(request, CopyDraftRead.model_validate(draft))


@router.delete("/drafts", response_model=ApiResponse[CopyDraftClearResponse])
def draft_clear(
    request: Request,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[CopyDraftClearResponse]:
    deleted_count = clear_drafts(db, tenant_id=user.tenant_id)
    return ok(request, CopyDraftClearResponse(deleted_count=deleted_count))


@router.delete("/drafts/{draft_id}", response_model=ApiResponse[CopyDraftDeletedResponse])
def draft_delete(
    request: Request,
    draft_id: str,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[CopyDraftDeletedResponse]:
    draft = delete_draft(db, tenant_id=user.tenant_id, draft_id=draft_id)
    return ok(
        request,
        CopyDraftDeletedResponse(id=draft.id, deleted_at=draft.deleted_at),
    )
