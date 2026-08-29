from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency
from app.core.exceptions import AppError
from app.db.models import User
from app.schemas.response import ApiResponse, ok
from app.services.billing_operations import BillingOperationLookup, lookup_operation

router = APIRouter()


@router.get(
    "/operations/by-idempotency/{operation}/{idempotency_key}",
    response_model=ApiResponse[BillingOperationLookup],
)
def get_operation_by_idempotency(
    operation: str,
    idempotency_key: UUID,
    request: Request,
    db: Session = DbSessionDependency,
    user: User = CurrentUserDependency,
) -> ApiResponse[BillingOperationLookup]:
    lookup = lookup_operation(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation=operation,
        idempotency_key=idempotency_key,
    )
    if lookup is None:
        raise AppError(
            "Billing operation not found.",
            code="BILLING_OPERATION_NOT_FOUND",
            status_code=404,
        )
    return ok(request, lookup)
