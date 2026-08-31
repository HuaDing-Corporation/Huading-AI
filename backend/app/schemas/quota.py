from pydantic import BaseModel


class QuotaResponse(BaseModel):
    has_active_subscription: bool
    active_subscription_id: str | None
    total: int
    used: int
    reserved: int
    remaining: int
    manual_fulfillment_held_credits: int
    pending_refund_credits: int
