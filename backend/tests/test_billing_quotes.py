from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import (
    BillingSubmissionHeaders,
    optional_billing_submission_headers,
    require_billing_submission_headers,
)
from app.core.config import settings
from app.core.exceptions import AppError, register_exception_handlers
from app.services.billing_quotes import (
    QUOTE_SCHEMA_VERSION,
    QUOTE_TOKEN_AUDIENCE,
    QUOTE_TOKEN_MAX_BYTES,
    QUOTE_TOKEN_TTL,
    QUOTE_TOKEN_TYPE,
    canonical_json,
    issue_quote,
    pricing_payload_sha256,
    quote_signing_key,
    request_sha256,
    verify_quote,
)
from app.services.pricing import (
    PRICING_POLICIES,
    PricingLine,
    RateScope,
    build_composite_pricing,
    build_simple_pricing,
    code_default_rate,
)

_REQUIRED_BILLING_HEADERS = Depends(require_billing_submission_headers)
_OPTIONAL_BILLING_HEADERS = Depends(optional_billing_submission_headers)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def quote_test_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "jwt_secret_key", "quote-test-secret-which-is-at-least-32-bytes")


@pytest.fixture
def header_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/required")
    def required(
        headers: BillingSubmissionHeaders = _REQUIRED_BILLING_HEADERS,
    ) -> dict[str, str]:
        return {"idempotency_key": str(headers.idempotency_key)}

    @app.post("/optional")
    def optional(
        headers: BillingSubmissionHeaders | None = _OPTIONAL_BILLING_HEADERS,
    ) -> dict[str, bool]:
        return {"present": headers is not None}

    return TestClient(app)


def scene_prompt_draft():
    policy = PRICING_POLICIES["scene_prompt"]
    return build_simple_pricing(
        policy=policy,
        rate=code_default_rate(policy),
        quantity=Decimal("1"),
    )


def _twenty_line_draft():
    policy = PRICING_POLICIES["video_create"]
    rate = code_default_rate(policy)
    line = PricingLine(
        operation=policy.operation,
        capability=policy.capability,
        unit=policy.unit,
        quantity=Decimal("1"),
        unit_credits=rate.unit_credits,
        subtotal_credits=rate.unit_credits,
        rate_scope=RateScope.TENANT_OVERRIDABLE,
        rate=rate,
        label="video",
    )
    return build_composite_pricing(operation="video_create", lines=(line,) * 20)


def _quote(now: datetime):
    return issue_quote(
        tenant_id="tenant-a",
        user_id="user-a",
        request_hash="a" * 64,
        draft=scene_prompt_draft(),
        now=now,
    )


def _resigned_token(now: datetime, **overrides: object) -> str:
    claims = jwt.decode(
        _quote(now).quote_token,
        quote_signing_key(settings.jwt_secret_key),
        algorithms=["HS256"],
        options={"verify_exp": False, "verify_iat": False, "verify_aud": False},
    )
    claims.update(overrides)
    return jwt.encode(
        claims,
        quote_signing_key(settings.jwt_secret_key),
        algorithm="HS256",
        headers={"typ": QUOTE_TOKEN_TYPE},
    )


def _verify(now: datetime, token: str) -> None:
    verify_quote(
        token=token,
        tenant_id="tenant-a",
        user_id="user-a",
        operation="scene_prompt",
        request_hash="a" * 64,
        current_draft=scene_prompt_draft(),
        now=now,
    )


def test_canonical_json_is_stable_utf8_and_normalizes_decimals() -> None:
    left = {"z": Decimal("1.00"), "a": [Decimal("2.50"), "新品"]}
    right = {"a": [Decimal("2.5"), "新品"], "z": Decimal("1")}

    expected = b'{"a":["2.5","\xe6\x96\xb0\xe5\x93\x81"],"z":"1"}'
    assert canonical_json(left) == canonical_json(right) == expected
    assert request_sha256(left) == request_sha256(right)


def test_quote_binds_user_tenant_request_and_pricing(now: datetime) -> None:
    quote = _quote(now)

    verified = verify_quote(
        token=quote.quote_token,
        tenant_id="tenant-a",
        user_id="user-a",
        operation="scene_prompt",
        request_hash="a" * 64,
        current_draft=scene_prompt_draft(),
        now=now + timedelta(minutes=9),
    )

    assert verified.snapshot.payable_credits == 30
    assert verified.snapshot.rounding == "ROUND_CEILING"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "tenant-b"),
        ("user_id", "user-b"),
        ("operation", "script_generate"),
        ("request_hash", "b" * 64),
    ],
)
def test_quote_rejects_mismatched_request_binding(
    now: datetime,
    field: str,
    value: str,
) -> None:
    kwargs = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "operation": "scene_prompt",
        "request_hash": "a" * 64,
    }
    kwargs[field] = value

    with pytest.raises(AppError):
        verify_quote(
            token=_quote(now).quote_token,
            current_draft=scene_prompt_draft(),
            now=now,
            **kwargs,
        )


def test_quote_expires_at_exact_ten_minute_boundary(now: datetime) -> None:
    with pytest.raises(AppError) as exc_info:
        verify_quote(
            token=_quote(now).quote_token,
            tenant_id="tenant-a",
            user_id="user-a",
            operation="scene_prompt",
            request_hash="a" * 64,
            current_draft=scene_prompt_draft(),
            now=now + timedelta(minutes=10),
        )

    assert exc_info.value.code == "QUOTE_EXPIRED"


def test_quote_normalizes_microsecond_issuance_to_signed_expiry(now: datetime) -> None:
    quote = _quote(now.replace(microsecond=987_654))
    claims = jwt.decode(
        quote.quote_token,
        quote_signing_key(settings.jwt_secret_key),
        algorithms=["HS256"],
        options={"verify_exp": False, "verify_iat": False, "verify_aud": False},
    )

    assert claims["exp"] - claims["iat"] == int(QUOTE_TOKEN_TTL.total_seconds())
    assert quote.expires_at == datetime.fromtimestamp(claims["exp"], UTC)
    assert quote.expires_at.microsecond == 0


@pytest.mark.parametrize("invalid_iat", ["not-a-time", True, 1.5])
def test_quote_rejects_non_integer_or_boolean_issued_at(now: datetime, invalid_iat: object) -> None:
    with pytest.raises(AppError) as exc_info:
        _verify(now, _resigned_token(now, iat=invalid_iat, exp=int(now.timestamp()) + 600))

    assert exc_info.value.code == "QUOTE_TOKEN_INVALID"


def test_quote_rejects_future_issued_at(now: datetime) -> None:
    future_iat = int(now.timestamp()) + 1

    with pytest.raises(AppError) as exc_info:
        _verify(now, _resigned_token(now, iat=future_iat, exp=future_iat + 600))

    assert exc_info.value.code == "QUOTE_TOKEN_INVALID"


def test_quote_rejects_a_ttl_other_than_exactly_ten_minutes(now: datetime) -> None:
    issued_at = int(now.timestamp())

    with pytest.raises(AppError) as exc_info:
        _verify(now, _resigned_token(now, iat=issued_at, exp=issued_at + 599))

    assert exc_info.value.code == "QUOTE_TOKEN_INVALID"


def test_quote_rejects_signature_tampering(now: datetime) -> None:
    quote = _quote(now)
    tampered = f"{quote.quote_token[:-1]}{'a' if quote.quote_token[-1] != 'a' else 'b'}"

    with pytest.raises(AppError) as exc_info:
        verify_quote(
            token=tampered,
            tenant_id="tenant-a",
            user_id="user-a",
            operation="scene_prompt",
            request_hash="a" * 64,
            current_draft=scene_prompt_draft(),
            now=now,
        )

    assert exc_info.value.code == "QUOTE_TOKEN_INVALID"


def test_quote_rejects_current_price_or_provenance_change(now: datetime) -> None:
    changed_rate = replace(code_default_rate(PRICING_POLICIES["scene_prompt"]), policy_version=2)
    changed_draft = build_simple_pricing(
        policy=PRICING_POLICIES["scene_prompt"],
        rate=changed_rate,
        quantity=Decimal("1"),
    )

    with pytest.raises(AppError) as exc_info:
        verify_quote(
            token=_quote(now).quote_token,
            tenant_id="tenant-a",
            user_id="user-a",
            operation="scene_prompt",
            request_hash="a" * 64,
            current_draft=changed_draft,
            now=now,
        )

    assert exc_info.value.code == "PRICE_CHANGED"


def test_quote_rejects_arithmetic_tampered_pricing_snapshot(now: datetime) -> None:
    invalid_line = replace(scene_prompt_draft().pricing_lines[0], subtotal_credits=Decimal("31"))
    invalid_draft = replace(scene_prompt_draft(), pricing_lines=(invalid_line,))

    with pytest.raises(AppError) as exc_info:
        pricing_payload_sha256(invalid_draft)

    assert exc_info.value.code == "QUOTE_PRICING_INVALID"


def test_quote_rejects_composite_display_breakdown_that_differs_from_canonical_lines() -> None:
    invalid_draft = replace(_twenty_line_draft(), breakdown=())

    with pytest.raises(AppError) as exc_info:
        pricing_payload_sha256(invalid_draft)

    assert exc_info.value.code == "QUOTE_PRICING_INVALID"


def test_required_headers_reject_oversize_quote_before_decode(header_client: TestClient) -> None:
    response = header_client.post(
        "/required",
        headers={"Idempotency-Key": str(uuid4()), "X-Huading-Quote": "x" * 4097},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "QUOTE_TOKEN_TOO_LARGE"


def test_required_headers_reject_non_ascii_quote_before_decode() -> None:
    with pytest.raises(AppError) as exc_info:
        require_billing_submission_headers(uuid4(), "\u62a5\u4ef7")

    assert exc_info.value.code == "QUOTE_TOKEN_INVALID"


def test_required_headers_reject_malformed_uuid(header_client: TestClient) -> None:
    response = header_client.post(
        "/required",
        headers={"Idempotency-Key": "not-a-uuid", "X-Huading-Quote": "token"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "headers",
    [
        {"Idempotency-Key": str(uuid4())},
        {"X-Huading-Quote": "token"},
    ],
)
def test_optional_headers_require_a_complete_pair(
    header_client: TestClient,
    headers: dict[str, str],
) -> None:
    response = header_client.post("/optional", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BILLING_HEADERS_REQUIRED"


def test_optional_headers_returns_none_only_when_both_are_absent(header_client: TestClient) -> None:
    assert header_client.post("/optional").json()["present"] is False
    assert (
        header_client.post(
            "/optional",
            headers={"Idempotency-Key": str(uuid4()), "X-Huading-Quote": "token"},
        ).json()["present"]
        is True
    )


def test_quote_uses_fixed_claims_and_an_independent_derived_key(now: datetime) -> None:
    quote = _quote(now)
    header = jwt.get_unverified_header(quote.quote_token)
    claims = jwt.decode(
        quote.quote_token,
        quote_signing_key(settings.jwt_secret_key),
        algorithms=["HS256"],
        audience=QUOTE_TOKEN_AUDIENCE,
        options={"verify_exp": False, "verify_iat": False},
    )

    assert quote_signing_key(settings.jwt_secret_key) != settings.jwt_secret_key.encode("utf-8")
    assert header["typ"] == QUOTE_TOKEN_TYPE
    assert claims["aud"] == QUOTE_TOKEN_AUDIENCE
    assert claims["typ"] == QUOTE_TOKEN_TYPE
    assert claims["schema"] == QUOTE_SCHEMA_VERSION
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            quote.quote_token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
            audience=QUOTE_TOKEN_AUDIENCE,
            options={"verify_exp": False, "verify_iat": False},
        )


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("aud", [QUOTE_TOKEN_AUDIENCE]),
        ("schema", True),
        ("schema", "1"),
    ],
)
def test_quote_rejects_non_exact_fixed_claim_values(
    now: datetime,
    claim: str,
    value: object,
) -> None:
    with pytest.raises(AppError) as exc_info:
        _verify(now, _resigned_token(now, **{claim: value}))

    assert exc_info.value.code == "QUOTE_TOKEN_INVALID"


def test_twenty_item_quote_token_fits_the_header_limit(now: datetime) -> None:
    quote = issue_quote(
        tenant_id="tenant-a",
        user_id="user-a",
        request_hash="a" * 64,
        draft=_twenty_line_draft(),
        now=now,
    )

    assert len(quote.quote_token.encode("ascii")) <= QUOTE_TOKEN_MAX_BYTES
