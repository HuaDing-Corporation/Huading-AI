from fastapi import APIRouter, Request
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency
from app.db.models import User
from app.schemas.quota import QuotaResponse
from app.schemas.response import ApiResponse, ok
from app.services.quota import active_subscription, quota_payload

router = APIRouter()


@router.get("", response_model=ApiResponse[QuotaResponse])
def get_quota(
    request: Request,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[QuotaResponse]:
    subscription = active_subscription(db, user.tenant_id)
    return ok(request, QuotaResponse(**quota_payload(subscription)))
