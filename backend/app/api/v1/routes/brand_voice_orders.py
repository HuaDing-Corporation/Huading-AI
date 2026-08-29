from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import (
    BillingSubmissionHeaders,
    DbSessionDependency,
    require_billing_submission_headers,
    require_permission,
)
from app.db.models import User
from app.schemas.billing import BillingQuote
from app.schemas.brand_voice_orders import (
    BrandVoiceOrderCreateRequest,
    BrandVoiceOrderPage,
    BrandVoiceOrderRead,
)
from app.schemas.response import ApiResponse, ok
from app.services import brand_voice_orders
from app.services.billing_operations import find_replay
from app.services.billing_quotes import issue_quote, verify_quote

router = APIRouter()
CreateOrderPermission = Depends(require_permission("video:create"))
BillingHeaders = Depends(require_billing_submission_headers)


@router.post("/estimate", response_model=ApiResponse[BillingQuote])
def estimate_brand_voice_order(
    request: Request,
    payload: BrandVoiceOrderCreateRequest,
    user: User = CreateOrderPermission,
    db: Session = DbSessionDependency,
) -> ApiResponse[BillingQuote]:
    brand_voice_orders.validate_brand_voice_order_materials(
        db, user=user, payload=payload, for_update=False
    )
    return ok(
        request,
        issue_quote(
            tenant_id=user.tenant_id,
            user_id=user.id,
            request_hash=brand_voice_orders.brand_voice_order_request_hash(payload),
            draft=brand_voice_orders.brand_voice_order_pricing_draft(
                db, tenant_id=user.tenant_id, payload=payload
            ),
        ),
    )


@router.post(
    "",
    response_model=ApiResponse[BrandVoiceOrderRead],
    status_code=status.HTTP_201_CREATED,
)
def submit_brand_voice_order(
    request: Request,
    payload: BrandVoiceOrderCreateRequest,
    user: User = CreateOrderPermission,
    db: Session = DbSessionDependency,
    headers: BillingSubmissionHeaders = BillingHeaders,
) -> ApiResponse[BrandVoiceOrderRead]:
    operation_name = brand_voice_orders.brand_voice_order_operation(payload)
    request_hash = brand_voice_orders.brand_voice_order_request_hash(payload)
    replay = find_replay(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        operation=operation_name,
        idempotency_key=headers.idempotency_key,
        request_hash=request_hash,
    )
    if replay is None:
        brand_voice_orders.validate_brand_voice_order_materials(
            db, user=user, payload=payload, for_update=False
        )
        verified_quote = verify_quote(
            token=headers.quote_token,
            tenant_id=user.tenant_id,
            user_id=user.id,
            operation=operation_name,
            request_hash=request_hash,
            current_draft=brand_voice_orders.brand_voice_order_pricing_draft(
                db, tenant_id=user.tenant_id, payload=payload
            ),
        )
    else:
        # Replay uses the immutable stored operation and does not revalidate an expired quote.
        verified_quote = None
    try:
        if replay is None:
            order = brand_voice_orders.create_brand_voice_order(
                db,
                user=user,
                payload=payload,
                verified_quote=verified_quote,
                submission_headers=headers,
            )
        else:
            order = brand_voice_orders.get_user_brand_voice_order(
                db,
                tenant_id=user.tenant_id,
                user_id=user.id,
                order_id=str(replay.operation.result_id),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(request, brand_voice_orders.brand_voice_order_read(db, order=order))


@router.get("", response_model=ApiResponse[BrandVoiceOrderPage])
def list_brand_voice_orders(
    request: Request,
    user: User = CreateOrderPermission,
    db: Session = DbSessionDependency,
) -> ApiResponse[BrandVoiceOrderPage]:
    orders = brand_voice_orders.list_user_brand_voice_orders(
        db, tenant_id=user.tenant_id, user_id=user.id
    )
    return ok(
        request,
        BrandVoiceOrderPage(
            items=[brand_voice_orders.brand_voice_order_read(db, order=order) for order in orders],
            total=len(orders),
        ),
    )


@router.get("/{order_id}", response_model=ApiResponse[BrandVoiceOrderRead])
def get_brand_voice_order(
    request: Request,
    order_id: str,
    user: User = CreateOrderPermission,
    db: Session = DbSessionDependency,
) -> ApiResponse[BrandVoiceOrderRead]:
    return ok(
        request,
        brand_voice_orders.user_brand_voice_order_read(
            db,
            tenant_id=user.tenant_id,
            user_id=user.id,
            order_id=order_id,
        ),
    )
