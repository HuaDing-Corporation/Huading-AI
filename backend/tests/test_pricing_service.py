from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base, CreditRate, Tenant
from app.schemas.billing import BillingQuote
from app.services.pricing import (
    PRICING_POLICIES,
    PricingDisclosure,
    PricingInvariantError,
    PricingLine,
    RateScope,
    RateSource,
    ResolvedRate,
    activate_credit_rate,
    build_composite_pricing,
    build_simple_pricing,
    code_default_rate,
    fixed_policy_rate,
    resolve_rate,
    validate_credit_rate_candidate,
    validate_pricing_snapshot,
)


@pytest.fixture
def db() -> Session:
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-a", slug="tenant-a", name="Tenant A"))
        session.commit()
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.parametrize(
    ("operation", "unit", "price", "scope"),
    [
        ("script_generate", "call", Decimal("1.0000"), RateScope.TENANT_OVERRIDABLE),
        ("scene_prompt", "call", Decimal("30.0000"), RateScope.TENANT_OVERRIDABLE),
        ("ecom_cutout", "image", Decimal("80.0000"), RateScope.TENANT_OVERRIDABLE),
        ("ecom_model", "image", Decimal("80.0000"), RateScope.TENANT_OVERRIDABLE),
        (
            "doubao_brand_voice_order_create",
            "call",
            Decimal("30000.0000"),
            RateScope.PLATFORM_FIXED,
        ),
        (
            "doubao_brand_voice_order_renew",
            "call",
            Decimal("30000.0000"),
            RateScope.PLATFORM_FIXED,
        ),
        ("cosyvoice_brand_tts", "character", Decimal("0.1000"), RateScope.TENANT_OVERRIDABLE),
    ],
)
def test_policy_defaults(operation, unit, price, scope):
    policy = PRICING_POLICIES[operation]
    assert (policy.unit, policy.default_unit_credits, policy.scope) == (unit, price, scope)


def test_policy_registry_contains_every_authoritative_operation() -> None:
    assert set(PRICING_POLICIES) == {
        "script_generate",
        "scene_prompt",
        "ecom_cutout",
        "ecom_model",
        "doubao_brand_voice_order_create",
        "doubao_brand_voice_order_renew",
        "cosyvoice_brand_voice_create",
        "video_create",
        "cosyvoice_brand_tts",
    }


def test_simple_quote_has_one_canonical_line_even_when_display_breakdown_is_empty():
    draft = build_simple_pricing(
        policy=PRICING_POLICIES["scene_prompt"],
        rate=code_default_rate(PRICING_POLICIES["scene_prompt"]),
        quantity=Decimal("1"),
    )
    assert draft.breakdown == ()
    assert len(draft.pricing_lines) == 1
    assert draft.payable_credits == 30


def _make_line(subtotal: str = "0.4") -> PricingLine:
    rate = ResolvedRate(
        unit_credits=Decimal(subtotal),
        source=RateSource.CODE_DEFAULT,
        rate_id=None,
        effective_at=None,
        policy_key="test_line",
        policy_version=1,
    )
    return PricingLine(
        operation="cosyvoice_brand_tts",
        capability="tts",
        unit="character",
        quantity=Decimal("1"),
        unit_credits=Decimal(subtotal),
        subtotal_credits=Decimal(subtotal),
        rate_scope=RateScope.TENANT_OVERRIDABLE,
        rate=rate,
        label="TTS",
    )


def test_composite_rounds_once_after_aggregation():
    draft = build_composite_pricing(
        operation="video_create",
        lines=(_make_line("0.4"), _make_line("0.4"), _make_line("0.4")),
    )
    assert draft.subtotal_credits == Decimal("1.2")
    assert draft.payable_credits == 2


@pytest.mark.parametrize(
    "value",
    [Decimal("-1"), Decimal("0"), Decimal("NaN"), Decimal("Infinity"), Decimal("0.00001")],
)
def test_positive_policy_rejects_invalid_rates(value: Decimal) -> None:
    policy = PRICING_POLICIES["scene_prompt"]
    rate = replace(code_default_rate(policy), unit_credits=value)
    with pytest.raises(PricingInvariantError):
        build_simple_pricing(policy=policy, rate=rate, quantity=Decimal("1"))


@pytest.mark.parametrize(
    "value",
    [Decimal("-1"), Decimal("0"), Decimal("NaN"), Decimal("Infinity"), Decimal("1.0001")],
)
def test_positive_policy_rejects_invalid_quantities(value: Decimal) -> None:
    policy = PRICING_POLICIES["scene_prompt"]
    with pytest.raises(PricingInvariantError):
        build_simple_pricing(policy=policy, rate=code_default_rate(policy), quantity=value)


def test_composite_rejects_line_arithmetic_mismatch() -> None:
    with pytest.raises(PricingInvariantError, match="arithmetic"):
        build_composite_pricing(
            operation="video_create",
            lines=(replace(_make_line("0.4"), subtotal_credits=Decimal("0.5")),),
        )


def test_resolve_rate_excludes_future_rows(db: Session) -> None:
    now = datetime.now(UTC)
    db.add(
        CreditRate(
            tenant_id="tenant-a",
            capability="image",
            unit="image",
            credits_per_unit=Decimal("41.0000"),
            effective_at=now + timedelta(minutes=1),
        )
    )
    db.commit()

    rate = resolve_rate(
        db,
        tenant_id="tenant-a",
        policy=PRICING_POLICIES["ecom_cutout"],
        now=now,
    )

    assert rate.source is RateSource.CODE_DEFAULT
    assert rate.unit_credits == Decimal("80.0000")


def test_resolve_rate_rejects_duplicate_eligible_active_rows(db: Session) -> None:
    now = datetime.now(UTC)
    db.add_all(
        [
            CreditRate(
                id="duplicate-a",
                capability="image",
                unit="image",
                credits_per_unit=Decimal("80.0000"),
                effective_at=now - timedelta(minutes=2),
            ),
            CreditRate(
                id="duplicate-b",
                capability="image",
                unit="image",
                credits_per_unit=Decimal("80.0000"),
                effective_at=now - timedelta(minutes=1),
            ),
        ]
    )
    db.commit()

    with pytest.raises(PricingInvariantError, match="duplicate-a.*duplicate-b"):
        resolve_rate(
            db,
            tenant_id="tenant-a",
            policy=PRICING_POLICIES["ecom_cutout"],
            now=now,
        )


def test_tenant_rate_precedes_platform_rate(db: Session) -> None:
    now = datetime.now(UTC)
    db.add_all(
        [
            CreditRate(
                id="platform-image",
                capability="image",
                unit="image",
                credits_per_unit=Decimal("80.0000"),
                effective_at=now,
            ),
            CreditRate(
                id="tenant-image",
                tenant_id="tenant-a",
                capability="image",
                unit="image",
                credits_per_unit=Decimal("42.0000"),
                effective_at=now,
            ),
        ]
    )
    db.commit()

    rate = resolve_rate(
        db,
        tenant_id="tenant-a",
        policy=PRICING_POLICIES["ecom_model"],
        now=now,
    )

    assert (rate.source, rate.rate_id, rate.unit_credits) == (
        RateSource.TENANT_RATE,
        "tenant-image",
        Decimal("42.0000"),
    )


def test_platform_rate_is_used_when_tenant_rate_is_absent(db: Session) -> None:
    now = datetime.now(UTC)
    db.add(
        CreditRate(
            id="platform-image",
            capability="image",
            unit="image",
            credits_per_unit=Decimal("79.0000"),
            effective_at=now,
        )
    )
    db.commit()

    rate = resolve_rate(
        db,
        tenant_id="tenant-a",
        policy=PRICING_POLICIES["ecom_cutout"],
        now=now,
    )

    assert rate.source is RateSource.PLATFORM_RATE
    assert rate.rate_id == "platform-image"
    assert rate.effective_at is not None
    assert rate.policy_key is None
    assert rate.policy_version is None


def test_code_default_and_fixed_policy_have_complete_non_database_provenance() -> None:
    default_policy = PRICING_POLICIES["scene_prompt"]
    fixed_policy = PRICING_POLICIES["cosyvoice_brand_voice_create"]
    default = code_default_rate(default_policy)
    fixed = fixed_policy_rate(fixed_policy)

    assert (default.source, default.rate_id, default.effective_at) == (
        RateSource.CODE_DEFAULT,
        None,
        None,
    )
    assert (default.policy_key, default.policy_version) == (
        default_policy.policy_key,
        default_policy.policy_version,
    )
    assert (fixed.source, fixed.rate_id, fixed.effective_at) == (
        RateSource.FIXED_POLICY,
        None,
        None,
    )
    assert (fixed.policy_key, fixed.policy_version) == (
        fixed_policy.policy_key,
        fixed_policy.policy_version,
    )


@pytest.mark.parametrize(
    "operation",
    ["doubao_brand_voice_order_create", "doubao_brand_voice_order_renew"],
)
def test_doubao_policy_ignores_tenant_voice_clone_rate(db: Session, operation: str) -> None:
    now = datetime.now(UTC)
    db.add_all(
        [
            CreditRate(
                id="platform-doubao",
                capability="voice_clone",
                unit="call",
                credits_per_unit=Decimal("30000.0000"),
                effective_at=now,
            ),
            CreditRate(
                id="tenant-doubao-ignored",
                tenant_id="tenant-a",
                capability="voice_clone",
                unit="call",
                credits_per_unit=Decimal("42.0000"),
                effective_at=now,
            ),
        ]
    )
    db.commit()

    rate = resolve_rate(
        db,
        tenant_id="tenant-a",
        policy=PRICING_POLICIES[operation],
        now=now,
    )

    assert rate.source is RateSource.PLATFORM_RATE
    assert rate.rate_id == "platform-doubao"
    assert rate.unit_credits == Decimal("30000.0000")


def test_cosyvoice_creation_is_the_exact_fixed_zero_exception() -> None:
    policy = PRICING_POLICIES["cosyvoice_brand_voice_create"]
    draft = build_simple_pricing(
        policy=policy,
        rate=fixed_policy_rate(policy),
        quantity=Decimal("1"),
    )
    line = draft.pricing_lines[0]
    assert line.quantity == Decimal("1")
    assert line.unit_credits == Decimal("0.0000")
    assert line.subtotal_credits == Decimal("0.0000")
    assert draft.payable_credits == 0
    assert line.rate.source is RateSource.FIXED_POLICY


@pytest.mark.parametrize("is_active", [True, False])
def test_candidate_rejects_negative_nonfinite_and_overprecision(is_active: bool) -> None:
    for value in (Decimal("-0.0001"), Decimal("NaN"), Decimal("Infinity"), Decimal("1.00001")):
        with pytest.raises(PricingInvariantError):
            validate_credit_rate_candidate(
                tenant_id=None,
                capability="image",
                unit="image",
                credits_per_unit=value,
                is_active=is_active,
            )


def test_candidate_rejects_active_zero_for_positive_policy() -> None:
    with pytest.raises(PricingInvariantError):
        validate_credit_rate_candidate(
            tenant_id=None,
            capability="image",
            unit="image",
            credits_per_unit=Decimal("0"),
            is_active=True,
        )


def test_candidate_rejects_newly_active_tenant_voice_clone_but_allows_inactive_history() -> None:
    validate_credit_rate_candidate(
        tenant_id="tenant-a",
        capability="voice_clone",
        unit="call",
        credits_per_unit=Decimal("42"),
        is_active=False,
    )
    with pytest.raises(PricingInvariantError, match="tenant.*voice_clone"):
        validate_credit_rate_candidate(
            tenant_id="tenant-a",
            capability="voice_clone",
            unit="call",
            credits_per_unit=Decimal("42"),
            is_active=True,
        )


def test_activate_rate_deactivates_prior_row_in_same_transaction(db: Session) -> None:
    now = datetime.now(UTC)
    old = CreditRate(
        id="old-image",
        capability="image",
        unit="image",
        credits_per_unit=Decimal("80"),
        effective_at=now - timedelta(days=1),
    )
    selected = CreditRate(
        id="new-image",
        capability="image",
        unit="image",
        credits_per_unit=Decimal("81"),
        effective_at=now + timedelta(days=1),
        is_active=False,
    )
    db.add_all([old, selected])
    db.commit()

    activated = activate_credit_rate(db, rate_id=selected.id, activated_at=now)

    assert activated is selected
    assert selected.is_active is True
    assert selected.effective_at == now
    assert old.is_active is False


def test_snapshot_validation_rejects_aggregate_or_line_tampering() -> None:
    line = _make_line("0.4")
    snapshot = {
        "operation": "video_create",
        "pricing_shape": "composite",
        "pricing_lines": [
            {
                "operation": line.operation,
                "capability": line.capability,
                "unit": line.unit,
                "quantity": "1",
                "unit_credits": "0.4",
                "subtotal_credits": "0.4",
                "rate_scope": line.rate_scope.value,
                "rate_source": line.rate.source.value,
                "rate_id": None,
                "effective_at": None,
                "policy_key": "test_line",
                "policy_version": 1,
                "label": "TTS",
            }
        ],
        "disclosures": [],
        "subtotal_credits": "0.4",
        "payable_credits": 1,
        "rounding": "ROUND_CEILING",
    }
    assert validate_pricing_snapshot(snapshot).payable_credits == 1
    with pytest.raises(PricingInvariantError):
        validate_pricing_snapshot({**snapshot, "subtotal_credits": "0.5"})
    tampered_line = {**snapshot["pricing_lines"][0], "subtotal_credits": "0.5"}
    with pytest.raises(PricingInvariantError):
        validate_pricing_snapshot({**snapshot, "pricing_lines": [tampered_line]})


def test_billing_quote_enforces_simple_and_composite_wire_shapes() -> None:
    now = datetime.now(UTC)
    simple = BillingQuote(
        operation="scene_prompt",
        pricing_shape="simple",
        unit="call",
        quantity="1",
        unit_credits="30.0000",
        rate_scope=RateScope.TENANT_OVERRIDABLE,
        rate_source=RateSource.CODE_DEFAULT,
        subtotal_credits="30.0000",
        payable_credits=30,
        breakdown=[],
        disclosures=[],
        quote_token="token",
        expires_at=now,
    )
    assert simple.model_dump(mode="json")["unit_credits"] == "30.0000"
    with pytest.raises(ValidationError):
        BillingQuote(**{**simple.model_dump(), "pricing_shape": "composite"})


def test_disclosures_do_not_change_pricing_totals() -> None:
    policy = PRICING_POLICIES["scene_prompt"]
    disclosure = PricingDisclosure(
        key="informational",
        rendered_text="Reference price",
        copy_version=1,
        unit=policy.unit,
        rate_scope=policy.scope,
        rate=code_default_rate(policy),
    )
    draft = build_simple_pricing(
        policy=policy,
        rate=code_default_rate(policy),
        quantity=Decimal("1"),
        disclosures=(disclosure,),
    )
    assert draft.subtotal_credits == Decimal("30.0000")
    assert draft.payable_credits == 30
