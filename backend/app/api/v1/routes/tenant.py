from fastapi import APIRouter, Request
from sqlalchemy.orm import Session

from app.api.deps import CurrentUserDependency, DbSessionDependency
from app.db.models import User
from app.schemas.response import ApiResponse, ok
from app.schemas.tenant import TenantLabelSettingsRead, TenantLabelSettingsUpdate
from app.services.synthetic_label import label_settings_for_tenant, upsert_label_settings

router = APIRouter()


@router.get(
    "/label-settings",
    response_model=ApiResponse[TenantLabelSettingsRead],
)
def get_label_settings(
    request: Request,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[TenantLabelSettingsRead]:
    label_settings = label_settings_for_tenant(db, tenant_id=user.tenant_id)
    return ok(request, TenantLabelSettingsRead(**label_settings.__dict__))


@router.put(
    "/label-settings",
    response_model=ApiResponse[TenantLabelSettingsRead],
)
def update_label_settings(
    request: Request,
    payload: TenantLabelSettingsUpdate,
    user: User = CurrentUserDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[TenantLabelSettingsRead]:
    label_settings = upsert_label_settings(
        db,
        tenant_id=user.tenant_id,
        position=payload.position,
        text=payload.text,
    )
    db.commit()
    return ok(request, TenantLabelSettingsRead(**label_settings.__dict__))
