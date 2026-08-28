from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from enum import StrEnum
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CreditRate


class PricingInvariantError(ValueError):
    """Raised when authoritative pricing data cannot be proven internally consistent."""


class RateScope(StrEnum):
    TENANT_OVERRIDABLE = "tenant_overridable"
    PLATFORM_FIXED = "platform_fixed"


class RateSource(StrEnum):
    TENANT_RATE = "tenant_rate"
    PLATFORM_RATE = "platform_rate"
    CODE_DEFAULT = "code_default"
    FIXED_POLICY = "fixed_policy"


class ZeroRule(StrEnum):
    REQUIRE_POSITIVE = "require_positive"
    ALLOW_ZERO = "allow_zero"


@dataclass(frozen=True)
class PricingPolicy:
    operation: str
    capability: str
    unit: str
    default_unit_credits: Decimal
    scope: RateScope
    zero_rule: ZeroRule
    policy_key: str
    policy_version: int


@dataclass(frozen=True)
class ResolvedRate:
    unit_credits: Decimal
    source: RateSource
    rate_id: str | None
    effective_at: datetime | None
    policy_key: str | None
    policy_version: int | None


@dataclass(frozen=True)
class PricingLine:
    operation: str
    capability: str
    unit: str
    quantity: Decimal
    unit_credits: Decimal
    subtotal_credits: Decimal
    rate_scope: RateScope
    rate: ResolvedRate
    label: str


@dataclass(frozen=True)
class PricingDisclosure:
    key: str
    rendered_text: str
    copy_version: int
    unit: str
    rate_scope: RateScope
    rate: ResolvedRate


@dataclass(frozen=True)
class PricingDraft:
    operation: str
    pricing_shape: Literal["simple", "composite"]
    pricing_lines: tuple[PricingLine, ...]
    breakdown: tuple[PricingLine, ...]
    disclosures: tuple[PricingDisclosure, ...]
    subtotal_credits: Decimal
    payable_credits: int


@dataclass(frozen=True)
class PricingSnapshot:
    operation: str
    pricing_shape: Literal["simple", "composite"]
    pricing_lines: tuple[PricingLine, ...]
    disclosures: tuple[PricingDisclosure, ...]
    subtotal_credits: Decimal
    payable_credits: int
    rounding: Literal["ROUND_CEILING"] = "ROUND_CEILING"


def _policy(
    operation: str,
    capability: str,
    unit: str,
    default: str,
    scope: RateScope,
    zero_rule: ZeroRule = ZeroRule.REQUIRE_POSITIVE,
) -> PricingPolicy:
    return PricingPolicy(
        operation=operation,
        capability=capability,
        unit=unit,
        default_unit_credits=Decimal(default),
        scope=scope,
        zero_rule=zero_rule,
        policy_key=operation,
        policy_version=1,
    )


PRICING_POLICIES: dict[str, PricingPolicy] = {
    "script_generate": _policy(
        "script_generate",
        "script_generate",
        "call",
        "1.0000",
        RateScope.TENANT_OVERRIDABLE,
    ),
    "scene_prompt": _policy(
        "scene_prompt",
        "scene_prompt",
        "call",
        "30.0000",
        RateScope.TENANT_OVERRIDABLE,
    ),
    "ecom_cutout": _policy(
        "ecom_cutout", "image", "image", "80.0000", RateScope.TENANT_OVERRIDABLE
    ),
    "ecom_model": _policy(
        "ecom_model", "image", "image", "80.0000", RateScope.TENANT_OVERRIDABLE
    ),
    "doubao_brand_voice_order_create": _policy(
        "doubao_brand_voice_order_create",
        "voice_clone",
        "call",
        "30000.0000",
        RateScope.PLATFORM_FIXED,
    ),
    "doubao_brand_voice_order_renew": _policy(
        "doubao_brand_voice_order_renew",
        "voice_clone",
        "call",
        "30000.0000",
        RateScope.PLATFORM_FIXED,
    ),
    "cosyvoice_brand_voice_create": _policy(
        "cosyvoice_brand_voice_create",
        "voice_clone",
        "call",
        "0.0000",
        RateScope.PLATFORM_FIXED,
        ZeroRule.ALLOW_ZERO,
    ),
    "video_create": _policy(
        "video_create", "video", "second", "100.0000", RateScope.TENANT_OVERRIDABLE
    ),
    "cosyvoice_brand_tts": _policy(
        "cosyvoice_brand_tts",
        "tts",
        "character",
        "0.1000",
        RateScope.TENANT_OVERRIDABLE,
    ),
}


# Existing quota paths use these same authoritative fallbacks until they are
# converted to operation-specific pricing drafts in their closure tasks.
DEFAULT_RATE_CREDITS: dict[tuple[str, str], Decimal] = {
    ("avatar", "second"): Decimal("180.0000"),
    ("video", "second"): PRICING_POLICIES["video_create"].default_unit_credits,
    ("video_gen", "second"): Decimal("100.0000"),
    ("image", "image"): PRICING_POLICIES["ecom_cutout"].default_unit_credits,
    ("reverse_prompt", "call"): Decimal("100.0000"),
    ("script_generate", "call"): PRICING_POLICIES["script_generate"].default_unit_credits,
    ("scene_prompt", "call"): PRICING_POLICIES["scene_prompt"].default_unit_credits,
    ("voice_clone", "call"): PRICING_POLICIES[
        "doubao_brand_voice_order_create"
    ].default_unit_credits,
    ("tts", "character"): PRICING_POLICIES["cosyvoice_brand_tts"].default_unit_credits,
}


_RATE_SCALE = 4
_QUANTITY_SCALE = 3
_SUBTOTAL_SCALE = 6
_DATABASE_SOURCES = {RateSource.TENANT_RATE, RateSource.PLATFORM_RATE}
_POLICY_SOURCES = {RateSource.CODE_DEFAULT, RateSource.FIXED_POLICY}


def _decimal(
    value: object,
    *,
    field: str,
    scale: int,
    allow_zero: bool,
) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise PricingInvariantError(f"{field} must be a Decimal-compatible non-float value")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (str, int)):
        try:
            result = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise PricingInvariantError(f"{field} is not a valid Decimal") from exc
    else:
        raise PricingInvariantError(f"{field} must be a Decimal-compatible value")
    if not result.is_finite():
        raise PricingInvariantError(f"{field} must be finite")
    if result < 0 or (not allow_zero and result == 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise PricingInvariantError(f"{field} must be {qualifier}")
    fractional_digits = max(0, -result.as_tuple().exponent)
    if fractional_digits > scale:
        raise PricingInvariantError(f"{field} exceeds {scale} decimal places")
    return result


def _validate_rate_provenance(rate: ResolvedRate) -> None:
    if rate.source in _DATABASE_SOURCES:
        if rate.rate_id is None or rate.effective_at is None:
            raise PricingInvariantError("database rate provenance requires rate_id/effective_at")
        if rate.policy_key is not None or rate.policy_version is not None:
            raise PricingInvariantError("database rate provenance cannot contain policy fields")
    elif rate.source in _POLICY_SOURCES:
        if rate.rate_id is not None or rate.effective_at is not None:
            raise PricingInvariantError("policy rate provenance cannot contain database fields")
        if not rate.policy_key or rate.policy_version is None or rate.policy_version < 1:
            raise PricingInvariantError("policy rate provenance requires policy_key/version")
    else:  # pragma: no cover - StrEnum construction prevents normal entry
        raise PricingInvariantError("unknown rate source")


def _validate_rate(rate: ResolvedRate, *, zero_rule: ZeroRule) -> Decimal:
    _validate_rate_provenance(rate)
    return _decimal(
        rate.unit_credits,
        field="unit_credits",
        scale=_RATE_SCALE,
        allow_zero=zero_rule is ZeroRule.ALLOW_ZERO,
    )


def code_default_rate(policy: PricingPolicy) -> ResolvedRate:
    if policy.zero_rule is ZeroRule.ALLOW_ZERO:
        raise PricingInvariantError("zero-price policy must use fixed_policy provenance")
    rate = ResolvedRate(
        unit_credits=policy.default_unit_credits,
        source=RateSource.CODE_DEFAULT,
        rate_id=None,
        effective_at=None,
        policy_key=policy.policy_key,
        policy_version=policy.policy_version,
    )
    _validate_rate(rate, zero_rule=policy.zero_rule)
    return rate


def fixed_policy_rate(policy: PricingPolicy) -> ResolvedRate:
    rate = ResolvedRate(
        unit_credits=policy.default_unit_credits,
        source=RateSource.FIXED_POLICY,
        rate_id=None,
        effective_at=None,
        policy_key=policy.policy_key,
        policy_version=policy.policy_version,
    )
    _validate_rate(rate, zero_rule=policy.zero_rule)
    return rate


def _eligible_rates(
    db: Session,
    *,
    policy: PricingPolicy,
    tenant_id: str | None,
    now: datetime,
) -> list[CreditRate]:
    tenant_clause = (
        CreditRate.tenant_id.is_(None)
        if tenant_id is None
        else CreditRate.tenant_id == tenant_id
    )
    return list(
        db.scalars(
            select(CreditRate)
            .where(
                tenant_clause,
                CreditRate.capability == policy.capability,
                CreditRate.unit == policy.unit,
                CreditRate.is_active.is_(True),
                CreditRate.effective_at <= now,
            )
            .order_by(CreditRate.id)
        )
    )


def _resolved_database_rate(
    row: CreditRate,
    *,
    source: RateSource,
    policy: PricingPolicy,
) -> ResolvedRate:
    rate = ResolvedRate(
        unit_credits=Decimal(row.credits_per_unit),
        source=source,
        rate_id=row.id,
        effective_at=row.effective_at,
        policy_key=None,
        policy_version=None,
    )
    _validate_rate(rate, zero_rule=policy.zero_rule)
    return rate


def _single_rate(
    rows: list[CreditRate],
    *,
    policy: PricingPolicy,
    source: RateSource,
) -> ResolvedRate | None:
    if len(rows) > 1:
        ids = ", ".join(row.id for row in rows)
        raise PricingInvariantError(
            f"duplicate active rates for {policy.capability}/{policy.unit}: {ids}"
        )
    if not rows:
        return None
    return _resolved_database_rate(rows[0], source=source, policy=policy)


def resolve_rate(
    db: Session,
    *,
    tenant_id: str,
    policy: PricingPolicy,
    now: datetime | None = None,
) -> ResolvedRate:
    effective_now = now or datetime.now(UTC)
    if policy.zero_rule is ZeroRule.ALLOW_ZERO:
        return fixed_policy_rate(policy)
    if policy.scope is RateScope.TENANT_OVERRIDABLE:
        tenant_rate = _single_rate(
            _eligible_rates(db, policy=policy, tenant_id=tenant_id, now=effective_now),
            policy=policy,
            source=RateSource.TENANT_RATE,
        )
        if tenant_rate is not None:
            return tenant_rate
    platform_rate = _single_rate(
        _eligible_rates(db, policy=policy, tenant_id=None, now=effective_now),
        policy=policy,
        source=RateSource.PLATFORM_RATE,
    )
    return platform_rate if platform_rate is not None else code_default_rate(policy)


def _validate_line(line: PricingLine) -> None:
    policy = PRICING_POLICIES.get(line.operation)
    zero_rule = policy.zero_rule if policy is not None else ZeroRule.REQUIRE_POSITIVE
    unit_credits = _validate_rate(line.rate, zero_rule=zero_rule)
    if line.rate.unit_credits != line.unit_credits or unit_credits != line.unit_credits:
        raise PricingInvariantError("line unit_credits must equal provenance rate")
    quantity = _decimal(
        line.quantity,
        field="quantity",
        scale=_QUANTITY_SCALE,
        allow_zero=False,
    )
    subtotal = _decimal(
        line.subtotal_credits,
        field="subtotal_credits",
        scale=_SUBTOTAL_SCALE,
        allow_zero=zero_rule is ZeroRule.ALLOW_ZERO,
    )
    if unit_credits * quantity != subtotal:
        raise PricingInvariantError("line arithmetic mismatch")
    if policy is not None:
        if (line.capability, line.unit, line.rate_scope) != (
            policy.capability,
            policy.unit,
            policy.scope,
        ):
            raise PricingInvariantError("line does not match its pricing policy")
        if zero_rule is ZeroRule.ALLOW_ZERO and (
            quantity != Decimal("1")
            or unit_credits != Decimal("0")
            or line.rate.source is not RateSource.FIXED_POLICY
        ):
            raise PricingInvariantError("fixed zero policy has an invalid line")


def _validate_disclosure(disclosure: PricingDisclosure) -> None:
    if not disclosure.key or not disclosure.rendered_text or disclosure.copy_version < 1:
        raise PricingInvariantError("disclosure metadata is invalid")
    _validate_rate_provenance(disclosure.rate)
    _decimal(
        disclosure.rate.unit_credits,
        field="reference_unit_credits",
        scale=_RATE_SCALE,
        allow_zero=True,
    )


def build_simple_pricing(
    *,
    policy: PricingPolicy,
    rate: ResolvedRate,
    quantity: Decimal,
    disclosures: tuple[PricingDisclosure, ...] = (),
) -> PricingDraft:
    unit_credits = _validate_rate(rate, zero_rule=policy.zero_rule)
    validated_quantity = _decimal(
        quantity,
        field="quantity",
        scale=_QUANTITY_SCALE,
        allow_zero=False,
    )
    subtotal = unit_credits * validated_quantity
    line = PricingLine(
        operation=policy.operation,
        capability=policy.capability,
        unit=policy.unit,
        quantity=validated_quantity,
        unit_credits=unit_credits,
        subtotal_credits=subtotal,
        rate_scope=policy.scope,
        rate=rate,
        label=policy.operation,
    )
    _validate_line(line)
    for disclosure in disclosures:
        _validate_disclosure(disclosure)
    return PricingDraft(
        operation=policy.operation,
        pricing_shape="simple",
        pricing_lines=(line,),
        breakdown=(),
        disclosures=disclosures,
        subtotal_credits=subtotal,
        payable_credits=int(subtotal.to_integral_value(rounding=ROUND_CEILING)),
    )


def build_composite_pricing(
    *,
    operation: str,
    lines: Sequence[PricingLine],
    disclosures: tuple[PricingDisclosure, ...] = (),
) -> PricingDraft:
    pricing_lines = tuple(lines)
    if not pricing_lines:
        raise PricingInvariantError("composite pricing requires at least one line")
    subtotal = Decimal("0")
    for line in pricing_lines:
        _validate_line(line)
        subtotal += line.subtotal_credits
    for disclosure in disclosures:
        _validate_disclosure(disclosure)
    _decimal(
        subtotal,
        field="subtotal_credits",
        scale=_SUBTOTAL_SCALE,
        allow_zero=False,
    )
    return PricingDraft(
        operation=operation,
        pricing_shape="composite",
        pricing_lines=pricing_lines,
        breakdown=pricing_lines,
        disclosures=disclosures,
        subtotal_credits=subtotal,
        payable_credits=int(subtotal.to_integral_value(rounding=ROUND_CEILING)),
    )


def _required(mapping: Mapping[str, object], key: str) -> object:
    if key not in mapping:
        raise PricingInvariantError(f"pricing snapshot is missing {key}")
    return mapping[key]


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PricingInvariantError(f"{field} must be an object")
    return value


def _sequence(value: object, *, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PricingInvariantError(f"{field} must be an array")
    return value


def _parse_rate(mapping: Mapping[str, object]) -> ResolvedRate:
    try:
        source = RateSource(str(_required(mapping, "rate_source")))
    except ValueError as exc:
        raise PricingInvariantError("invalid rate_source") from exc
    effective_value = mapping.get("effective_at")
    if effective_value is None:
        effective_at = None
    elif isinstance(effective_value, datetime):
        effective_at = effective_value
    elif isinstance(effective_value, str):
        try:
            effective_at = datetime.fromisoformat(effective_value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PricingInvariantError("invalid effective_at") from exc
    else:
        raise PricingInvariantError("invalid effective_at")
    version_value = mapping.get("policy_version")
    if version_value is not None and (
        isinstance(version_value, bool) or not isinstance(version_value, int)
    ):
        raise PricingInvariantError("invalid policy_version")
    rate = ResolvedRate(
        unit_credits=_decimal(
            _required(mapping, "unit_credits"),
            field="unit_credits",
            scale=_RATE_SCALE,
            allow_zero=True,
        ),
        source=source,
        rate_id=str(mapping["rate_id"]) if mapping.get("rate_id") is not None else None,
        effective_at=effective_at,
        policy_key=(
            str(mapping["policy_key"]) if mapping.get("policy_key") is not None else None
        ),
        policy_version=version_value,
    )
    _validate_rate_provenance(rate)
    return rate


def _parse_line(value: object) -> PricingLine:
    mapping = _mapping(value, field="pricing line")
    rate = _parse_rate(mapping)
    try:
        scope = RateScope(str(_required(mapping, "rate_scope")))
    except ValueError as exc:
        raise PricingInvariantError("invalid rate_scope") from exc
    line = PricingLine(
        operation=str(_required(mapping, "operation")),
        capability=str(_required(mapping, "capability")),
        unit=str(_required(mapping, "unit")),
        quantity=_decimal(
            _required(mapping, "quantity"),
            field="quantity",
            scale=_QUANTITY_SCALE,
            allow_zero=False,
        ),
        unit_credits=rate.unit_credits,
        subtotal_credits=_decimal(
            _required(mapping, "subtotal_credits"),
            field="subtotal_credits",
            scale=_SUBTOTAL_SCALE,
            allow_zero=True,
        ),
        rate_scope=scope,
        rate=rate,
        label=str(_required(mapping, "label")),
    )
    _validate_line(line)
    return line


def _parse_disclosure(value: object) -> PricingDisclosure:
    mapping = _mapping(value, field="disclosure")
    rate_mapping = dict(mapping)
    rate_mapping["unit_credits"] = _required(mapping, "reference_unit_credits")
    rate = _parse_rate(rate_mapping)
    try:
        scope = RateScope(str(_required(mapping, "rate_scope")))
    except ValueError as exc:
        raise PricingInvariantError("invalid disclosure rate_scope") from exc
    copy_version = _required(mapping, "copy_version")
    if isinstance(copy_version, bool) or not isinstance(copy_version, int):
        raise PricingInvariantError("invalid copy_version")
    disclosure = PricingDisclosure(
        key=str(_required(mapping, "key")),
        rendered_text=str(_required(mapping, "rendered_text")),
        copy_version=copy_version,
        unit=str(_required(mapping, "unit")),
        rate_scope=scope,
        rate=rate,
    )
    _validate_disclosure(disclosure)
    return disclosure


def validate_pricing_snapshot(snapshot: Mapping[str, object]) -> PricingSnapshot:
    operation = str(_required(snapshot, "operation"))
    shape_value = _required(snapshot, "pricing_shape")
    if shape_value not in ("simple", "composite"):
        raise PricingInvariantError("invalid pricing_shape")
    lines = tuple(
        _parse_line(value)
        for value in _sequence(_required(snapshot, "pricing_lines"), field="pricing_lines")
    )
    if not lines or (shape_value == "simple" and len(lines) != 1):
        raise PricingInvariantError("pricing_shape does not match canonical lines")
    disclosures = tuple(
        _parse_disclosure(value)
        for value in _sequence(snapshot.get("disclosures", ()), field="disclosures")
    )
    subtotal = _decimal(
        _required(snapshot, "subtotal_credits"),
        field="subtotal_credits",
        scale=_SUBTOTAL_SCALE,
        allow_zero=operation == "cosyvoice_brand_voice_create",
    )
    line_sum = sum((line.subtotal_credits for line in lines), Decimal("0"))
    if line_sum != subtotal:
        raise PricingInvariantError("snapshot aggregate arithmetic mismatch")
    payable = _required(snapshot, "payable_credits")
    if isinstance(payable, bool) or not isinstance(payable, int) or payable < 0:
        raise PricingInvariantError("invalid payable_credits")
    if payable != int(subtotal.to_integral_value(rounding=ROUND_CEILING)):
        raise PricingInvariantError("snapshot payable arithmetic mismatch")
    if snapshot.get("rounding") != "ROUND_CEILING":
        raise PricingInvariantError("unsupported pricing rounding")
    return PricingSnapshot(
        operation=operation,
        pricing_shape=shape_value,
        pricing_lines=lines,
        disclosures=disclosures,
        subtotal_credits=subtotal,
        payable_credits=payable,
    )


def validate_credit_rate_candidate(
    *,
    tenant_id: str | None,
    capability: str,
    unit: str,
    credits_per_unit: Decimal,
    is_active: bool,
) -> None:
    value = _decimal(
        credits_per_unit,
        field="credits_per_unit",
        scale=_RATE_SCALE,
        allow_zero=True,
    )
    if tenant_id is not None and capability == "voice_clone" and unit == "call" and is_active:
        raise PricingInvariantError("tenant voice_clone/call rates cannot be activated")
    positive_pairs = {
        (policy.capability, policy.unit)
        for policy in PRICING_POLICIES.values()
        if policy.zero_rule is ZeroRule.REQUIRE_POSITIVE
    } | {
        ("avatar", "second"),
        ("video_gen", "second"),
        ("reverse_prompt", "call"),
    }
    if is_active and value == 0 and (capability, unit) in positive_pairs:
        raise PricingInvariantError("active rate for a positive pricing policy must be positive")


def activate_credit_rate(
    db: Session,
    *,
    rate_id: str,
    activated_at: datetime,
) -> CreditRate:
    selected = db.scalar(
        select(CreditRate).where(CreditRate.id == rate_id).with_for_update()
    )
    if selected is None:
        raise PricingInvariantError(f"credit rate not found: {rate_id}")
    validate_credit_rate_candidate(
        tenant_id=selected.tenant_id,
        capability=selected.capability,
        unit=selected.unit,
        credits_per_unit=Decimal(selected.credits_per_unit),
        is_active=True,
    )
    tenant_clause = (
        CreditRate.tenant_id.is_(None)
        if selected.tenant_id is None
        else CreditRate.tenant_id == selected.tenant_id
    )
    prior_rows = list(
        db.scalars(
            select(CreditRate)
            .where(
                tenant_clause,
                CreditRate.capability == selected.capability,
                CreditRate.unit == selected.unit,
                CreditRate.is_active.is_(True),
                CreditRate.id != selected.id,
            )
            .order_by(CreditRate.id)
            .with_for_update()
        )
    )
    if len(prior_rows) > 1:
        ids = ", ".join(row.id for row in prior_rows)
        raise PricingInvariantError(f"duplicate active rates must be audited first: {ids}")
    for prior in prior_rows:
        prior.is_active = False
    if prior_rows:
        db.flush(prior_rows)
    selected.effective_at = activated_at
    selected.is_active = True
    db.flush([selected])
    return selected
