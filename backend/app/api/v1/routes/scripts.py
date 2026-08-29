import asyncio
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    BillingSubmissionHeaders,
    CurrentUserDependency,
    DbSessionDependency,
    require_billing_submission_headers,
)
from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import UsageRecord, User
from app.providers.base import ProviderInvocationError, invoke, resolve
from app.schemas.billing import BillingQuote
from app.schemas.response import ApiResponse, ok
from app.schemas.scripts import ScriptGenerateRequest, ScriptGenerateResponse
from app.services import provider_costs
from app.services.billing_operations import (
    BillingFailedLookup,
    BillingInProgressLookup,
    BillingSucceededLookup,
    ScriptGenerateStoredResult,
    UsageAllocation,
    billing_summary,
    complete_failed,
    complete_succeeded,
    create_reserved_operation,
    find_replay,
    lookup_operation,
)
from app.services.billing_quotes import issue_quote, request_sha256, verify_quote
from app.services.pricing import PRICING_POLICIES, build_simple_pricing, resolve_rate
from app.workers.avatar_talk import build_script_payload, clean_spoken_script

router = APIRouter()
_OPERATION = "script_generate"
BillingSubmissionHeadersDependency = Depends(require_billing_submission_headers)


def _normalized_request(payload: ScriptGenerateRequest) -> dict[str, object]:
    """The body is signed; billing values are carried only in HTTP headers."""
    return payload.model_dump(mode="json")


def _pricing_draft(db: Session, *, tenant_id: str):
    policy = PRICING_POLICIES[_OPERATION]
    return build_simple_pricing(
        policy=policy,
        rate=resolve_rate(db, tenant_id=tenant_id, policy=policy),
        quantity=Decimal("1"),
    )


def _failure_usage_from_error(exc: BaseException) -> object | None:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        usage = getattr(current, "usage_result", None)
        if isinstance(usage, dict):
            return usage
        current = current.__cause__ or current.__context__
    return None


def _replayed_response(
    db: Session, *, user: User, headers: BillingSubmissionHeaders
) -> ScriptGenerateResponse:
    lookup = lookup_operation(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation=_OPERATION,
        idempotency_key=headers.idempotency_key,
    )
    if isinstance(lookup, BillingSucceededLookup):
        stored = ScriptGenerateStoredResult.model_validate(lookup.result)
        return ScriptGenerateResponse(script=stored.script, billing=lookup.billing)
    if isinstance(lookup, BillingInProgressLookup):
        raise AppError(
            "Script generation is still in progress.",
            code="BILLING_OPERATION_IN_PROGRESS",
            status_code=409,
            detail={"billing": lookup.billing.model_dump(mode="json")},
        )
    if isinstance(lookup, BillingFailedLookup):
        raise AppError(
            "Script generation failed.",
            code=lookup.failure.code,
            status_code=lookup.failure.original_http_status,
            detail={"billing": lookup.billing.model_dump(mode="json")},
        )
    raise AppError(
        "Billing operation cannot be replayed.", code="BILLING_REPLAY_INVALID", status_code=500
    )


def _release_after_failure(
    db: Session,
    *,
    operation_id: str,
    result: object | None,
    code: str,
    message: str,
    cause: BaseException | None = None,
) -> None:
    usage = db.scalar(select(UsageRecord).where(UsageRecord.billing_operation_id == operation_id))
    if usage is not None and result is not None:
        provider_costs.attach_deepseek_usage(usage, result=result)
        db.flush([usage])
    operation = complete_failed(
        db, operation_id=operation_id, code=code, http_status=502, sanitized_detail=None
    )
    db.commit()
    error = AppError(
        message,
        code=code,
        status_code=502,
        detail={"billing": billing_summary(operation).model_dump(mode="json")},
    )
    if cause is None:
        raise error
    raise error from cause


@router.post("/estimate", response_model=ApiResponse[BillingQuote])
def estimate_script(
    request: Request,
    payload: ScriptGenerateRequest,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[BillingQuote]:
    return ok(
        request,
        issue_quote(
            tenant_id=user.tenant_id,
            user_id=user.id,
            request_hash=request_sha256(_normalized_request(payload)),
            draft=_pricing_draft(db, tenant_id=user.tenant_id),
        ),
    )


@router.post("/generate", response_model=ApiResponse[ScriptGenerateResponse])
def generate_script(
    request: Request,
    payload: ScriptGenerateRequest,
    headers: BillingSubmissionHeaders = BillingSubmissionHeadersDependency,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ScriptGenerateResponse]:
    request_hash = request_sha256(_normalized_request(payload))
    replay = find_replay(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation=_OPERATION,
        idempotency_key=headers.idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return ok(request, _replayed_response(db, user=user, headers=headers))
    verified_quote = verify_quote(
        token=headers.quote_token,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation=_OPERATION,
        request_hash=request_hash,
        current_draft=_pricing_draft(db, tenant_id=user.tenant_id),
    )
    if not (
        settings.engine_llm_api_key and settings.engine_llm_base_url and settings.engine_llm_model
    ):
        raise AppError("DeepSeek is not configured.", code="LLM_NOT_CONFIGURED", status_code=503)
    operation = create_reserved_operation(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation=_OPERATION,
        idempotency_key=headers.idempotency_key,
        request_hash=request_hash,
        verified_quote=verified_quote,
        usage_allocations=(
            UsageAllocation(
                item_index=0,
                pricing_line_index=0,
                quantity=Decimal("1"),
                credits=verified_quote.snapshot.pricing_lines[0].subtotal_credits,
                provider="deepseek",
                model=settings.engine_llm_model,
                video_task_id=None,
            ),
        ),
        result_type="script_generate_result",
    )
    db.commit()
    try:
        provider = resolve(db, tenant_id=user.tenant_id, capability="llm")
        result = asyncio.run(
            invoke(
                db,
                tenant_id=user.tenant_id,
                capability="llm",
                provider=provider.__class__.__name__,
                operation=lambda: provider.generate_text(
                    build_script_payload(
                        payload.topic.strip(),
                        video_mode=payload.video_mode,
                        duration_sec=payload.duration_sec,
                        length_tier=payload.length_tier,
                    )
                ),
                timeout_seconds=30.0,
            )
        )
    except (ProviderInvocationError, AppError) as exc:
        _release_after_failure(
            db,
            operation_id=operation.id,
            result=_failure_usage_from_error(exc),
            code="LLM_PROVIDER_FAILED",
            message="DeepSeek provider failed.",
            cause=exc,
        )
    if not isinstance(result, dict):
        _release_after_failure(
            db,
            operation_id=operation.id,
            result=None,
            code="LLM_EMPTY_RESULT",
            message="DeepSeek returned an empty script.",
        )
    script = clean_spoken_script(str(result.get("text") or ""))
    if not script:
        _release_after_failure(
            db,
            operation_id=operation.id,
            result=result,
            code="LLM_EMPTY_RESULT",
            message="DeepSeek returned an empty script.",
        )
    usage = db.scalar(select(UsageRecord).where(UsageRecord.billing_operation_id == operation.id))
    if usage is not None:
        provider_costs.attach_deepseek_usage(usage, result=result)
        db.flush([usage])
    operation = complete_succeeded(
        db,
        operation_id=operation.id,
        actual_quantities={0: Decimal("1")},
        result_type="script_generate_result",
        result_id=None,
        result_payload=ScriptGenerateStoredResult(script=script),
    )
    db.commit()
    return ok(request, ScriptGenerateResponse(script=script, billing=billing_summary(operation)))
