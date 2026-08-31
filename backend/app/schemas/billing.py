from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.services.pricing import RateScope, RateSource


class _BillingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BillingPricingLine(_BillingModel):
    operation: str
    capability: str
    unit: str
    quantity: str
    unit_credits: str
    subtotal_credits: str
    rate_scope: RateScope
    rate_source: RateSource
    rate_id: str | None
    effective_at: datetime | None
    policy_key: str | None
    policy_version: int | None
    label: str

    @model_validator(mode="after")
    def validate_provenance(self) -> BillingPricingLine:
        _validate_wire_provenance(
            self.rate_source,
            self.rate_id,
            self.effective_at,
            self.policy_key,
            self.policy_version,
        )
        return self


class BillingDisclosure(_BillingModel):
    key: str
    rendered_text: str
    copy_version: int
    unit: str
    rate_scope: RateScope
    rate_source: RateSource
    rate_id: str | None
    effective_at: datetime | None
    policy_key: str | None
    policy_version: int | None
    reference_unit_credits: str

    @model_validator(mode="after")
    def validate_provenance(self) -> BillingDisclosure:
        _validate_wire_provenance(
            self.rate_source,
            self.rate_id,
            self.effective_at,
            self.policy_key,
            self.policy_version,
        )
        return self


class BillingQuote(_BillingModel):
    pricing_contract: Literal["billing_quote"] = "billing_quote"
    operation: str
    pricing_shape: Literal["simple", "composite"]
    unit: str | None
    quantity: str | None
    unit_credits: str | None
    rate_scope: RateScope | None
    rate_source: RateSource | None
    subtotal_credits: str
    payable_credits: int
    breakdown: list[BillingPricingLine]
    disclosures: list[BillingDisclosure]
    quote_token: str
    expires_at: datetime

    @model_validator(mode="after")
    def validate_shape(self) -> BillingQuote:
        simple_fields = (
            self.unit,
            self.quantity,
            self.unit_credits,
            self.rate_scope,
            self.rate_source,
        )
        if self.pricing_shape == "simple" and (
            any(value is None for value in simple_fields) or self.breakdown
        ):
            raise ValueError("simple quote requires top-level pricing fields and empty breakdown")
        if self.pricing_shape == "composite" and (
            any(value is not None for value in simple_fields) or not self.breakdown
        ):
            raise ValueError(
                "composite quote requires null top-level pricing fields and non-empty breakdown"
            )
        return self


def _validate_wire_provenance(
    source: RateSource,
    rate_id: str | None,
    effective_at: datetime | None,
    policy_key: str | None,
    policy_version: int | None,
) -> None:
    if source in (RateSource.TENANT_RATE, RateSource.PLATFORM_RATE):
        if (
            rate_id is None
            or effective_at is None
            or policy_key is not None
            or policy_version is not None
        ):
            raise ValueError("database provenance requires row fields and null policy fields")
    elif (
        rate_id is not None
        or effective_at is not None
        or not policy_key
        or policy_version is None
        or policy_version < 1
    ):
        raise ValueError("policy provenance requires policy fields and null row fields")
