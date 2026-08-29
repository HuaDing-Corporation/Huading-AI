from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import Asset, BillingOperation, Subscription, TaskAsset, UsageRecord, VideoTask
from app.schemas.ecom_images import EcomCutoutBatchRequest, EcomCutoutRequest
from app.services.billing_operations import UsageAllocation, create_reserved_operation
from app.services.billing_quotes import VerifiedQuote
from app.services.pricing import (
    PricingLine,
    PricingSnapshot,
    RateScope,
    RateSource,
    ResolvedRate,
)


def _ecom_quote(*, quantity: int) -> VerifiedQuote:
    rate = ResolvedRate(
        unit_credits=Decimal("0.4000"),
        source=RateSource.TENANT_RATE,
        rate_id="ecom-terminal-rate",
        effective_at=datetime(2026, 8, 29, tzinfo=UTC),
        policy_key=None,
        policy_version=None,
    )
    subtotal = Decimal(quantity) * rate.unit_credits
    line = PricingLine(
        operation="ecom_cutout",
        capability="image",
        unit="image",
        quantity=Decimal(quantity),
        unit_credits=rate.unit_credits,
        subtotal_credits=subtotal,
        rate_scope=RateScope.TENANT_OVERRIDABLE,
        rate=rate,
        label="ecom_cutout",
    )
    return VerifiedQuote(
        snapshot=PricingSnapshot(
            operation="ecom_cutout",
            pricing_shape="simple",
            pricing_lines=(line,),
            disclosures=(),
            subtotal_credits=subtotal,
            payable_credits=1,
        ),
        quote_hash="a" * 64,
        pricing_payload_hash="b" * 64,
    )


def _create_terminal_ecom_operation(
    db,
    *,
    tenant_id: str,
    user_id: str,
    successful: bool,
) -> str:
    tasks = [
        VideoTask(
            id=str(uuid4()),
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            status="queued",
            topic="cutout",
            mode="photo",
            video_mode="photo",
            params={"kind": "ecom_cutout", "source_asset_id": f"source-{index}"},
        )
        for index in range(2)
    ]
    db.add_all(tasks)
    db.flush()
    operation = create_reserved_operation(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        operation="ecom_cutout",
        idempotency_key=uuid4(),
        request_hash="c" * 64,
        verified_quote=_ecom_quote(quantity=len(tasks)),
        usage_allocations=tuple(
            UsageAllocation(index, 0, Decimal("1"), Decimal("0.4"), "apimart", None, task.id)
            for index, task in enumerate(tasks)
        ),
        result_type="ecom_image_batch",
        result_id="terminal-ecom-batch",
    )
    for index, task in enumerate(tasks):
        task.params = {
            **task.params,
            "billing_operation_id": operation.id,
            "billing_item_index": index,
        }
        task.status = "done" if successful else "failed"
        if successful:
            asset = Asset(
                tenant_id=tenant_id,
                type="generated_image",
                source="generated",
                storage_key=f"tenants/{tenant_id}/photos/{task.id}/output.png",
                mime_type="image/png",
                status="ready",
            )
            db.add(asset)
            db.flush()
            db.add(TaskAsset(video_task_id=task.id, asset_id=asset.id, role="output_image"))
    db.commit()
    return operation.id


def test_cutout_single_and_one_item_batch_normalize_to_the_same_items() -> None:
    from app.services.ecom_billing import normalize_cutout_items

    item = EcomCutoutRequest(source_asset_id="cutout-source")

    assert normalize_cutout_items(item) == normalize_cutout_items(
        EcomCutoutBatchRequest(items=[item])
    )


def test_cutout_batch_rejects_a_twenty_first_item() -> None:
    from pydantic import ValidationError

    from app.schemas.ecom_images import EcomCutoutBatchRequest

    try:
        EcomCutoutBatchRequest(
            items=[{"source_asset_id": f"cutout-{index}"} for index in range(21)]
        )
    except ValidationError:
        return
    raise AssertionError("21 e-commerce image items must be rejected")


def test_cutout_estimate_prices_every_validated_item(auth_context, auth_db) -> None:
    from fastapi.testclient import TestClient

    from app.db.models import Asset, ProviderConfig
    from app.main import app

    with auth_db() as db:
        db.add(
            ProviderConfig(
                tenant_id=None,
                capability="image",
                provider="apimart",
                config={"api_key": "quote-test-key"},
                is_active=True,
            )
        )
        db.add_all(
            [
                Asset(
                    id=f"cutout-{index}",
                    tenant_id=auth_context["tenant_id"],
                    type="product_image",
                    source="upload",
                    storage_key=f"tenants/{auth_context['tenant_id']}/uploads/cutout-{index}.png",
                    mime_type="image/png",
                    status="ready",
                )
                for index in range(20)
            ]
        )
        db.commit()

    response = TestClient(app).post(
        "/api/v1/ecom-images/cutout/estimate",
        headers=auth_context["headers"],
        json={"items": [{"source_asset_id": f"cutout-{index}"} for index in range(20)]},
    )

    assert response.status_code == 200
    assert response.json()["data"]["quantity"] == "20"
    assert response.json()["data"]["payable_credits"] == 1600


def test_cutout_estimate_rejects_an_unowned_source_before_issuing_a_quote(auth_context) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).post(
        "/api/v1/ecom-images/cutout/estimate",
        headers=auth_context["headers"],
        json={"source_asset_id": "not-owned"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ECOM_SOURCE_ASSET_NOT_FOUND"


def test_single_and_one_item_batch_have_the_same_signed_request_hash() -> None:
    from app.services.ecom_billing import ecom_request_hash, normalize_cutout_items

    item = EcomCutoutRequest(source_asset_id="same-source", background="transparent")

    assert ecom_request_hash(
        operation="ecom_cutout", items=normalize_cutout_items(item)
    ) == ecom_request_hash(
        operation="ecom_cutout",
        items=normalize_cutout_items(EcomCutoutBatchRequest(items=[item])),
    )


def test_changing_the_twentieth_item_changes_the_signed_request_hash() -> None:
    from app.services.ecom_billing import ecom_request_hash, normalize_cutout_items

    original = EcomCutoutBatchRequest(
        items=[{"source_asset_id": f"source-{index}"} for index in range(20)]
    )
    changed = EcomCutoutBatchRequest(
        items=[
            {"source_asset_id": "replacement-19" if index == 19 else f"source-{index}"}
            for index in range(20)
        ]
    )

    assert ecom_request_hash(
        operation="ecom_cutout", items=normalize_cutout_items(original)
    ) != ecom_request_hash(
        operation="ecom_cutout", items=normalize_cutout_items(changed)
    )


def test_ecom_partial_success_rounds_the_parent_reservation_once(auth_db, auth_context) -> None:
    from app.db.models import Asset, TaskAsset, VideoTask
    from app.services.billing_operations import UsageAllocation, create_reserved_operation
    from app.services.billing_quotes import VerifiedQuote
    from app.services.ecom_billing import try_finalize_ecom_operation
    from app.services.pricing import (
        PricingLine,
        PricingSnapshot,
        RateScope,
        RateSource,
        ResolvedRate,
    )

    rate = ResolvedRate(
        unit_credits=Decimal("0.4000"),
        source=RateSource.TENANT_RATE,
        rate_id="ecom-decimal-rate",
        effective_at=datetime(2026, 8, 29, tzinfo=UTC),
        policy_key=None,
        policy_version=None,
    )
    line = PricingLine(
        operation="ecom_cutout",
        capability="image",
        unit="image",
        quantity=Decimal("3"),
        unit_credits=rate.unit_credits,
        subtotal_credits=Decimal("1.2"),
        rate_scope=RateScope.TENANT_OVERRIDABLE,
        rate=rate,
        label="ecom_cutout",
    )
    quote = VerifiedQuote(
        snapshot=PricingSnapshot(
            operation="ecom_cutout",
            pricing_shape="simple",
            pricing_lines=(line,),
            disclosures=(),
            subtotal_credits=Decimal("1.2"),
            payable_credits=2,
        ),
        quote_hash="a" * 64,
        pricing_payload_hash="b" * 64,
    )
    with auth_db() as db:
        tasks = [
            VideoTask(
                id=str(uuid4()),
                tenant_id=auth_context["tenant_id"],
                created_by_user_id=auth_context["user_id"],
                status="queued",
                topic="cutout",
                mode="photo",
                video_mode="photo",
                params={"kind": "ecom_cutout", "source_asset_id": f"source-{index}"},
            )
            for index in range(3)
        ]
        db.add_all(tasks)
        db.flush()
        operation = create_reserved_operation(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="ecom_cutout",
            idempotency_key=uuid4(),
            request_hash="c" * 64,
            verified_quote=quote,
            usage_allocations=tuple(
                UsageAllocation(index, 0, Decimal("1"), Decimal("0.4"), "apimart", None, task.id)
                for index, task in enumerate(tasks)
            ),
            result_type="ecom_image_batch",
            result_id="batch-id",
        )
        for index, task in enumerate(tasks):
            task.params = {
                **task.params,
                "billing_operation_id": operation.id,
                "billing_item_index": index,
            }
            task.status = "done" if index in {0, 2} else "failed"
        db.commit()
        operation_id = operation.id

    with auth_db() as db:
        finalized = try_finalize_ecom_operation(db, billing_operation_id=operation_id)
        assert finalized is None
        done_tasks = list(
            db.scalars(
                select(VideoTask).where(
                    VideoTask.params["billing_operation_id"].as_string() == operation_id,
                    VideoTask.status == "done",
                )
            )
        )
        for task in done_tasks:
            asset = Asset(
                tenant_id=auth_context["tenant_id"],
                type="generated_image",
                source="generated",
                storage_key=f"tenants/{auth_context['tenant_id']}/photos/{task.id}/output.png",
                mime_type="image/png",
                status="ready",
            )
            db.add(asset)
            db.flush()
            db.add(TaskAsset(video_task_id=task.id, asset_id=asset.id, role="output_image"))
        db.commit()

    with auth_db() as db:
        finalized = try_finalize_ecom_operation(db, billing_operation_id=operation_id)
        assert finalized is not None
        assert finalized.settled_credits == 1
        assert finalized.released_credits == 1
        assert try_finalize_ecom_operation(db, billing_operation_id=operation_id) is not None


@pytest.mark.parametrize("successful", [True, False], ids=["all_success", "all_failed"])
def test_terminal_ecom_finalization_is_idempotent_and_conserves_the_reservation(
    auth_db,
    auth_context,
    successful: bool,
) -> None:
    from app.services.ecom_billing import try_finalize_ecom_operation

    with auth_db() as db:
        operation_id = _create_terminal_ecom_operation(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            successful=successful,
        )

    with auth_db() as db:
        finalized = try_finalize_ecom_operation(db, billing_operation_id=operation_id)
        assert finalized is not None
        operation = db.get(BillingOperation, operation_id)
        assert operation is not None
        usages = list(
            db.scalars(
                select(UsageRecord)
                .where(UsageRecord.billing_operation_id == operation_id)
                .order_by(UsageRecord.billing_item_index)
            )
        )
        subscription = db.scalars(select(Subscription)).one()
        first_terminal_state = (
            operation.status,
            operation.completion_kind,
            operation.requested_credits,
            operation.settled_credits,
            operation.released_credits,
            operation.result_payload,
            operation.error_code,
            tuple(
                (usage.status, usage.quantity, usage.credits, usage.settled_at)
                for usage in usages
            ),
            subscription.quota_credits_used,
            subscription.quota_credits_reserved,
        )
        assert operation.status == "completed"
        assert operation.completion_kind == ("succeeded" if successful else "failed")
        assert operation.settled_credits + operation.released_credits == operation.requested_credits
        assert operation.settled_credits == (1 if successful else 0)
        assert operation.released_credits == (0 if successful else 1)
        assert [usage.status for usage in usages] == (
            ["settled", "settled"] if successful else ["released", "released"]
        )

    with auth_db() as db:
        repeated = try_finalize_ecom_operation(db, billing_operation_id=operation_id)
        assert repeated is not None
        operation = db.get(BillingOperation, operation_id)
        assert operation is not None
        usages = list(
            db.scalars(
                select(UsageRecord)
                .where(UsageRecord.billing_operation_id == operation_id)
                .order_by(UsageRecord.billing_item_index)
            )
        )
        subscription = db.scalars(select(Subscription)).one()
        repeated_terminal_state = (
            operation.status,
            operation.completion_kind,
            operation.requested_credits,
            operation.settled_credits,
            operation.released_credits,
            operation.result_payload,
            operation.error_code,
            tuple(
                (usage.status, usage.quantity, usage.credits, usage.settled_at)
                for usage in usages
            ),
            subscription.quota_credits_used,
            subscription.quota_credits_reserved,
        )
        assert len(list(db.scalars(select(BillingOperation)))) == 1

    assert repeated_terminal_state == first_terminal_state
