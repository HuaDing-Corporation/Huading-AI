from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.deps import BillingSubmissionHeaders
from app.core.exceptions import AppError
from app.db.models import Asset, BrandVoice, BrandVoiceOrder, Plan, Subscription, User
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

    response = client.post(
        "/api/v1/brand-voice-orders",
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote["quote_token"],
        },
        json=payload,
    )

    assert response.status_code == 201
    resource = response.json()["data"]
    assert resource["status"] == "awaiting_fulfillment"
    assert resource["billing"]["status"] == "reserved"
    assert resource["billing"]["held_credits"] == 30_000
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BrandVoice)) == 0


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
