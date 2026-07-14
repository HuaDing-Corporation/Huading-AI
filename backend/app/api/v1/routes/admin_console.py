from __future__ import annotations

import csv
import io
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi import status as http_status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency, require_platform_admin
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.models import User, VideoTask
from app.schemas.admin_console import (
    AdminAuditLogPage,
    AdminCreditsAdjustRequest,
    AdminCreditsAdjustResponse,
    AdminPlanChangeRequest,
    AdminPlanChangeResponse,
    AdminTaskFamily,
    AdminTaskPage,
    AdminTaskRetryResponse,
    AdminTenantDetail,
    AdminTenantPage,
    AdminTenantStatusRequest,
    AdminTenantStatusResponse,
    AdminUsagePage,
    AdminVoiceSlotAssignRequest,
    AdminVoiceSlotAssignResponse,
    AdminVoiceSlotsResponse,
)
from app.schemas.response import ApiResponse, ok
from app.services import admin_console
from app.services.voice_slots import SpeakerSlotAssignmentError
from app.workers.avatar_talk import generate_avatar_talk_task, generate_seedance_i2v_task
from app.workers.image_gen import generate_ecom_replicate_task, generate_image_task
from app.workers.reverse_prompt import generate_reverse_prompt_video_task
from app.workers.video_gen import generate_video_gen_task
from app.workers.video_tasks import generate_video_task

router = APIRouter(dependencies=[Depends(require_platform_admin)])
logger = get_logger(__name__)

PageQuery = Annotated[int, Query(ge=1)]
PageSizeQuery = Annotated[int, Query(ge=1, le=100)]
FromDateQuery = Annotated[date | None, Query(alias="from")]
ToDateQuery = Annotated[date | None, Query()]


@router.get("/tenants", response_model=ApiResponse[AdminTenantPage])
def list_tenants(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    plan: Literal["free", "basic", "huading"] | None = None,
    status: Literal["active", "suspended", "closed"] | None = None,
    sort: Literal["credits_used", "created_at", "balance"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminTenantPage]:
    return ok(
        request,
        admin_console.list_tenants(
            db,
            q=q,
            plan=plan,
            status=status,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        ),
    )


@router.get("/tenants/{tenant_id}", response_model=ApiResponse[AdminTenantDetail])
def get_tenant(
    request: Request,
    tenant_id: str,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminTenantDetail]:
    tenant = admin_console.tenant_item_or_404(db, tenant_id=tenant_id)
    slots = [item for item in admin_console.voice_slots(db).items if item.tenant_id == tenant_id]
    return ok(
        request,
        AdminTenantDetail(
            tenant=tenant,
            recent_tasks=admin_console.recent_tasks(db, tenant_id=tenant_id),
            recent_usage=admin_console.recent_usage(db, tenant_id=tenant_id),
            voice_slots=slots,
        ),
    )


@router.post(
    "/tenants/{tenant_id}/credits",
    response_model=ApiResponse[AdminCreditsAdjustResponse],
)
def adjust_tenant_credits(
    request: Request,
    tenant_id: str,
    payload: AdminCreditsAdjustRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminCreditsAdjustResponse]:
    try:
        result = admin_console.adjust_credits(
            db,
            actor=user,
            tenant_id=tenant_id,
            delta=payload.delta,
            reason=payload.reason,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(request, result)


@router.patch(
    "/tenants/{tenant_id}/plan",
    response_model=ApiResponse[AdminPlanChangeResponse],
)
def change_tenant_plan(
    request: Request,
    tenant_id: str,
    payload: AdminPlanChangeRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminPlanChangeResponse]:
    try:
        result = admin_console.change_plan(
            db,
            actor=user,
            tenant_id=tenant_id,
            plan_code=payload.plan_code,
            reason=payload.reason,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(request, result)


@router.patch(
    "/tenants/{tenant_id}/status",
    response_model=ApiResponse[AdminTenantStatusResponse],
)
def change_tenant_status(
    request: Request,
    tenant_id: str,
    payload: AdminTenantStatusRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminTenantStatusResponse]:
    try:
        result = admin_console.change_tenant_status(
            db,
            actor=user,
            tenant_id=tenant_id,
            active=payload.active,
            reason=payload.reason,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(request, result)


@router.get("/voice-slots", response_model=ApiResponse[AdminVoiceSlotsResponse])
def get_voice_slots(
    request: Request,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminVoiceSlotsResponse]:
    return ok(request, admin_console.voice_slots(db))


@router.post(
    "/tenants/{tenant_id}/voice-slots",
    response_model=ApiResponse[AdminVoiceSlotAssignResponse],
)
def assign_voice_slot(
    request: Request,
    tenant_id: str,
    payload: AdminVoiceSlotAssignRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminVoiceSlotAssignResponse]:
    try:
        result = admin_console.assign_tenant_voice_slot(
            db,
            actor=user,
            tenant_id=tenant_id,
            speaker_id=payload.speaker_id,
            reason=payload.reason,
        )
        db.commit()
    except SpeakerSlotAssignmentError as exc:
        db.rollback()
        raise AppError(
            str(exc),
            code="VOICE_SLOT_ASSIGNMENT_FAILED",
            status_code=422,
        ) from exc
    except Exception:
        db.rollback()
        raise
    return ok(request, result)


@router.get("/usage", response_model=ApiResponse[AdminUsagePage])
def get_usage(
    request: Request,
    tenant_id: str | None = None,
    from_: FromDateQuery = None,
    to: ToDateQuery = None,
    capability: str | None = Query(default=None, max_length=32),
    provider: str | None = Query(default=None, max_length=40),
    status: Literal["reserved", "settled", "released"] | None = None,
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminUsagePage]:
    return ok(
        request,
        admin_console.list_usage(
            db,
            tenant_id=tenant_id,
            from_=from_,
            to=to,
            capability=capability,
            provider=provider,
            status=status,
            page=page,
            page_size=page_size,
        ),
    )


@router.get("/usage/export", response_class=Response)
def export_usage(
    tenant_id: str | None = None,
    from_: FromDateQuery = None,
    to: ToDateQuery = None,
    capability: str | None = Query(default=None, max_length=32),
    provider: str | None = Query(default=None, max_length=40),
    status: Literal["reserved", "settled", "released"] | None = None,
    db: Session = DbSessionDependency,
) -> Response:
    rows = admin_console.export_usage_rows(
        db,
        tenant_id=tenant_id,
        from_=from_,
        to=to,
        capability=capability,
        provider=provider,
        status=status,
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "created_at",
            "tenant_id",
            "tenant_slug",
            "tenant_name",
            "capability",
            "provider",
            "model",
            "quantity",
            "unit",
            "credits",
            "cost_cents",
            "status",
            "video_task_id",
        ]
    )
    for item in rows:
        writer.writerow(
            [
                item.created_at.isoformat(),
                item.tenant_id,
                item.tenant_slug,
                item.tenant_name,
                item.capability,
                item.provider,
                item.model or "",
                item.quantity,
                item.unit,
                item.credits,
                item.cost_cents,
                item.status,
                item.video_task_id or "",
            ]
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="usage-records.csv"'},
    )


@router.get("/tasks", response_model=ApiResponse[AdminTaskPage])
def get_tasks(
    request: Request,
    task_family: AdminTaskFamily | None = None,
    tenant_id: str | None = None,
    status: Literal["queued", "running", "done", "succeeded", "failed", "cancelled"]
    | None = None,
    from_: FromDateQuery = None,
    to: ToDateQuery = None,
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminTaskPage]:
    return ok(
        request,
        admin_console.list_tasks(
            db,
            task_family=task_family,
            tenant_id=tenant_id,
            status=status,
            from_=from_,
            to=to,
            page=page,
            page_size=page_size,
        ),
    )


def _enqueue_task_retry(task: VideoTask) -> None:
    payload = admin_console.task_retry_payload(task)
    if task.video_mode == "photo":
        generate_image_task.apply_async(args=[payload], task_id=task.id, queue="image")
    elif task.video_mode == "seedance_i2v":
        generate_seedance_i2v_task.apply_async(args=[payload], task_id=task.id, queue="video")
    elif task.video_mode == "video_gen":
        generate_video_gen_task.apply_async(args=[payload], task_id=task.id, queue="video")
    elif task.video_mode == "avatar_talk":
        generate_avatar_talk_task.apply_async(args=[payload], task_id=task.id, queue="avatar")
    else:
        generate_video_task.apply_async(args=[payload], task_id=task.id, queue="default")


def _compensate_retry_dispatch_failure(
    db: Session,
    *,
    actor_id: str,
    task_family: AdminTaskFamily,
    task_id: str,
    output_indexes: list[int],
) -> None:
    try:
        with Session(bind=db.get_bind()) as compensation_db:
            actor = compensation_db.get(User, actor_id)
            if actor is None:
                raise RuntimeError("Retry compensation actor was not found.")
            if task_family == "video":
                admin_console.compensate_task_retry_enqueue_failure(
                    compensation_db,
                    actor=actor,
                    task_id=task_id,
                )
            elif task_family == "reverse_prompt":
                admin_console.compensate_reverse_prompt_retry_enqueue_failure(
                    compensation_db,
                    actor=actor,
                    job_id=task_id,
                )
            else:
                admin_console.compensate_ecom_replicate_retry_enqueue_failure(
                    compensation_db,
                    actor=actor,
                    job_id=task_id,
                    output_indexes=output_indexes,
                )
            compensation_db.commit()
    except Exception:
        logger.exception(
            "admin_task_retry_compensation_failed",
            task_family=task_family,
            task_id=task_id,
        )


@router.post(
    "/tasks/{task_id}/retry",
    response_model=ApiResponse[AdminTaskRetryResponse],
    status_code=http_status.HTTP_202_ACCEPTED,
)
def retry_task(
    request: Request,
    task_id: str,
    task_family: AdminTaskFamily | None = None,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminTaskRetryResponse]:
    try:
        resolved_family = admin_console.resolve_task_family(
            db,
            task_id=task_id,
            requested_family=task_family,
        )
        if resolved_family == "video":
            preparation = admin_console.prepare_task_retry(db, actor=user, task_id=task_id)
        elif resolved_family == "reverse_prompt":
            preparation = admin_console.prepare_reverse_prompt_retry(
                db,
                actor=user,
                job_id=task_id,
            )
        else:
            preparation = admin_console.prepare_ecom_replicate_retry(
                db,
                actor=user,
                job_id=task_id,
            )
        task = preparation.task
        retried_output_indexes = list(preparation.output_indexes)
        response = AdminTaskRetryResponse(
            id=task.id,
            task_family=resolved_family,
            tenant_id=task.tenant_id,
            status="queued",
            progress=task.progress if resolved_family == "video" else 0,
            charged=preparation.charged,
            credits=preparation.credits,
            is_estimate=preparation.is_estimate,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    try:
        if resolved_family == "video":
            _enqueue_task_retry(task)
        elif resolved_family == "reverse_prompt":
            generate_reverse_prompt_video_task.apply_async(
                args=[task.id], task_id=task.id, queue="image"
            )
        else:
            generate_ecom_replicate_task.apply_async(
                args=[task.id], task_id=task.id, queue="image"
            )
    except Exception as exc:
        _compensate_retry_dispatch_failure(
            db,
            actor_id=user.id,
            task_family=resolved_family,
            task_id=task.id,
            output_indexes=retried_output_indexes,
        )
        raise AppError(
            "Task retry could not be queued. Please retry.",
            code="TASK_RETRY_ENQUEUE_FAILED",
            status_code=503,
        ) from exc
    return ok(request, response)


@router.get("/audit-logs", response_model=ApiResponse[AdminAuditLogPage])
def get_audit_logs(
    request: Request,
    action: Literal[
        "credits_adjust",
        "plan_change",
        "status_change",
        "voice_slot_assign",
        "task_retry",
    ]
    | None = None,
    target_tenant_id: str | None = None,
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
    db: Session = DbSessionDependency,
) -> ApiResponse[AdminAuditLogPage]:
    return ok(
        request,
        admin_console.list_audit_logs(
            db,
            action=action,
            target_tenant_id=target_tenant_id,
            page=page,
            page_size=page_size,
        ),
    )
