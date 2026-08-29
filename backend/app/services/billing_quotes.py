from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

import jwt

from app.core.config import settings
from app.core.exceptions import AppError
from app.schemas.billing import BillingDisclosure, BillingPricingLine, BillingQuote
from app.services.pricing import (
    PricingDraft,
    PricingInvariantError,
    PricingLine,
    PricingSnapshot,
    validate_pricing_snapshot,
)

QUOTE_TOKEN_TTL = timedelta(minutes=10)
QUOTE_TOKEN_MAX_BYTES = 4096
QUOTE_TOKEN_CONTEXT = b"huading-billing-quote-v1"
QUOTE_TOKEN_AUDIENCE = "huading-billing-quote"
QUOTE_TOKEN_TYPE = "billing_quote"
QUOTE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class VerifiedQuote:
    snapshot: PricingSnapshot
    quote_hash: str
    pricing_payload_hash: str


def quote_signing_key(jwt_secret_key: str) -> bytes:
    return hmac.new(
        jwt_secret_key.encode("utf-8"),
        QUOTE_TOKEN_CONTEXT,
        hashlib.sha256,
    ).digest()


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _datetime_string(value: datetime) -> str:
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _canonical_value(value: object) -> object:
    if isinstance(value, Decimal):
        return _decimal_string(value)
    if isinstance(value, datetime):
        return _datetime_string(value)
    if isinstance(value, (UUID, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical_value(asdict(value))
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    return value


def canonical_json(value: object) -> bytes:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def request_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _rate_payload(line: PricingLine) -> dict[str, object]:
    return {
        "rate_source": line.rate.source.value,
        "rate_id": line.rate.rate_id,
        "effective_at": line.rate.effective_at,
        "policy_key": line.rate.policy_key,
        "policy_version": line.rate.policy_version,
    }


def _line_payload(line: PricingLine) -> dict[str, object]:
    return {
        "operation": line.operation,
        "capability": line.capability,
        "unit": line.unit,
        "quantity": line.quantity,
        "unit_credits": line.unit_credits,
        "subtotal_credits": line.subtotal_credits,
        "rate_scope": line.rate_scope.value,
        **_rate_payload(line),
        "label": line.label,
    }


def _disclosure_payload(draft: PricingDraft) -> list[dict[str, object]]:
    return [
        {
            "key": disclosure.key,
            "rendered_text": disclosure.rendered_text,
            "copy_version": disclosure.copy_version,
            "unit": disclosure.unit,
            "rate_scope": disclosure.rate_scope.value,
            "rate_source": disclosure.rate.source.value,
            "rate_id": disclosure.rate.rate_id,
            "effective_at": disclosure.rate.effective_at,
            "policy_key": disclosure.rate.policy_key,
            "policy_version": disclosure.rate.policy_version,
            "reference_unit_credits": disclosure.rate.unit_credits,
        }
        for disclosure in draft.disclosures
    ]


def _snapshot_payload(draft: PricingDraft) -> dict[str, object]:
    return {
        "operation": draft.operation,
        "pricing_shape": draft.pricing_shape,
        "pricing_lines": [_line_payload(line) for line in draft.pricing_lines],
        "disclosures": _disclosure_payload(draft),
        "subtotal_credits": draft.subtotal_credits,
        "payable_credits": draft.payable_credits,
        "rounding": "ROUND_CEILING",
    }


def _validated_snapshot(draft: PricingDraft) -> tuple[dict[str, object], PricingSnapshot]:
    if (
        (draft.pricing_shape == "simple" and draft.breakdown)
        or (draft.pricing_shape == "composite" and draft.breakdown != draft.pricing_lines)
    ):
        raise AppError(
            "Authoritative pricing is invalid.",
            code="QUOTE_PRICING_INVALID",
            status_code=500,
        )
    payload = _snapshot_payload(draft)
    try:
        return payload, validate_pricing_snapshot(payload)
    except PricingInvariantError as exc:
        raise AppError(
            "Authoritative pricing is invalid.",
            code="QUOTE_PRICING_INVALID",
            status_code=500,
        ) from exc


def pricing_payload_sha256(draft: PricingDraft) -> str:
    payload, _ = _validated_snapshot(draft)
    return request_sha256(payload)


def _billing_line(line: PricingLine) -> BillingPricingLine:
    return BillingPricingLine(
        operation=line.operation,
        capability=line.capability,
        unit=line.unit,
        quantity=str(line.quantity),
        unit_credits=str(line.unit_credits),
        subtotal_credits=str(line.subtotal_credits),
        rate_scope=line.rate_scope,
        rate_source=line.rate.source,
        rate_id=line.rate.rate_id,
        effective_at=line.rate.effective_at,
        policy_key=line.rate.policy_key,
        policy_version=line.rate.policy_version,
        label=line.label,
    )


def _billing_disclosure(draft: PricingDraft) -> list[BillingDisclosure]:
    return [
        BillingDisclosure(
            key=disclosure.key,
            rendered_text=disclosure.rendered_text,
            copy_version=disclosure.copy_version,
            unit=disclosure.unit,
            rate_scope=disclosure.rate_scope,
            rate_source=disclosure.rate.source,
            rate_id=disclosure.rate.rate_id,
            effective_at=disclosure.rate.effective_at,
            policy_key=disclosure.rate.policy_key,
            policy_version=disclosure.rate.policy_version,
            reference_unit_credits=str(disclosure.rate.unit_credits),
        )
        for disclosure in draft.disclosures
    ]


def _validate_request_hash(request_hash: str) -> None:
    if len(request_hash) != 64 or any(
        character not in "0123456789abcdef" for character in request_hash
    ):
        raise ValueError("request_hash must be a lowercase SHA-256 digest")


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _issued_at(now: datetime | None) -> datetime:
    return _as_utc(now or datetime.now(UTC)).replace(microsecond=0)


def issue_quote(
    *,
    tenant_id: str,
    user_id: str,
    request_hash: str,
    draft: PricingDraft,
    now: datetime | None = None,
) -> BillingQuote:
    _validate_request_hash(request_hash)
    _, snapshot = _validated_snapshot(draft)
    pricing_hash = pricing_payload_sha256(draft)
    issued_at = _issued_at(now)
    expires_at = issued_at + QUOTE_TOKEN_TTL
    claims: dict[str, Any] = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "operation": snapshot.operation,
        "request_sha256": request_hash,
        "pricing_payload_sha256": pricing_hash,
        "subtotal_credits": _decimal_string(snapshot.subtotal_credits),
        "payable_credits": snapshot.payable_credits,
        "iat": issued_at,
        "exp": expires_at,
        "aud": QUOTE_TOKEN_AUDIENCE,
        "typ": QUOTE_TOKEN_TYPE,
        "schema": QUOTE_SCHEMA_VERSION,
    }
    token = jwt.encode(
        claims,
        quote_signing_key(settings.jwt_secret_key),
        algorithm=settings.quote_token_algorithm,
        headers={"typ": QUOTE_TOKEN_TYPE},
    )
    encoded_token = token.encode("ascii")
    if len(encoded_token) > QUOTE_TOKEN_MAX_BYTES:  # pragma: no cover - bounded claims
        raise AppError(
            "Quote token exceeds the header limit.",
            code="QUOTE_TOKEN_TOO_LARGE",
            status_code=500,
        )

    if draft.pricing_shape == "simple":
        line = draft.pricing_lines[0]
        unit = line.unit
        quantity = str(line.quantity)
        unit_credits = str(line.unit_credits)
        rate_scope = line.rate_scope
        rate_source = line.rate.source
        breakdown: list[BillingPricingLine] = []
    else:
        unit = quantity = unit_credits = rate_scope = rate_source = None
        breakdown = [_billing_line(line) for line in draft.breakdown]
    return BillingQuote(
        operation=snapshot.operation,
        pricing_shape=snapshot.pricing_shape,
        unit=unit,
        quantity=quantity,
        unit_credits=unit_credits,
        rate_scope=rate_scope,
        rate_source=rate_source,
        subtotal_credits=str(snapshot.subtotal_credits),
        payable_credits=snapshot.payable_credits,
        breakdown=breakdown,
        disclosures=_billing_disclosure(draft),
        quote_token=token,
        expires_at=expires_at,
    )


def _invalid_quote() -> AppError:
    return AppError(
        "报价凭证格式无效",
        code="QUOTE_TOKEN_INVALID",
        status_code=422,
    )


def _decode_quote(token: str) -> dict[str, object]:
    try:
        encoded = token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise _invalid_quote() from exc
    if len(encoded) > QUOTE_TOKEN_MAX_BYTES:
        raise AppError("报价凭证过长", code="QUOTE_TOKEN_TOO_LARGE", status_code=422)
    try:
        header = jwt.get_unverified_header(token)
        if (
            header.get("alg") != settings.quote_token_algorithm
            or header.get("typ") != QUOTE_TOKEN_TYPE
        ):
            raise _invalid_quote()
        claims = jwt.decode(
            token,
            quote_signing_key(settings.jwt_secret_key),
            algorithms=[settings.quote_token_algorithm],
            audience=QUOTE_TOKEN_AUDIENCE,
            options={
                "verify_exp": False,
                "verify_iat": False,
                "verify_aud": False,
                "require": [
                    "tenant_id",
                    "user_id",
                    "operation",
                    "request_sha256",
                    "pricing_payload_sha256",
                    "subtotal_credits",
                    "payable_credits",
                    "iat",
                    "exp",
                    "aud",
                    "typ",
                    "schema",
                ],
            },
        )
    except AppError:
        raise
    except jwt.PyJWTError as exc:
        raise _invalid_quote() from exc
    if not isinstance(claims, dict):  # pragma: no cover - PyJWT guarantees dict
        raise _invalid_quote()
    return claims


def _claim_matches(claims: Mapping[str, object], key: str, expected: str | int) -> bool:
    return type(claims.get(key)) is type(expected) and claims.get(key) == expected


def _claim_time(claims: Mapping[str, object], key: str) -> datetime:
    value = claims.get(key)
    if type(value) is not int:
        raise _invalid_quote()
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise _invalid_quote() from exc


def verify_quote(
    *,
    token: str,
    tenant_id: str,
    user_id: str,
    operation: str,
    request_hash: str,
    current_draft: PricingDraft,
    now: datetime | None = None,
) -> VerifiedQuote:
    _validate_request_hash(request_hash)
    claims = _decode_quote(token)
    if (
        not _claim_matches(claims, "tenant_id", tenant_id)
        or not _claim_matches(claims, "user_id", user_id)
        or not _claim_matches(claims, "operation", operation)
        or not _claim_matches(claims, "aud", QUOTE_TOKEN_AUDIENCE)
        or not _claim_matches(claims, "typ", QUOTE_TOKEN_TYPE)
        or not _claim_matches(claims, "schema", QUOTE_SCHEMA_VERSION)
    ):
        raise _invalid_quote()
    verified_at = _as_utc(now or datetime.now(UTC))
    issued_at = _claim_time(claims, "iat")
    expires_at = _claim_time(claims, "exp")
    if issued_at > verified_at or expires_at - issued_at != QUOTE_TOKEN_TTL:
        raise _invalid_quote()
    if expires_at <= verified_at:
        raise AppError("报价凭证已过期", code="QUOTE_EXPIRED", status_code=422)
    if not _claim_matches(claims, "request_sha256", request_hash):
        raise AppError("报价或请求已变化", code="PRICE_CHANGED", status_code=422)

    _, snapshot = _validated_snapshot(current_draft)
    pricing_hash = pricing_payload_sha256(current_draft)
    if (
        snapshot.operation != operation
        or not _claim_matches(claims, "pricing_payload_sha256", pricing_hash)
        or not _claim_matches(
            claims,
            "subtotal_credits",
            _decimal_string(snapshot.subtotal_credits),
        )
        or not _claim_matches(claims, "payable_credits", snapshot.payable_credits)
    ):
        raise AppError("报价或请求已变化", code="PRICE_CHANGED", status_code=422)
    return VerifiedQuote(
        snapshot=snapshot,
        quote_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
        pricing_payload_hash=pricing_hash,
    )
