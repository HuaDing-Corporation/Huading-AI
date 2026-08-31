from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models import BrandVoiceOrder


def _held_error() -> AppError:
    return AppError(
        "The resource is held by an awaiting manual brand voice order.",
        code="BRAND_VOICE_ORDER_NOT_CANCELLABLE",
        status_code=409,
    )


def assert_asset_not_held_by_manual_order(db: Session, *, asset_id: str) -> None:
    if (
        db.scalar(
            select(BrandVoiceOrder.id).where(
                BrandVoiceOrder.source_audio_asset_id == asset_id,
                BrandVoiceOrder.status == "awaiting_fulfillment",
            )
        )
        is not None
    ):
        raise _held_error()


def assert_brand_voice_not_held_by_manual_order(db: Session, *, brand_voice_id: str) -> None:
    if (
        db.scalar(
            select(BrandVoiceOrder.id).where(
                BrandVoiceOrder.existing_brand_voice_id == brand_voice_id,
                BrandVoiceOrder.status == "awaiting_fulfillment",
            )
        )
        is not None
    ):
        raise _held_error()


def assert_no_awaiting_orders_for_principal(
    db: Session, *, tenant_id: str, user_id: str | None = None
) -> None:
    statement = select(BrandVoiceOrder.id).where(
        BrandVoiceOrder.tenant_id == tenant_id,
        BrandVoiceOrder.status == "awaiting_fulfillment",
    )
    if user_id is not None:
        statement = statement.where(BrandVoiceOrder.user_id == user_id)
    if db.scalar(statement) is not None:
        raise _held_error()
