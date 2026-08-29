from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.deps import BillingSubmissionHeaders
from app.core.exceptions import AppError
from app.db.models import (
    AdminAuditLog,
    Asset,
    BillingOperation,
    BrandVoice,
    BrandVoiceOrder,
    BrandVoiceProviderId,
    CreditRefundGrant,
    Plan,
    ProviderConfig,
    Subscription,
    Tenant,
    UsageRecord,
    User,
)
from app.schemas.brand_voice_orders import BrandVoiceOrderCreateRequest
from app.services.billing_quotes import VerifiedQuote, _validated_snapshot
from app.services.pricing import PRICING_POLICIES, build_simple_pricing, resolve_rate


def _verified_order_quote(db, *, user: User, payload: BrandVoiceOrderCreateRequest):
    operation = f"doubao_brand_voice_order_{payload.order_type}"
    draft = build_simple_pricing(
        policy=PRICING_POLICIES[operation],
        rate=resolve_rate(db, tenant_id=user.tenant_id, policy=PRICING_POLICIES[operation]),
        quantity=Decimal("1"),
    )
    return VerifiedQuote(
        snapshot=_validated_snapshot(draft)[1],
        quote_hash="a" * 64,
        pricing_payload_hash="b" * 64,
    )


def _seed_manual_order_for_invariant_test(db_session) -> tuple[str, str]:
    from app.services.brand_voice_orders import create_brand_voice_order

    user = db_session.get(User, "user-a")
    subscription = db_session.get(Subscription, "subscription-a")
    db_session.get(Plan, subscription.plan_id).code = "huading"
    subscription.quota_credits_total = 100_000
    asset = Asset(
        id="invariant-audio",
        tenant_id=user.tenant_id,
        type="audio",
        source="upload",
        storage_key=f"tenants/{user.tenant_id}/uploads/invariant.wav",
        mime_type="audio/wav",
        duration_ms=10_000,
        status="ready",
    )
    db_session.add(asset)
    db_session.commit()
    payload = BrandVoiceOrderCreateRequest(
        order_type="create",
        requested_name="Invariant voice",
        source_audio_asset_id=asset.id,
        consent_confirmed=True,
    )
    order = create_brand_voice_order(
        db_session,
        user=user,
        payload=payload,
        verified_quote=_verified_order_quote(db_session, user=user, payload=payload),
        submission_headers=BillingSubmissionHeaders(
            idempotency_key=uuid4(),
            quote_token="quoted",
        ),
        now=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
    )
    db_session.commit()
    return order.id, order.billing_operation_id


def _manual_order_state(db_session, *, order_id: str) -> tuple[object, ...]:
    order = db_session.get(BrandVoiceOrder, order_id)
    operation = db_session.get(BillingOperation, order.billing_operation_id)
    usages = list(
        db_session.scalars(
            select(UsageRecord)
            .where(UsageRecord.billing_operation_id == operation.id)
            .order_by(UsageRecord.id)
        )
    )
    subscription = db_session.get(Subscription, usages[0].subscription_id)
    return (
        (
            order.tenant_id,
            order.user_id,
            order.billing_operation_id,
            order.source_audio_asset_id,
            order.existing_brand_voice_id,
            order.status,
            order.resolver_user_id,
            order.fulfilled_brand_voice_id,
            order.fulfilled_provider_id,
            order.fulfilled_at,
            order.rejected_at,
            order.rejection_reason,
        ),
        (
            operation.tenant_id,
            operation.user_id,
            operation.operation,
            operation.status,
            operation.completion_kind,
            operation.completed_at,
            operation.result_type,
            operation.result_id,
            operation.pricing_snapshot,
            Decimal(operation.requested_credits),
            Decimal(operation.settled_credits),
            Decimal(operation.released_credits),
            operation.result_payload,
        ),
        tuple(
            (
                usage.id,
                usage.subscription_id,
                usage.tenant_id,
                usage.status,
                usage.settled_at,
                usage.billing_item_index,
                usage.billing_pricing_line_index,
                usage.capability,
                usage.provider,
                usage.model,
                usage.unit,
                Decimal(usage.quantity),
                Decimal(usage.credits),
            )
            for usage in usages
        ),
        (
            subscription.id,
            subscription.quota_credits_total,
            subscription.quota_credits_used,
            subscription.quota_credits_reserved,
        ),
        tuple(
            db_session.execute(
                select(
                    CreditRefundGrant.id,
                    CreditRefundGrant.status,
                    CreditRefundGrant.amount_credits,
                    CreditRefundGrant.target_subscription_id,
                ).order_by(CreditRefundGrant.id)
            )
        ),
        tuple(
            db_session.execute(
                select(
                    BrandVoiceProviderId.id,
                    BrandVoiceProviderId.normalized_provider_id,
                    BrandVoiceProviderId.status,
                ).order_by(BrandVoiceProviderId.id)
            )
        ),
        tuple(
            db_session.execute(
                select(
                    ProviderConfig.id,
                    ProviderConfig.tenant_id,
                    ProviderConfig.capability,
                    ProviderConfig.provider,
                    ProviderConfig.config,
                    ProviderConfig.is_active,
                ).order_by(ProviderConfig.id)
            )
        ),
        tuple(
            db_session.execute(
                select(
                    BillingOperation.id,
                    BillingOperation.status,
                    BillingOperation.completion_kind,
                    BillingOperation.requested_credits,
                    BillingOperation.settled_credits,
                    BillingOperation.released_credits,
                ).order_by(BillingOperation.id)
            )
        ),
        tuple(
            db_session.execute(
                select(
                    UsageRecord.id,
                    UsageRecord.billing_operation_id,
                    UsageRecord.subscription_id,
                    UsageRecord.tenant_id,
                    UsageRecord.status,
                    UsageRecord.settled_at,
                    UsageRecord.credits,
                ).order_by(UsageRecord.id)
            )
        ),
        db_session.scalar(select(func.count()).select_from(AdminAuditLog)),
        db_session.scalar(select(func.count()).select_from(BrandVoice)),
    )


def _assert_resolve_failure_preserves_every_surface(
    db_session,
    *,
    order_id: str,
    expected_exception: type[Exception] | None = None,
    expected_code: str = "BILLING_INVARIANT_VIOLATION",
) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import resolve_brand_voice_order

    before = _manual_order_state(db_session, order_id=order_id)
    with pytest.raises(expected_exception or BillingInvariantError) as captured:
        resolve_brand_voice_order(
            db_session,
            actor=db_session.get(User, "user-a"),
            order_id=order_id,
            action="reject",
            rejection_reason="invalid invariant",
            now=datetime(2026, 8, 29, 12, 2, tzinfo=UTC),
        )
    assert captured.value.code == expected_code
    db_session.expire_all()
    assert _manual_order_state(db_session, order_id=order_id) == before


def test_resolve_operation_lifecycle_failure_preserves_every_surface(db_session) -> None:
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    operation = db_session.get(BillingOperation, operation_id)
    operation.status = "completed"
    operation.completion_kind = "rejected"
    operation.completed_at = datetime(2026, 8, 29, 12, 1, tzinfo=UTC)
    operation.released_credits = Decimal("30000")
    db_session.commit()
    _assert_resolve_failure_preserves_every_surface(db_session, order_id=order_id)


def test_resolve_duplicate_usage_failure_preserves_every_surface(db_session) -> None:
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    usage = db_session.scalar(
        select(UsageRecord).where(UsageRecord.billing_operation_id == operation_id)
    )
    db_session.add(
        UsageRecord(
            tenant_id=usage.tenant_id,
            subscription_id=usage.subscription_id,
            billing_operation_id=operation_id,
            billing_item_index=1,
            billing_pricing_line_index=0,
            capability=usage.capability,
            provider=usage.provider,
            model=usage.model,
            unit=usage.unit,
            quantity=usage.quantity,
            credits=usage.credits,
            cost_cents=0,
            status="reserved",
        )
    )
    db_session.commit()
    _assert_resolve_failure_preserves_every_surface(db_session, order_id=order_id)


def test_resolve_usage_state_failure_preserves_every_surface(db_session) -> None:
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    usage = db_session.scalar(
        select(UsageRecord).where(UsageRecord.billing_operation_id == operation_id)
    )
    usage.status = "released"
    usage.settled_at = datetime(2026, 8, 29, 12, 1, tzinfo=UTC)
    db_session.commit()
    _assert_resolve_failure_preserves_every_surface(db_session, order_id=order_id)


def test_resolve_usage_tenant_association_failure_preserves_every_surface(
    db_session,
) -> None:
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    db_session.add(Tenant(id="invariant-other-tenant", slug="invariant-other", name="Other"))
    db_session.flush()
    usage = db_session.scalar(
        select(UsageRecord).where(UsageRecord.billing_operation_id == operation_id)
    )
    usage.tenant_id = "invariant-other-tenant"
    db_session.commit()
    _assert_resolve_failure_preserves_every_surface(db_session, order_id=order_id)


def test_resolve_order_operation_link_failure_preserves_every_surface(db_session) -> None:
    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    unrelated = BillingOperation(
        tenant_id="tenant-a",
        user_id="user-a",
        operation="doubao_brand_voice_order_create",
        idempotency_key="00000000-0000-0000-0000-000000000101",
        request_hash="c" * 64,
        quote_hash="d" * 64,
        pricing_snapshot={},
        requested_credits=30_000,
        settled_credits=0,
        released_credits=0,
        status="in_progress",
    )
    db_session.add(unrelated)
    db_session.flush()
    db_session.get(BrandVoiceOrder, order_id).billing_operation_id = unrelated.id
    db_session.commit()

    before = _manual_order_state_for_orphaned_order(db_session, order_id=order_id)
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import resolve_brand_voice_order

    with pytest.raises(BillingInvariantError):
        resolve_brand_voice_order(
            db_session,
            actor=db_session.get(User, "user-a"),
            order_id=order_id,
            action="reject",
            rejection_reason="orphaned operation",
        )
    db_session.expire_all()
    assert _manual_order_state_for_orphaned_order(db_session, order_id=order_id) == before


def _manual_order_state_for_orphaned_order(db_session, *, order_id: str) -> tuple[object, ...]:
    order = db_session.get(BrandVoiceOrder, order_id)
    return (
        order.status,
        order.billing_operation_id,
        tuple(
            db_session.execute(
                select(
                    BillingOperation.id,
                    BillingOperation.status,
                    BillingOperation.requested_credits,
                ).order_by(BillingOperation.id)
            )
        ),
        tuple(
            db_session.execute(
                select(
                    UsageRecord.id,
                    UsageRecord.billing_operation_id,
                    UsageRecord.status,
                ).order_by(UsageRecord.id)
            )
        ),
        tuple(
            db_session.execute(
                select(
                    Subscription.id,
                    Subscription.quota_credits_used,
                    Subscription.quota_credits_reserved,
                ).order_by(Subscription.id)
            )
        ),
        db_session.scalar(select(func.count()).select_from(CreditRefundGrant)),
        db_session.scalar(select(func.count()).select_from(BrandVoiceProviderId)),
        db_session.scalar(select(func.count()).select_from(AdminAuditLog)),
    )


def test_resolve_pricing_snapshot_failure_preserves_every_surface(db_session) -> None:
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    db_session.get(BillingOperation, operation_id).pricing_snapshot = {}
    db_session.commit()
    _assert_resolve_failure_preserves_every_surface(db_session, order_id=order_id)


def test_resolve_requested_amount_failure_preserves_every_surface(db_session) -> None:
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    db_session.get(BillingOperation, operation_id).requested_credits = Decimal("30001")
    db_session.commit()
    _assert_resolve_failure_preserves_every_surface(db_session, order_id=order_id)


def test_resolve_source_hold_failure_preserves_every_surface(db_session) -> None:
    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    db_session.get(Subscription, "subscription-a").quota_credits_reserved = 29_999
    db_session.commit()
    _assert_resolve_failure_preserves_every_surface(db_session, order_id=order_id)


def test_resolve_decimal_parent_reservation_failure_preserves_every_surface(
    db_session,
) -> None:
    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    operation = BillingOperation(
        tenant_id="tenant-a",
        user_id="user-a",
        operation="avatar_generate",
        idempotency_key="00000000-0000-0000-0000-000000000102",
        request_hash="e" * 64,
        quote_hash="f" * 64,
        pricing_snapshot={},
        requested_credits=Decimal("0.5"),
        settled_credits=0,
        released_credits=0,
        status="in_progress",
    )
    db_session.add(operation)
    db_session.flush()
    db_session.add(
        UsageRecord(
            tenant_id="tenant-a",
            subscription_id="subscription-a",
            billing_operation_id=operation.id,
            billing_item_index=0,
            billing_pricing_line_index=0,
            capability="avatar",
            provider="apimart",
            model="decimal-parent",
            unit="call",
            quantity=Decimal("1"),
            credits=Decimal("0.5"),
            cost_cents=0,
            status="reserved",
        )
    )
    db_session.commit()
    _assert_resolve_failure_preserves_every_surface(db_session, order_id=order_id)


def test_resolve_reconciliation_counts_parent_once_and_ceils_legacy_rows(
    db_session,
) -> None:
    from app.services.brand_voice_orders import resolve_brand_voice_order

    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    operation = BillingOperation(
        tenant_id="tenant-a",
        user_id="user-a",
        operation="avatar_generate",
        idempotency_key="00000000-0000-0000-0000-000000000103",
        request_hash="g" * 64,
        quote_hash="h" * 64,
        pricing_snapshot={},
        requested_credits=Decimal("10"),
        settled_credits=0,
        released_credits=0,
        status="in_progress",
    )
    db_session.add(operation)
    db_session.flush()
    for index in (0, 1):
        db_session.add(
            UsageRecord(
                tenant_id="tenant-a",
                subscription_id="subscription-a",
                billing_operation_id=operation.id,
                billing_item_index=index,
                billing_pricing_line_index=0,
                capability="avatar",
                provider="apimart",
                model="shared-parent",
                unit="call",
                quantity=Decimal("1"),
                credits=Decimal("5"),
                cost_cents=0,
                status="reserved",
            )
        )
    db_session.add(
        UsageRecord(
            tenant_id="tenant-a",
            subscription_id="subscription-a",
            capability="avatar",
            provider="legacy",
            model="legacy-reservation",
            unit="call",
            quantity=Decimal("1"),
            credits=Decimal("1.2"),
            cost_cents=0,
            status="reserved",
        )
    )
    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_reserved = 30_012
    db_session.commit()

    resolved = resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="reject",
        rejection_reason="valid reconciliation",
        now=datetime(2026, 8, 29, 12, 2, tzinfo=UTC),
    )

    assert resolved.status == "rejected"
    db_session.expire_all()
    assert db_session.get(Subscription, "subscription-a").quota_credits_reserved == 12
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(UsageRecord)
            .where(UsageRecord.status == "reserved")
        )
        == 3
    )


def test_resolve_registry_readiness_failure_preserves_every_surface(
    db_session,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry

    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    monkeypatch.setattr(provider_voice_registry.settings, "environment", "production")
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["invariant-official"],
    )
    provider_voice_registry.register_official_provider_voice_ids(
        db_session,
        provider_voice_ids=["invariant-official"],
    )
    db_session.add(
        ProviderConfig(
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={"speaker_ids": ["unknown-invariant-history"]},
            is_active=False,
        )
    )
    db_session.commit()
    _assert_resolve_failure_preserves_every_surface(
        db_session,
        order_id=order_id,
        expected_exception=AppError,
        expected_code="DOUBAO_REGISTRY_NOT_READY",
    )


def test_doubao_order_only_freezes_and_does_not_create_brand_voice(db_session):
    from app.services.brand_voice_orders import (
        create_brand_voice_order,
        list_user_brand_voice_orders,
    )

    user = db_session.get(User, "user-a")
    subscription = db_session.get(Subscription, "subscription-a")
    db_session.get(Plan, subscription.plan_id).code = "huading"
    subscription.quota_credits_total = 100_000
    asset = Asset(
        id="audio-a",
        tenant_id=user.tenant_id,
        type="audio",
        source="upload",
        storage_key=f"tenants/{user.tenant_id}/uploads/audio-a.wav",
        mime_type="audio/wav",
        duration_ms=10_000,
        status="ready",
        metadata_={"original_filename": "voice.wav"},
    )
    db_session.add(asset)
    db_session.commit()
    payload = BrandVoiceOrderCreateRequest(
        order_type="create",
        requested_name="客服音色",
        source_audio_asset_id=asset.id,
        consent_confirmed=True,
    )

    order = create_brand_voice_order(
        db_session,
        user=user,
        payload=payload,
        verified_quote=_verified_order_quote(db_session, user=user, payload=payload),
        submission_headers=BillingSubmissionHeaders(idempotency_key=uuid4(), quote_token="quoted"),
        now=datetime(2026, 8, 29, tzinfo=UTC),
    )
    db_session.commit()

    assert order.status == "awaiting_fulfillment"
    assert order.user_id == user.id
    assert db_session.scalar(select(func.count()).select_from(BrandVoice)) == 0
    assert db_session.scalar(select(func.count()).select_from(BrandVoiceOrder)) == 1
    db_session.refresh(subscription)
    assert subscription.quota_credits_reserved == 30_000
    other_user = User(
        id="user-b",
        tenant_id=user.tenant_id,
        email="billing-b@example.com",
        password_hash="hash",
        role="creator",
    )
    db_session.add(other_user)
    db_session.commit()
    assert (
        list_user_brand_voice_orders(
            db_session,
            tenant_id=user.tenant_id,
            user_id=other_user.id,
        )
        == []
    )
    from app.services.asset_retention import (
        assert_asset_not_held_by_manual_order,
        assert_no_awaiting_orders_for_principal,
    )

    with pytest.raises(AppError) as asset_hold:
        assert_asset_not_held_by_manual_order(db_session, asset_id=asset.id)
    assert asset_hold.value.code == "BRAND_VOICE_ORDER_NOT_CANCELLABLE"
    with pytest.raises(AppError) as principal_hold:
        assert_no_awaiting_orders_for_principal(
            db_session,
            tenant_id=user.tenant_id,
        )
    assert principal_hold.value.code == "BRAND_VOICE_ORDER_NOT_CANCELLABLE"


def test_estimate_returns_fixed_30000_quote(auth_context, auth_db):
    with auth_db() as db:
        subscription = db.get(Subscription, db.scalar(select(Subscription.id)))
        db.get(Plan, subscription.plan_id).code = "huading"
        db.add(
            Asset(
                id="estimate-audio",
                tenant_id=auth_context["tenant_id"],
                type="audio",
                source="upload",
                storage_key=f"tenants/{auth_context['tenant_id']}/uploads/estimate.wav",
                mime_type="audio/wav",
                duration_ms=10_000,
                status="ready",
            )
        )
        db.commit()

    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).post(
        "/api/v1/brand-voice-orders/estimate",
        headers=auth_context["headers"],
        json={
            "order_type": "create",
            "requested_name": "客服音色",
            "source_audio_asset_id": "estimate-audio",
            "consent_confirmed": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["payable_credits"] == 30_000


def test_renewal_estimate_rejects_reusing_the_voice_source_audio(auth_context, auth_db):
    from fastapi.testclient import TestClient

    from app.main import app

    now = datetime.now(UTC)
    with auth_db() as db:
        subscription = db.scalar(select(Subscription))
        db.get(Plan, subscription.plan_id).code = "huading"
        asset = Asset(
            id="renew-reused-audio",
            tenant_id=auth_context["tenant_id"],
            type="audio",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/reused.wav",
            mime_type="audio/wav",
            duration_ms=10_000,
            status="ready",
        )
        voice = BrandVoice(
            id="renew-reused-voice",
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Expired voice",
            source_audio_asset_id=asset.id,
            provider="doubao-voice-clone",
            speaker_id="renew-reused-provider",
            status="ready",
            consent_confirmed=True,
            expires_at=now - timedelta(days=1),
        )
        db.add_all([asset, voice])
        db.commit()

    response = TestClient(app).post(
        "/api/v1/brand-voice-orders/estimate",
        headers=auth_context["headers"],
        json={
            "order_type": "renew",
            "requested_name": "Renewed voice",
            "source_audio_asset_id": "renew-reused-audio",
            "consent_confirmed": True,
            "existing_brand_voice_id": "renew-reused-voice",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BRAND_VOICE_RENEWAL_SOURCE_AUDIO_REUSED"


def test_renewal_submit_revalidates_source_audio_reuse_after_estimate(
    auth_context,
    auth_db,
) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    now = datetime.now(UTC)
    with auth_db() as db:
        subscription = db.scalar(select(Subscription))
        db.get(Plan, subscription.plan_id).code = "huading"
        subscription.quota_credits_total = 100_000
        old_asset = Asset(
            id="renew-old-audio",
            tenant_id=auth_context["tenant_id"],
            type="audio",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/old.wav",
            mime_type="audio/wav",
            duration_ms=10_000,
            status="ready",
        )
        candidate = Asset(
            id="renew-candidate-audio",
            tenant_id=auth_context["tenant_id"],
            type="audio",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/candidate.wav",
            mime_type="audio/wav",
            duration_ms=10_000,
            status="ready",
        )
        voice = BrandVoice(
            id="renew-revalidation-voice",
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Expired voice",
            source_audio_asset_id=old_asset.id,
            provider="doubao-voice-clone",
            speaker_id="renew-revalidation-provider",
            status="ready",
            consent_confirmed=True,
            expires_at=now - timedelta(days=1),
        )
        db.add_all([old_asset, candidate, voice])
        db.commit()

    client = TestClient(app)
    payload = {
        "order_type": "renew",
        "requested_name": "Renewed voice",
        "source_audio_asset_id": "renew-candidate-audio",
        "consent_confirmed": True,
        "existing_brand_voice_id": "renew-revalidation-voice",
    }
    quote = client.post(
        "/api/v1/brand-voice-orders/estimate",
        headers=auth_context["headers"],
        json=payload,
    )
    assert quote.status_code == 200
    with auth_db() as db:
        db.get(BrandVoice, "renew-revalidation-voice").source_audio_asset_id = (
            "renew-candidate-audio"
        )
        db.commit()

    response = client.post(
        "/api/v1/brand-voice-orders",
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote.json()["data"]["quote_token"],
        },
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BRAND_VOICE_RENEWAL_SOURCE_AUDIO_REUSED"
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BrandVoiceOrder)) == 0


def test_submit_creates_awaiting_order_with_reserved_billing(auth_context, auth_db):
    with auth_db() as db:
        subscription = db.scalar(select(Subscription))
        db.get(Plan, subscription.plan_id).code = "huading"
        db.add(
            Asset(
                id="submit-audio",
                tenant_id=auth_context["tenant_id"],
                type="audio",
                source="upload",
                storage_key=f"tenants/{auth_context['tenant_id']}/uploads/submit.wav",
                mime_type="audio/wav",
                duration_ms=10_000,
                status="ready",
            )
        )
        db.commit()

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    payload = {
        "order_type": "create",
        "requested_name": "客服音色",
        "source_audio_asset_id": "submit-audio",
        "consent_confirmed": True,
    }
    quote = client.post(
        "/api/v1/brand-voice-orders/estimate",
        headers=auth_context["headers"],
        json=payload,
    ).json()["data"]

    idempotency_key = str(uuid4())
    headers = {
        **auth_context["headers"],
        "Idempotency-Key": idempotency_key,
        "X-Huading-Quote": quote["quote_token"],
    }
    response = client.post(
        "/api/v1/brand-voice-orders",
        headers=headers,
        json=payload,
    )
    replay = client.post(
        "/api/v1/brand-voice-orders",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 201
    assert replay.status_code == 201
    resource = response.json()["data"]
    assert replay.json()["data"]["id"] == resource["id"]
    assert resource["status"] == "awaiting_fulfillment"
    assert resource["billing"]["status"] == "reserved"
    assert resource["billing"]["held_credits"] == 30_000
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BrandVoice)) == 0
        assert db.scalar(select(func.count()).select_from(BrandVoiceOrder)) == 1
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 1
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 1
        subscription = db.scalar(select(Subscription))
        assert subscription.quota_credits_reserved == 30_000


def test_submit_fails_closed_when_balance_changed_after_quote(auth_context, auth_db):
    with auth_db() as db:
        subscription = db.scalar(select(Subscription))
        db.get(Plan, subscription.plan_id).code = "huading"
        subscription.quota_credits_total = 29_999
        db.add(
            Asset(
                id="insufficient-audio",
                tenant_id=auth_context["tenant_id"],
                type="audio",
                source="upload",
                storage_key=f"tenants/{auth_context['tenant_id']}/uploads/insufficient.wav",
                mime_type="audio/wav",
                duration_ms=10_000,
                status="ready",
            )
        )
        db.commit()

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    payload = {
        "order_type": "create",
        "requested_name": "Insufficient balance",
        "source_audio_asset_id": "insufficient-audio",
        "consent_confirmed": True,
    }
    quote = client.post(
        "/api/v1/brand-voice-orders/estimate",
        headers=auth_context["headers"],
        json=payload,
    )
    assert quote.status_code == 200

    response = client.post(
        "/api/v1/brand-voice-orders",
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote.json()["data"]["quote_token"],
        },
        json=payload,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_QUOTA_EXCEEDED"
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BrandVoiceOrder)) == 0
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        assert db.scalar(select(Subscription)).quota_credits_reserved == 0


def test_fulfill_settles_and_starts_payment_user_voice_for_365_days(db_session):
    from app.services.brand_voice_orders import (
        create_brand_voice_order,
        resolve_brand_voice_order,
    )

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    user = db_session.get(User, "user-a")
    subscription = db_session.get(Subscription, "subscription-a")
    db_session.get(Plan, subscription.plan_id).code = "huading"
    subscription.quota_credits_total = 100_000
    asset = Asset(
        id="fulfill-audio",
        tenant_id=user.tenant_id,
        type="audio",
        source="upload",
        storage_key=f"tenants/{user.tenant_id}/uploads/fulfill.wav",
        mime_type="audio/wav",
        duration_ms=10_000,
        status="ready",
    )
    db_session.add(asset)
    db_session.commit()
    payload = BrandVoiceOrderCreateRequest(
        order_type="create",
        requested_name="交付音色",
        source_audio_asset_id=asset.id,
        consent_confirmed=True,
    )
    order = create_brand_voice_order(
        db_session,
        user=user,
        payload=payload,
        verified_quote=_verified_order_quote(db_session, user=user, payload=payload),
        submission_headers=BillingSubmissionHeaders(idempotency_key=uuid4(), quote_token="quoted"),
        now=now,
    )
    db_session.commit()

    resolved = resolve_brand_voice_order(
        db_session,
        actor=user,
        order_id=order.id,
        action="fulfill",
        provider_voice_id="customer-voice-001",
        now=now,
    )

    assert resolved.status == "fulfilled"
    voice = db_session.get(BrandVoice, resolved.fulfilled_brand_voice_id)
    assert voice.owner_user_id == user.id
    assert voice.activated_at.replace(tzinfo=UTC) == now
    assert voice.expires_at.replace(tzinfo=UTC) == now + timedelta(days=365)
    db_session.refresh(subscription)
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 30_000
    # The production schema intentionally retains a RESTRICT audit cycle between
    # a fulfilled order and its permanent provider-ID row. SQLite cannot DROP
    # that cycle even though the FK uses use_alter for PostgreSQL.
    db_session.commit()
    db_session.close()
    with db_session.get_bind().connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")


def test_reject_releases_hold_and_conflicting_replay_changes_nothing(db_session):
    from app.services.brand_voice_orders import (
        create_brand_voice_order,
        resolve_brand_voice_order,
    )

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    user = db_session.get(User, "user-a")
    subscription = db_session.get(Subscription, "subscription-a")
    db_session.get(Plan, subscription.plan_id).code = "huading"
    subscription.quota_credits_total = 100_000
    db_session.add(
        Asset(
            id="reject-audio",
            tenant_id=user.tenant_id,
            type="audio",
            source="upload",
            storage_key=f"tenants/{user.tenant_id}/uploads/reject.wav",
            mime_type="audio/wav",
            duration_ms=10_000,
            status="ready",
        )
    )
    db_session.commit()
    payload = BrandVoiceOrderCreateRequest(
        order_type="create",
        requested_name="拒绝音色",
        source_audio_asset_id="reject-audio",
        consent_confirmed=True,
    )
    order = create_brand_voice_order(
        db_session,
        user=user,
        payload=payload,
        verified_quote=_verified_order_quote(db_session, user=user, payload=payload),
        submission_headers=BillingSubmissionHeaders(idempotency_key=uuid4(), quote_token="quoted"),
        now=now,
    )
    db_session.commit()

    rejected = resolve_brand_voice_order(
        db_session,
        actor=user,
        order_id=order.id,
        action="reject",
        rejection_reason="音频质量不足",
        now=now,
    )
    assert rejected.status == "rejected"
    db_session.refresh(subscription)
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 0

    replay = resolve_brand_voice_order(
        db_session,
        actor=user,
        order_id=order.id,
        action="reject",
        rejection_reason="音频质量不足",
        now=now + timedelta(days=1),
    )
    assert replay.id == rejected.id
    with pytest.raises(AppError) as captured:
        resolve_brand_voice_order(
            db_session,
            actor=user,
            order_id=order.id,
            action="fulfill",
            provider_voice_id="different-provider-id",
            now=now + timedelta(days=1),
        )
    assert captured.value.code == "BRAND_VOICE_ORDER_ALREADY_RESOLVED"
    db_session.refresh(subscription)
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 0


def test_cross_period_reject_moves_refund_to_current_subscription(db_session) -> None:
    from app.services.brand_voice_orders import resolve_brand_voice_order

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    source = db_session.get(Subscription, "subscription-a")
    source.status = "expired"
    source.period_end = now - timedelta(seconds=1)
    current = Subscription(
        id="subscription-current",
        tenant_id="tenant-a",
        plan_id=source.plan_id,
        status="active",
        period_start=now,
        period_end=now + timedelta(days=30),
        quota_credits_total=100_000,
        quota_credits_used=0,
        quota_credits_reserved=0,
    )
    db_session.add(current)
    db_session.commit()

    rejected = resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="reject",
        rejection_reason="cross-period rejection",
        now=now,
    )

    assert rejected.status == "rejected"
    db_session.expire_all()
    assert db_session.get(Subscription, source.id).quota_credits_reserved == 0
    assert db_session.get(Subscription, current.id).quota_credits_total == 130_000
    grant = db_session.scalar(
        select(CreditRefundGrant).where(
            CreditRefundGrant.billing_operation_id == operation_id
        )
    )
    assert grant.status == "applied"
    assert grant.source_subscription_id == source.id
    assert grant.target_subscription_id == current.id
