from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.core.exceptions import AppError
from app.db.models import BillingOperation, Subscription, UsageRecord, User
from app.services.billing_operations import (
    BillingInvariantError,
    UsageAllocation,
    billing_summary,
    complete_failed,
    complete_succeeded,
    create_reserved_operation,
    find_replay,
    lookup_operation,
    register_billing_result_schema,
)
from app.services.billing_quotes import VerifiedQuote
from app.services.pricing import (
    PricingLine,
    PricingSnapshot,
    RateScope,
    RateSource,
    ResolvedRate,
)


class EcomBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_ids: list[str]


class StoredResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: str


class LargeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


@pytest.fixture
def verified_quote() -> VerifiedQuote:
    rate = ResolvedRate(
        unit_credits=Decimal("0.6000"),
        source=RateSource.TENANT_RATE,
        rate_id="rate-batch",
        effective_at=datetime(2026, 8, 29, tzinfo=UTC),
        policy_key=None,
        policy_version=None,
    )
    line = PricingLine(
        operation="ecom_cutout",
        capability="image",
        unit="image",
        quantity=Decimal("4"),
        unit_credits=rate.unit_credits,
        subtotal_credits=Decimal("2.4"),
        rate_scope=RateScope.TENANT_OVERRIDABLE,
        rate=rate,
        label="cutout batch",
    )
    return VerifiedQuote(
        snapshot=PricingSnapshot(
            operation="ecom_cutout",
            pricing_shape="simple",
            pricing_lines=(line,),
            disclosures=(),
            subtotal_credits=Decimal("2.4"),
            payable_credits=3,
        ),
        quote_hash="b" * 64,
        pricing_payload_hash="c" * 64,
    )


@pytest.fixture
def zero_price_quote() -> VerifiedQuote:
    rate = ResolvedRate(
        unit_credits=Decimal("0"),
        source=RateSource.FIXED_POLICY,
        rate_id=None,
        effective_at=None,
        policy_key="cosyvoice_brand_voice_create",
        policy_version=1,
    )
    line = PricingLine(
        operation="cosyvoice_brand_voice_create",
        capability="voice_clone",
        unit="call",
        quantity=Decimal("1"),
        unit_credits=Decimal("0"),
        subtotal_credits=Decimal("0"),
        rate_scope=RateScope.PLATFORM_FIXED,
        rate=rate,
        label="CosyVoice clone",
    )
    return VerifiedQuote(
        snapshot=PricingSnapshot(
            operation="cosyvoice_brand_voice_create",
            pricing_shape="simple",
            pricing_lines=(line,),
            disclosures=(),
            subtotal_credits=Decimal("0"),
            payable_credits=0,
        ),
        quote_hash="d" * 64,
        pricing_payload_hash="e" * 64,
    )


def batch_allocations() -> list[UsageAllocation]:
    return [
        UsageAllocation(
            item_index=index,
            pricing_line_index=0,
            quantity=Decimal("1"),
            credits=Decimal("0.6"),
            provider="apimart",
            model="image-model",
            video_task_id=None,
        )
        for index in range(4)
    ]


def reserve_batch(db_session, verified_quote, *, key=None, request_hash="a" * 64):
    operation = create_reserved_operation(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        operation="ecom_cutout",
        idempotency_key=key or uuid4(),
        request_hash=request_hash,
        verified_quote=verified_quote,
        usage_allocations=batch_allocations(),
    )
    db_session.commit()
    return operation


def test_partial_success_rounds_success_subtotal_once(db_session, verified_quote):
    register_billing_result_schema("test_ecom_batch", EcomBatchResult)
    reserved = reserve_batch(db_session, verified_quote)

    operation = complete_succeeded(
        db_session,
        operation_id=reserved.id,
        actual_quantities={0: Decimal("1"), 2: Decimal("1")},
        result_type="test_ecom_batch",
        result_id="batch-1",
        result_payload=EcomBatchResult(item_ids=["item-0", "item-2"]),
    )
    db_session.commit()

    assert operation.requested_credits == 3
    assert operation.settled_credits == 2
    assert operation.released_credits == 1
    assert billing_summary(operation).status == "partially_settled"
    subscription = db_session.get(Subscription, "subscription-a")
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 2


def test_same_key_same_request_does_not_reserve_twice(db_session, verified_quote):
    key = uuid4()
    first = reserve_batch(db_session, verified_quote, key=key)
    second = create_reserved_operation(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        operation="ecom_cutout",
        idempotency_key=key,
        request_hash="a" * 64,
        verified_quote=verified_quote,
        usage_allocations=batch_allocations(),
    )
    replay = find_replay(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        operation="ecom_cutout",
        idempotency_key=key,
        request_hash="a" * 64,
    )

    assert second.id == first.id
    assert replay is not None and replay.replayed is True
    assert db_session.get(Subscription, "subscription-a").quota_credits_reserved == 3
    assert db_session.scalar(select(func.count()).select_from(UsageRecord)) == 4


def test_same_key_different_request_is_rejected(db_session, verified_quote):
    key = uuid4()
    reserve_batch(db_session, verified_quote, key=key)

    with pytest.raises(AppError) as exc_info:
        find_replay(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            operation="ecom_cutout",
            idempotency_key=key,
            request_hash="f" * 64,
        )

    assert exc_info.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_same_key_is_isolated_by_user(db_session, verified_quote):
    key = uuid4()
    reserve_batch(db_session, verified_quote, key=key)
    db_session.add(
        User(
            id="user-b",
            tenant_id="tenant-a",
            email="billing-b@example.com",
            password_hash="hash",
            role="creator",
        )
    )
    db_session.commit()

    second = create_reserved_operation(
        db_session,
        tenant_id="tenant-a",
        user_id="user-b",
        operation="ecom_cutout",
        idempotency_key=key,
        request_hash="a" * 64,
        verified_quote=verified_quote,
        usage_allocations=batch_allocations(),
    )
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(BillingOperation)) == 2
    assert second.user_id == "user-b"
    assert db_session.get(Subscription, "subscription-a").quota_credits_reserved == 6


def test_insufficient_quota_creates_no_ledger_rows(db_session, verified_quote):
    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_total = 2
    db_session.commit()

    with pytest.raises(AppError) as exc_info:
        create_reserved_operation(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            operation="ecom_cutout",
            idempotency_key=uuid4(),
            request_hash="a" * 64,
            verified_quote=verified_quote,
            usage_allocations=batch_allocations(),
        )
    db_session.rollback()

    assert exc_info.value.code == "TENANT_QUOTA_EXCEEDED"
    assert db_session.scalar(select(func.count()).select_from(BillingOperation)) == 0
    assert db_session.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_missing_subscription_creates_no_ledger_rows(db_session, verified_quote):
    db_session.delete(db_session.get(Subscription, "subscription-a"))
    db_session.commit()

    with pytest.raises(AppError) as exc_info:
        create_reserved_operation(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            operation="ecom_cutout",
            idempotency_key=uuid4(),
            request_hash="a" * 64,
            verified_quote=verified_quote,
            usage_allocations=batch_allocations(),
        )
    db_session.rollback()

    assert exc_info.value.code == "SUBSCRIPTION_NOT_FOUND"
    assert db_session.scalar(select(func.count()).select_from(BillingOperation)) == 0


def test_reservation_revalidates_verified_snapshot_before_wallet_mutation(
    db_session, verified_quote
):
    invalid = replace(
        verified_quote,
        snapshot=replace(verified_quote.snapshot, payable_credits=99),
    )

    with pytest.raises(AppError) as exc_info:
        create_reserved_operation(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            operation="ecom_cutout",
            idempotency_key=uuid4(),
            request_hash="a" * 64,
            verified_quote=invalid,
            usage_allocations=batch_allocations(),
        )
    db_session.rollback()

    assert exc_info.value.code == "BILLING_QUOTE_INVALID"
    assert db_session.get(Subscription, "subscription-a").quota_credits_reserved == 0
    assert db_session.scalar(select(func.count()).select_from(BillingOperation)) == 0


def test_zero_price_operation_is_idempotent_and_keeps_wallet_unchanged(
    db_session, zero_price_quote
):
    key = uuid4()
    allocation = UsageAllocation(0, 0, Decimal("1"), Decimal("0"), "cosyvoice", None, None)
    first = create_reserved_operation(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        operation="cosyvoice_brand_voice_create",
        idempotency_key=key,
        request_hash="a" * 64,
        verified_quote=zero_price_quote,
        usage_allocations=[allocation],
    )
    second = create_reserved_operation(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        operation="cosyvoice_brand_voice_create",
        idempotency_key=key,
        request_hash="a" * 64,
        verified_quote=zero_price_quote,
        usage_allocations=[allocation],
    )

    assert second.id == first.id
    assert db_session.get(Subscription, "subscription-a").quota_credits_reserved == 0
    assert db_session.scalar(select(func.count()).select_from(UsageRecord)) == 1


def test_actual_quantity_may_be_shorter_or_equal_but_never_greater(
    db_session, verified_quote
):
    register_billing_result_schema("test_quantity_result", EcomBatchResult)
    reserved = reserve_batch(db_session, verified_quote)
    completed = complete_succeeded(
        db_session,
        operation_id=reserved.id,
        actual_quantities={0: Decimal("0.5"), 1: Decimal("1")},
        result_type="test_quantity_result",
        result_id=None,
        result_payload=EcomBatchResult(item_ids=["a", "b"]),
    )
    db_session.commit()
    assert completed.settled_credits == 1
    assert completed.released_credits == 2

    second = reserve_batch(db_session, verified_quote, key=uuid4())
    failed = complete_succeeded(
        db_session,
        operation_id=second.id,
        actual_quantities={0: Decimal("1.001")},
        result_type="test_quantity_result",
        result_id=None,
        result_payload=EcomBatchResult(item_ids=["overage"]),
    )
    db_session.commit()
    assert failed.completion_kind == "failed"
    assert failed.settled_credits == 0
    assert failed.released_credits == 3
    assert db_session.get(Subscription, "subscription-a").quota_credits_used == 1

    third = reserve_batch(db_session, verified_quote, key=uuid4())
    invalid = complete_succeeded(
        db_session,
        operation_id=third.id,
        actual_quantities={0: Decimal("NaN")},
        result_type="test_quantity_result",
        result_id=None,
        result_payload=EcomBatchResult(item_ids=["invalid"]),
    )
    db_session.commit()
    assert invalid.completion_kind == "failed"
    assert invalid.settled_credits == 0
    assert invalid.released_credits == 3


def test_repeated_settle_and_release_are_terminal_noops(db_session, verified_quote):
    register_billing_result_schema("test_repeat_result", EcomBatchResult)
    reserved = reserve_batch(db_session, verified_quote)
    first = complete_succeeded(
        db_session,
        operation_id=reserved.id,
        actual_quantities={0: Decimal("1")},
        result_type="test_repeat_result",
        result_id=None,
        result_payload=EcomBatchResult(item_ids=["a"]),
    )
    second = complete_failed(
        db_session,
        operation_id=reserved.id,
        code="PROVIDER_FAILED",
        http_status=502,
        sanitized_detail={"reason": "later duplicate"},
    )
    third = complete_succeeded(
        db_session,
        operation_id=reserved.id,
        actual_quantities={0: Decimal("1")},
        result_type="test_repeat_result",
        result_id=None,
        result_payload=EcomBatchResult(item_ids=["a"]),
    )

    assert first.completion_kind == second.completion_kind == third.completion_kind == "succeeded"
    assert db_session.get(Subscription, "subscription-a").quota_credits_used == 1


def test_result_and_error_payload_bounds_and_sanitization(db_session, verified_quote):
    register_billing_result_schema("test_large_result", LargeResult)
    reserved = reserve_batch(db_session, verified_quote)
    with pytest.raises(AppError) as result_error:
        complete_succeeded(
            db_session,
            operation_id=reserved.id,
            actual_quantities={0: Decimal("1")},
            result_type="test_large_result",
            result_id=None,
            result_payload=LargeResult(value="x" * (64 * 1024)),
        )
    assert result_error.value.code == "BILLING_RESULT_PAYLOAD_TOO_LARGE"
    db_session.rollback()

    reserved = reserve_batch(db_session, verified_quote, key=uuid4())
    with pytest.raises(AppError) as error_error:
        complete_failed(
            db_session,
            operation_id=reserved.id,
            code="PROVIDER_FAILED",
            http_status=502,
            sanitized_detail={"message": "x" * (16 * 1024)},
        )
    assert error_error.value.code == "BILLING_ERROR_PAYLOAD_TOO_LARGE"
    db_session.rollback()

    reserved = reserve_batch(db_session, verified_quote, key=uuid4())
    failed = complete_failed(
        db_session,
        operation_id=reserved.id,
        code="PROVIDER_FAILED",
        http_status=502,
        sanitized_detail={"api_key": "secret-value", "reason": "timeout"},
    )
    assert failed.error_payload == {
        "detail": {"api_key": "[REDACTED]", "reason": "timeout"}
    }
    assert "secret-value" not in str(failed.error_payload)

    reserved = reserve_batch(db_session, verified_quote, key=uuid4())
    with pytest.raises(AppError) as raw_exception_error:
        complete_failed(
            db_session,
            operation_id=reserved.id,
            code="PROVIDER_FAILED",
            http_status=502,
            sanitized_detail=RuntimeError("raw supplier exception"),
        )
    assert raw_exception_error.value.code == "BILLING_ERROR_PAYLOAD_INVALID"


def test_lookup_returns_all_four_closed_union_states(db_session, verified_quote):
    register_billing_result_schema("test_lookup_result", EcomBatchResult)
    register_billing_result_schema("test_lookup_resource", StoredResource)

    reserved = reserve_batch(db_session, verified_quote)
    reserved_lookup = lookup_operation(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        operation=reserved.operation,
        idempotency_key=UUID(reserved.idempotency_key),
    )
    assert reserved_lookup is not None and reserved_lookup.state == "in_progress"

    succeeded = reserve_batch(db_session, verified_quote, key=uuid4())
    complete_succeeded(
        db_session,
        operation_id=succeeded.id,
        actual_quantities={0: Decimal("1")},
        result_type="test_lookup_result",
        result_id="result-1",
        result_payload=EcomBatchResult(item_ids=["a"]),
    )
    succeeded_lookup = lookup_operation(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        operation=succeeded.operation,
        idempotency_key=UUID(succeeded.idempotency_key),
    )
    assert succeeded_lookup is not None
    assert succeeded_lookup.completion_kind == "succeeded"
    assert succeeded_lookup.result.item_ids == ["a"]

    failed = reserve_batch(db_session, verified_quote, key=uuid4())
    complete_failed(
        db_session,
        operation_id=failed.id,
        code="PROVIDER_FAILED",
        http_status=502,
        sanitized_detail={"reason": "timeout"},
    )
    failed_lookup = lookup_operation(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        operation=failed.operation,
        idempotency_key=UUID(failed.idempotency_key),
    )
    assert failed_lookup is not None
    assert failed_lookup.failure.code == "PROVIDER_FAILED"
    assert failed_lookup.failure.original_http_status == 502

    rejected = reserve_batch(db_session, verified_quote, key=uuid4())
    rejected.status = "completed"
    rejected.completion_kind = "rejected"
    rejected.completed_at = datetime.now(UTC)
    rejected.released_credits = rejected.requested_credits
    rejected.result_type = "test_lookup_resource"
    rejected.result_id = "order-1"
    rejected.result_payload = {"id": "order-1", "status": "rejected"}
    for usage in db_session.scalars(
        select(UsageRecord).where(UsageRecord.billing_operation_id == rejected.id)
    ):
        usage.status = "released"
        usage.settled_at = datetime.now(UTC)
    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_reserved -= 3
    db_session.commit()
    rejected_lookup = lookup_operation(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        operation=rejected.operation,
        idempotency_key=UUID(rejected.idempotency_key),
    )
    assert rejected_lookup is not None
    assert rejected_lookup.completion_kind == "rejected"
    assert rejected_lookup.resource.id == "order-1"


def test_lookup_fails_closed_for_unknown_or_invalid_stored_payload(
    db_session, verified_quote
):
    operation = reserve_batch(db_session, verified_quote)
    operation.result_type = "not_registered"
    operation.result_payload = {"raw": "must not escape"}
    db_session.commit()
    with pytest.raises(BillingInvariantError):
        lookup_operation(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            operation=operation.operation,
            idempotency_key=UUID(operation.idempotency_key),
        )

    register_billing_result_schema("test_invalid_stored", EcomBatchResult)
    operation.result_type = "test_invalid_stored"
    operation.result_payload = {"unexpected": "raw"}
    db_session.commit()
    with pytest.raises(BillingInvariantError):
        lookup_operation(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            operation=operation.operation,
            idempotency_key=UUID(operation.idempotency_key),
        )


def test_legacy_unassociated_reservation_coexists_with_operation_reservation(
    db_session, verified_quote
):
    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_reserved = 7
    legacy = UsageRecord(
        tenant_id="tenant-a",
        subscription_id=subscription.id,
        capability="image",
        provider="legacy",
        unit="image",
        quantity=Decimal("1"),
        credits=Decimal("7"),
        cost_cents=0,
        status="reserved",
    )
    db_session.add(legacy)
    db_session.commit()

    reserve_batch(db_session, verified_quote)

    assert db_session.get(Subscription, "subscription-a").quota_credits_reserved == 10
    assert legacy.billing_operation_id is None
    assert legacy.billing_item_index is None
    assert legacy.billing_pricing_line_index is None


def test_lookup_route_hides_other_users_and_visible_failure_is_http_200(
    auth_db, auth_context, verified_quote
):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    register_billing_result_schema("test_route_result", EcomBatchResult)
    with auth_db() as db:
        db.add(
            User(
                id="lookup-user-b",
                tenant_id=auth_context["tenant_id"],
                email="lookup-b@example.com",
                password_hash="hash",
                role="creator",
            )
        )
        db.commit()
        hidden = create_reserved_operation(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id="lookup-user-b",
            operation="ecom_cutout",
            idempotency_key=uuid4(),
            request_hash="a" * 64,
            verified_quote=verified_quote,
            usage_allocations=batch_allocations(),
        )
        visible = create_reserved_operation(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="ecom_cutout",
            idempotency_key=uuid4(),
            request_hash="a" * 64,
            verified_quote=verified_quote,
            usage_allocations=batch_allocations(),
        )
        complete_failed(
            db,
            operation_id=visible.id,
            code="PROVIDER_FAILED",
            http_status=502,
            sanitized_detail={"reason": "timeout"},
        )
        succeeded = create_reserved_operation(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="ecom_cutout",
            idempotency_key=uuid4(),
            request_hash="a" * 64,
            verified_quote=verified_quote,
            usage_allocations=batch_allocations(),
        )
        complete_succeeded(
            db,
            operation_id=succeeded.id,
            actual_quantities={0: Decimal("1")},
            result_type="test_route_result",
            result_id="batch-route",
            result_payload=EcomBatchResult(item_ids=["item-route"]),
        )
        db.commit()
        hidden_key = hidden.idempotency_key
        visible_key = visible.idempotency_key
        succeeded_key = succeeded.idempotency_key

    hidden_response = client.get(
        f"/api/v1/billing/operations/by-idempotency/ecom_cutout/{hidden_key}",
        headers=auth_context["headers"],
    )
    visible_response = client.get(
        f"/api/v1/billing/operations/by-idempotency/ecom_cutout/{visible_key}",
        headers=auth_context["headers"],
    )
    succeeded_response = client.get(
        f"/api/v1/billing/operations/by-idempotency/ecom_cutout/{succeeded_key}",
        headers=auth_context["headers"],
    )

    assert hidden_response.status_code == 404
    assert visible_response.status_code == 200
    assert visible_response.json()["data"]["failure"]["original_http_status"] == 502
    assert succeeded_response.status_code == 200
    assert succeeded_response.json()["data"]["result"] == {"item_ids": ["item-route"]}
