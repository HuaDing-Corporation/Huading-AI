from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text

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
    VideoTask,
)
from app.schemas.brand_voice_orders import BrandVoiceOrderCreateRequest
from app.services.billing_quotes import VerifiedQuote, _validated_snapshot
from app.services.pricing import PRICING_POLICIES, build_simple_pricing, resolve_rate


@pytest.fixture(autouse=True)
def _configure_service_level_platform_admin(db_session, monkeypatch) -> None:
    """Service tests explicitly supply the request layer's authorized principal."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "engine_platform_tenant_slugs", {"billing-a"})
    actor = db_session.get(User, "user-a")
    actor.role = "admin"
    db_session.commit()


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


def _seed_manual_order_for_invariant_test(
    db_session,
    *,
    suffix: str = "",
) -> tuple[str, str]:
    from app.services.brand_voice_orders import create_brand_voice_order

    user = db_session.get(User, "user-a")
    subscription = db_session.get(Subscription, "subscription-a")
    db_session.get(Plan, subscription.plan_id).code = "huading"
    subscription.quota_credits_total = 100_000
    asset = Asset(
        id=f"invariant-audio{suffix}",
        tenant_id=user.tenant_id,
        type="audio",
        source="upload",
        storage_key=f"tenants/{user.tenant_id}/uploads/invariant{suffix}.wav",
        mime_type="audio/wav",
        duration_ms=10_000,
        status="ready",
    )
    db_session.add(asset)
    db_session.commit()
    payload = BrandVoiceOrderCreateRequest(
        order_type="create",
        requested_name=f"Invariant voice{suffix}",
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


def _seed_cross_period_refund_grant(db_session, *, suffix: str):
    from app.services.brand_voice_orders import resolve_brand_voice_order

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix=suffix,
    )
    source = db_session.get(Subscription, "subscription-a")
    source.status = "expired"
    source.period_end = now - timedelta(seconds=1)
    current = Subscription(
        id=f"refund-current{suffix}",
        tenant_id=source.tenant_id,
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
    resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="reject",
        rejection_reason=f"refund invariant {suffix}",
        now=now,
    )
    db_session.expire_all()
    grant = db_session.scalar(
        select(CreditRefundGrant).where(CreditRefundGrant.billing_operation_id == operation_id)
    )
    return order_id, operation_id, source, current, grant


def _seed_invariant_dependencies(db_session) -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    db_session.add(
        Tenant(
            id="invariant-other-tenant",
            slug="invariant-other-tenant",
            name="Invariant Other Tenant",
        )
    )
    db_session.flush()
    db_session.add(
        User(
            id="invariant-other-user",
            tenant_id="tenant-a",
            email="invariant-other@example.com",
            password_hash="hash",
            role="creator",
        )
    )
    db_session.add(
        Subscription(
            id="invariant-other-subscription",
            tenant_id="tenant-a",
            plan_id="plan-a",
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            quota_credits_total=100_000,
            quota_credits_used=0,
            quota_credits_reserved=0,
        )
    )
    db_session.add_all(
        [
            Asset(
                id="invariant-other-audio",
                tenant_id="tenant-a",
                type="audio",
                source="upload",
                storage_key="invariant/other.wav",
                mime_type="audio/wav",
                duration_ms=10_000,
                status="ready",
            ),
            Asset(
                id="invariant-other-tenant-audio",
                tenant_id="invariant-other-tenant",
                type="audio",
                source="upload",
                storage_key="invariant/other-tenant.wav",
                mime_type="audio/wav",
                duration_ms=10_000,
                status="ready",
            ),
            VideoTask(
                id="invariant-video-task",
                tenant_id="tenant-a",
                status="queued",
            ),
        ]
    )
    db_session.flush()
    db_session.add(
        BrandVoice(
            id="invariant-existing-voice",
            tenant_id="tenant-a",
            owner_user_id="user-a",
            name="Invariant existing",
            source_audio_asset_id="invariant-other-audio",
            provider="doubao-voice-clone",
            speaker_id="invariant-existing-provider",
            status="ready",
            consent_confirmed=True,
            expires_at=now - timedelta(days=1),
        )
    )
    db_session.add(
        BillingOperation(
            id="invariant-unrelated-operation",
            tenant_id="tenant-a",
            user_id="user-a",
            operation="avatar_generate",
            idempotency_key="00000000-0000-0000-0000-000000000111",
            request_hash="1" * 64,
            quote_hash="2" * 64,
            pricing_snapshot={},
            requested_credits=1,
            settled_credits=0,
            released_credits=0,
            status="in_progress",
        )
    )
    db_session.commit()


def _capture_resolve_locator(db_session, *, order_id: str):
    from app.services.brand_voice_orders import _resolve_locator

    return _resolve_locator(db_session, order_id=order_id)


def _update_ignoring_sqlite_checks(db_session, statement: str, parameters: dict) -> None:
    db_session.execute(text("PRAGMA ignore_check_constraints=ON"))
    db_session.execute(text(statement), parameters)
    db_session.commit()
    db_session.execute(text("PRAGMA ignore_check_constraints=OFF"))
    db_session.commit()
    db_session.expire_all()


def _delete_asset_ignoring_sqlite_fk(db_session, *, asset_id: str) -> None:
    db_session.commit()
    engine = db_session.get_bind()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(text("DELETE FROM assets WHERE id = :asset_id"), {"asset_id": asset_id})
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    db_session.expire_all()


def _manual_order_state(db_session, *, order_id: str) -> dict[str, tuple[tuple, ...]]:
    assert db_session.get(BrandVoiceOrder, order_id) is not None

    def rows(statement) -> tuple[tuple, ...]:
        return deepcopy(tuple(tuple(row) for row in db_session.execute(statement)))

    return {
        "orders": rows(
            select(
                BrandVoiceOrder.id,
                BrandVoiceOrder.tenant_id,
                BrandVoiceOrder.user_id,
                BrandVoiceOrder.order_type,
                BrandVoiceOrder.requested_name,
                BrandVoiceOrder.source_audio_asset_id,
                BrandVoiceOrder.source_metadata_snapshot,
                BrandVoiceOrder.consent_confirmed_at,
                BrandVoiceOrder.existing_brand_voice_id,
                BrandVoiceOrder.billing_operation_id,
                BrandVoiceOrder.status,
                BrandVoiceOrder.fulfilled_brand_voice_id,
                BrandVoiceOrder.fulfilled_provider_id,
                BrandVoiceOrder.resolver_user_id,
                BrandVoiceOrder.fulfilled_at,
                BrandVoiceOrder.rejected_at,
                BrandVoiceOrder.rejection_reason,
                BrandVoiceOrder.created_at,
                BrandVoiceOrder.updated_at,
            ).order_by(BrandVoiceOrder.id)
        ),
        "operations": rows(
            select(
                BillingOperation.id,
                BillingOperation.tenant_id,
                BillingOperation.user_id,
                BillingOperation.operation,
                BillingOperation.idempotency_key,
                BillingOperation.request_hash,
                BillingOperation.quote_hash,
                BillingOperation.pricing_snapshot,
                BillingOperation.requested_credits,
                BillingOperation.settled_credits,
                BillingOperation.released_credits,
                BillingOperation.status,
                BillingOperation.completion_kind,
                BillingOperation.completed_at,
                BillingOperation.result_type,
                BillingOperation.result_id,
                BillingOperation.result_payload,
                BillingOperation.error_code,
                BillingOperation.error_http_status,
                BillingOperation.error_payload,
                BillingOperation.created_at,
                BillingOperation.updated_at,
            ).order_by(BillingOperation.id)
        ),
        "usages": rows(
            select(
                UsageRecord.id,
                UsageRecord.tenant_id,
                UsageRecord.subscription_id,
                UsageRecord.video_task_id,
                UsageRecord.reverse_prompt_job_id,
                UsageRecord.chat_message_id,
                UsageRecord.billing_operation_id,
                UsageRecord.billing_item_index,
                UsageRecord.billing_pricing_line_index,
                UsageRecord.capability,
                UsageRecord.provider,
                UsageRecord.model,
                UsageRecord.unit,
                UsageRecord.quantity,
                UsageRecord.credits,
                UsageRecord.cost_cents,
                UsageRecord.provider_cost_usd,
                UsageRecord.provider_usage,
                UsageRecord.currency,
                UsageRecord.status,
                UsageRecord.created_at,
                UsageRecord.settled_at,
            ).order_by(UsageRecord.id)
        ),
        "subscriptions": rows(
            select(
                Subscription.id,
                Subscription.tenant_id,
                Subscription.plan_id,
                Subscription.status,
                Subscription.period_start,
                Subscription.period_end,
                Subscription.quota_credits_total,
                Subscription.quota_credits_used,
                Subscription.quota_credits_reserved,
                Subscription.created_at,
                Subscription.updated_at,
            ).order_by(Subscription.id)
        ),
        "refund_grants": rows(
            select(
                CreditRefundGrant.id,
                CreditRefundGrant.billing_operation_id,
                CreditRefundGrant.tenant_id,
                CreditRefundGrant.user_id,
                CreditRefundGrant.source_subscription_id,
                CreditRefundGrant.target_subscription_id,
                CreditRefundGrant.amount_credits,
                CreditRefundGrant.status,
                CreditRefundGrant.created_at,
                CreditRefundGrant.applied_at,
            ).order_by(CreditRefundGrant.id)
        ),
        "provider_registry": rows(
            select(
                BrandVoiceProviderId.id,
                BrandVoiceProviderId.provider,
                BrandVoiceProviderId.normalized_provider_id,
                BrandVoiceProviderId.kind,
                BrandVoiceProviderId.brand_voice_id,
                BrandVoiceProviderId.first_order_id,
                BrandVoiceProviderId.status,
                BrandVoiceProviderId.created_at,
                BrandVoiceProviderId.updated_at,
            ).order_by(BrandVoiceProviderId.id)
        ),
        "provider_configs": rows(
            select(
                ProviderConfig.id,
                ProviderConfig.tenant_id,
                ProviderConfig.capability,
                ProviderConfig.provider,
                ProviderConfig.config,
                ProviderConfig.is_active,
            ).order_by(ProviderConfig.id)
        ),
        "audit_logs": rows(
            select(
                AdminAuditLog.id,
                AdminAuditLog.actor_user_id,
                AdminAuditLog.actor_tenant_id,
                AdminAuditLog.action,
                AdminAuditLog.target_tenant_id,
                AdminAuditLog.target_id,
                AdminAuditLog.before,
                AdminAuditLog.after,
                AdminAuditLog.reason,
                AdminAuditLog.created_at,
            ).order_by(AdminAuditLog.id)
        ),
        "brand_voices": rows(
            select(
                BrandVoice.id,
                BrandVoice.tenant_id,
                BrandVoice.owner_user_id,
                BrandVoice.name,
                BrandVoice.source_audio_asset_id,
                BrandVoice.provider,
                BrandVoice.speaker_id,
                BrandVoice.status,
                BrandVoice.consent_confirmed,
                BrandVoice.consent_confirmed_at,
                BrandVoice.activated_at,
                BrandVoice.expires_at,
                BrandVoice.error_code,
                BrandVoice.error_message,
                BrandVoice.created_at,
                BrandVoice.updated_at,
                BrandVoice.deleted_at,
            ).order_by(BrandVoice.id)
        ),
        "assets": rows(
            select(
                Asset.id,
                Asset.tenant_id,
                Asset.type,
                Asset.source,
                Asset.provider,
                Asset.storage_key,
                Asset.mime_type,
                Asset.size_bytes,
                Asset.duration_ms,
                Asset.width,
                Asset.height,
                Asset.status,
                Asset.metadata_,
                Asset.created_at,
                Asset.deleted_at,
            ).order_by(Asset.id)
        ),
    }


def test_manual_order_state_snapshot_captures_every_resolve_surface(db_session) -> None:
    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)

    state = _manual_order_state(db_session, order_id=order_id)

    assert isinstance(state, dict)
    assert set(state) == {
        "orders",
        "operations",
        "usages",
        "subscriptions",
        "refund_grants",
        "provider_registry",
        "provider_configs",
        "audit_logs",
        "brand_voices",
        "assets",
    }


@pytest.mark.parametrize("terminal_replay", [False, True])
def test_resolve_revalidates_revoked_platform_admin_before_replay_or_mutation(
    db_session,
    monkeypatch,
    terminal_replay: bool,
) -> None:
    from app.core.config import settings
    from app.services.brand_voice_orders import resolve_brand_voice_order

    monkeypatch.setattr(settings, "engine_platform_tenant_slugs", {"billing-a"})
    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    actor = db_session.get(User, "user-a")
    actor.role = "admin"
    db_session.commit()
    if terminal_replay:
        resolve_brand_voice_order(
            db_session,
            actor=actor,
            order_id=order_id,
            action="reject",
            rejection_reason="terminal before revocation",
            now=datetime(2026, 8, 29, 12, 1, tzinfo=UTC),
        )
        db_session.commit()
    stale_authenticated_actor = SimpleNamespace(
        id=actor.id,
        tenant_id=actor.tenant_id,
        role="admin",
        is_active=True,
    )
    actor.role = "creator"
    db_session.commit()
    before = _manual_order_state(db_session, order_id=order_id)

    with pytest.raises(AppError) as captured:
        resolve_brand_voice_order(
            db_session,
            actor=stale_authenticated_actor,
            order_id=order_id,
            action="reject",
            rejection_reason=(
                "terminal before revocation"
                if terminal_replay
                else "must not mutate after revocation"
            ),
            now=datetime(2026, 8, 29, 12, 2, tzinfo=UTC),
        )

    assert captured.value.code == "PLATFORM_ADMIN_REQUIRED"
    db_session.expire_all()
    assert db_session.get(User, "user-a").role == "creator"
    assert _manual_order_state(db_session, order_id=order_id) == before


def _assert_resolve_failure_preserves_every_surface(
    db_session,
    *,
    order_id: str,
    locator=None,
    action: str = "fulfill",
    expected_exception: type[Exception] | None = None,
    expected_code: str = "BILLING_INVARIANT_VIOLATION",
) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import (
        _resolve_in_transaction,
        resolve_brand_voice_order,
    )

    before = _manual_order_state(db_session, order_id=order_id)
    with pytest.raises(expected_exception or BillingInvariantError) as captured:
        if locator is None:
            resolve_brand_voice_order(
                db_session,
                actor=db_session.get(User, "user-a"),
                order_id=order_id,
                action=action,
                provider_voice_id="invariant-provider-id" if action == "fulfill" else None,
                rejection_reason="invalid invariant" if action == "reject" else None,
                now=datetime(2026, 8, 29, 12, 2, tzinfo=UTC),
            )
        else:
            _resolve_in_transaction(
                db_session,
                locator=locator,
                actor_id="user-a",
                actor_tenant_id="tenant-a",
                action=action,
                provider_voice_id="invariant-provider-id" if action == "fulfill" else None,
                rejection_reason="invalid invariant" if action == "reject" else None,
                transaction_now=datetime(2026, 8, 29, 12, 2, tzinfo=UTC),
            )
    assert captured.value.code == expected_code
    db_session.rollback()
    db_session.expire_all()
    assert _manual_order_state(db_session, order_id=order_id) == before


def test_resolve_operation_db_coupled_lifecycle_failure_preserves_every_surface(
    db_session,
) -> None:
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
            select(func.count()).select_from(UsageRecord).where(UsageRecord.status == "reserved")
        )
        == 3
    )


@pytest.mark.parametrize("action", ["fulfill", "reject"])
def test_resolve_registry_readiness_failure_preserves_every_surface(
    db_session,
    monkeypatch,
    action,
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
        action=action,
        expected_exception=AppError,
        expected_code="DOUBAO_REGISTRY_NOT_READY",
    )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        pytest.param("tenant_id", "invariant-other-tenant", id="tenant"),
        pytest.param("user_id", "invariant-other-user", id="user"),
        pytest.param("operation", "avatar_generate", id="operation-kind"),
        pytest.param("result_type", "other-result", id="result-type"),
        pytest.param("result_id", "other-result-id", id="result-id"),
        pytest.param("requested_credits", Decimal("30001"), id="requested"),
        pytest.param("settled_credits", Decimal("1"), id="settled"),
        pytest.param("released_credits", Decimal("1"), id="released"),
    ],
)
def test_resolve_each_operation_invariant_failure_preserves_every_surface(
    db_session,
    field,
    invalid_value,
) -> None:
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    _seed_invariant_dependencies(db_session)
    locator = _capture_resolve_locator(db_session, order_id=order_id)

    if field in {"settled_credits", "released_credits"}:
        _update_ignoring_sqlite_checks(
            db_session,
            f"UPDATE billing_operations SET {field} = :value WHERE id = :operation_id",
            {"value": str(invalid_value), "operation_id": operation_id},
        )
    else:
        setattr(db_session.get(BillingOperation, operation_id), field, invalid_value)
        db_session.commit()

    _assert_resolve_failure_preserves_every_surface(
        db_session,
        order_id=order_id,
        locator=locator,
    )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        pytest.param(
            "billing_operation_id",
            "invariant-unrelated-operation",
            id="billing-operation-association",
        ),
        pytest.param(
            "subscription_id",
            "invariant-other-subscription",
            id="subscription-association",
        ),
        pytest.param("tenant_id", "invariant-other-tenant", id="tenant"),
        pytest.param("status", "released", id="status"),
        pytest.param(
            "settled_at",
            datetime(2026, 8, 29, 12, 1, tzinfo=UTC),
            id="settled-at",
        ),
        pytest.param("billing_item_index", 1, id="item-index"),
        pytest.param("billing_pricing_line_index", 1, id="pricing-line-index"),
        pytest.param("capability", "tts", id="capability"),
        pytest.param("provider", "other-provider", id="provider"),
        pytest.param("model", "other-model", id="model"),
        pytest.param("video_task_id", "invariant-video-task", id="video-task"),
        pytest.param("unit", "second", id="unit"),
        pytest.param("quantity", Decimal("2"), id="quantity"),
        pytest.param("credits", Decimal("30001"), id="credits"),
    ],
)
def test_resolve_each_usage_invariant_failure_preserves_every_surface(
    db_session,
    field,
    invalid_value,
) -> None:
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    _seed_invariant_dependencies(db_session)
    locator = _capture_resolve_locator(db_session, order_id=order_id)
    usage = db_session.scalar(
        select(UsageRecord).where(UsageRecord.billing_operation_id == operation_id)
    )
    setattr(usage, field, invalid_value)
    db_session.commit()

    _assert_resolve_failure_preserves_every_surface(
        db_session,
        order_id=order_id,
        locator=locator,
    )


@pytest.mark.parametrize(
    "case",
    [
        "order-tenant",
        "order-user",
        "order-operation",
        "order-source-asset",
        "order-existing-voice",
        "order-type-operation",
        "asset-missing",
        "asset-tenant",
        "source-subscription-tenant",
    ],
)
def test_resolve_each_business_invariant_failure_preserves_every_surface(
    db_session,
    case,
) -> None:
    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    _seed_invariant_dependencies(db_session)
    locator = _capture_resolve_locator(db_session, order_id=order_id)
    order = db_session.get(BrandVoiceOrder, order_id)

    if case == "order-tenant":
        order.tenant_id = "invariant-other-tenant"
    elif case == "order-user":
        order.user_id = "invariant-other-user"
    elif case == "order-operation":
        order.billing_operation_id = "invariant-unrelated-operation"
    elif case == "order-source-asset":
        order.source_audio_asset_id = "invariant-other-audio"
    elif case == "order-existing-voice":
        _update_ignoring_sqlite_checks(
            db_session,
            "UPDATE brand_voice_orders "
            "SET existing_brand_voice_id = :voice_id WHERE id = :order_id",
            {"voice_id": "invariant-existing-voice", "order_id": order_id},
        )
    elif case == "order-type-operation":
        _update_ignoring_sqlite_checks(
            db_session,
            "UPDATE brand_voice_orders SET order_type = 'renew' WHERE id = :order_id",
            {"order_id": order_id},
        )
    elif case == "asset-missing":
        _delete_asset_ignoring_sqlite_fk(db_session, asset_id=locator.source_asset_id)
    elif case == "asset-tenant":
        db_session.get(Asset, locator.source_asset_id).tenant_id = "invariant-other-tenant"
    elif case == "source-subscription-tenant":
        db_session.get(
            Subscription, locator.source_subscription_id
        ).tenant_id = "invariant-other-tenant"
    else:  # pragma: no cover - parameter boundary
        raise AssertionError(case)
    if db_session.dirty:
        db_session.commit()

    _assert_resolve_failure_preserves_every_surface(
        db_session,
        order_id=order_id,
        locator=locator,
    )


def test_resolve_order_status_changed_after_replay_reaches_business_invariant(
    db_session,
) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import _resolve_in_transaction

    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    locator = _capture_resolve_locator(db_session, order_id=order_id)
    before = _manual_order_state(db_session, order_id=order_id)
    engine = db_session.get_bind()
    injected = False

    def change_status_after_terminal_replay(
        _connection,
        cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        nonlocal injected
        if not injected and "FROM subscriptions" in statement:
            cursor.execute(
                "UPDATE brand_voice_orders SET status = 'rejected' WHERE id = ?",
                (order_id,),
            )
            injected = True

    db_session.execute(text("PRAGMA ignore_check_constraints=ON"))
    event.listen(engine, "before_cursor_execute", change_status_after_terminal_replay)
    try:
        with pytest.raises(BillingInvariantError) as captured:
            _resolve_in_transaction(
                db_session,
                locator=locator,
                actor_id="user-a",
                actor_tenant_id="tenant-a",
                action="fulfill",
                provider_voice_id="invariant-provider-id",
                rejection_reason=None,
                transaction_now=datetime(2026, 8, 29, 12, 2, tzinfo=UTC),
            )
        assert captured.value.code == "BILLING_INVARIANT_VIOLATION"
        assert injected is True
    finally:
        event.remove(engine, "before_cursor_execute", change_status_after_terminal_replay)
        db_session.rollback()
        db_session.execute(text("PRAGMA ignore_check_constraints=OFF"))
        db_session.commit()
    db_session.expire_all()
    assert _manual_order_state(db_session, order_id=order_id) == before


@pytest.mark.parametrize(
    "case",
    [
        "line-count",
        "top-operation",
        "pricing-shape",
        "payable",
        "top-subtotal",
        "line-operation",
        "line-capability",
        "line-unit",
        "line-quantity",
        "line-unit-credits",
        "line-subtotal",
        "line-scope",
        "line-source",
        "code-default-policy-key",
    ],
)
def test_resolve_each_snapshot_invariant_failure_preserves_every_surface(
    db_session,
    case,
) -> None:
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    locator = _capture_resolve_locator(db_session, order_id=order_id)
    operation = db_session.get(BillingOperation, operation_id)
    snapshot = deepcopy(operation.pricing_snapshot)
    line = snapshot["pricing_lines"][0]

    if case == "line-count":
        snapshot["pricing_lines"].append(deepcopy(line))
    elif case == "top-operation":
        snapshot["operation"] = "doubao_brand_voice_order_renew"
    elif case == "pricing-shape":
        snapshot["pricing_shape"] = "composite"
    elif case == "payable":
        snapshot["payable_credits"] = 30_001
    elif case == "top-subtotal":
        snapshot["subtotal_credits"] = "30001"
    elif case == "line-operation":
        line["operation"] = "doubao_brand_voice_order_renew"
    elif case == "line-capability":
        line["capability"] = "tts"
    elif case == "line-unit":
        line["unit"] = "second"
    elif case == "line-quantity":
        line["quantity"] = "2"
        line["unit_credits"] = "15000"
    elif case == "line-unit-credits":
        line["unit_credits"] = "30001"
        line["subtotal_credits"] = "30001"
        snapshot["subtotal_credits"] = "30001"
        snapshot["payable_credits"] = 30_001
    elif case == "line-subtotal":
        line["subtotal_credits"] = "30001"
    elif case == "line-scope":
        line["rate_scope"] = "tenant_overridable"
    elif case == "line-source":
        line["rate_source"] = "fixed_policy"
    elif case == "code-default-policy-key":
        line["policy_key"] = "wrong-policy-key"
    else:  # pragma: no cover - parameter boundary
        raise AssertionError(case)
    operation.pricing_snapshot = snapshot
    db_session.commit()

    _assert_resolve_failure_preserves_every_surface(
        db_session,
        order_id=order_id,
        locator=locator,
    )


@pytest.mark.parametrize("action", ["fulfill", "reject"])
def test_resolve_each_terminal_action_writes_exact_consistent_state(
    db_session,
    action,
) -> None:
    from app.services.brand_voice_orders import resolve_brand_voice_order
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    source_subscription = db_session.get(Subscription, "subscription-a")
    resolved_at = source_subscription.period_start.replace(tzinfo=UTC) + timedelta(minutes=1)
    order_id, operation_id = _seed_manual_order_for_invariant_test(db_session)
    resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action=action,
        provider_voice_id="exact-terminal-provider" if action == "fulfill" else None,
        rejection_reason="exact terminal rejection" if action == "reject" else None,
        now=resolved_at,
    )
    db_session.expire_all()

    order = db_session.get(BrandVoiceOrder, order_id)
    operation = db_session.get(BillingOperation, operation_id)
    usage = db_session.scalar(
        select(UsageRecord).where(UsageRecord.billing_operation_id == operation_id)
    )
    subscription = db_session.get(Subscription, usage.subscription_id)
    audit = db_session.scalar(select(AdminAuditLog))
    expected_status = "fulfilled" if action == "fulfill" else "rejected"
    expected_settled = 30_000 if action == "fulfill" else 0

    assert order.status == expected_status
    assert order.resolver_user_id == "user-a"
    assert operation.status == "completed"
    assert operation.completion_kind == ("succeeded" if action == "fulfill" else "rejected")
    assert operation.completed_at.replace(tzinfo=UTC) == resolved_at
    assert operation.result_type == "brand_voice_order"
    assert operation.result_id == order_id
    assert operation.result_payload["id"] == order_id
    assert operation.result_payload["status"] == expected_status
    assert operation.requested_credits == 30_000
    assert operation.settled_credits == expected_settled
    assert operation.released_credits == 30_000 - expected_settled
    assert usage.status == ("settled" if action == "fulfill" else "released")
    assert usage.settled_at.replace(tzinfo=UTC) == resolved_at
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == expected_settled
    assert db_session.scalar(select(func.count()).select_from(AdminAuditLog)) == 1
    assert audit.actor_user_id == "user-a"
    assert audit.actor_tenant_id == "tenant-a"
    assert audit.action == f"brand_voice_order_{action}"
    assert audit.target_tenant_id == "tenant-a"
    assert audit.target_id == order_id
    assert audit.before == {"status": "awaiting_fulfillment"}
    assert audit.after == {"status": expected_status}
    assert audit.reason == (None if action == "fulfill" else "exact terminal rejection")
    assert audit.created_at.replace(tzinfo=UTC) == resolved_at
    assert db_session.scalar(select(func.count()).select_from(CreditRefundGrant)) == 0

    db_session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
    db_session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20260829_0038')"))
    db_session.commit()
    valid_report = pricing_closure_readiness(db_session, production_mode=False)
    assert not any(
        blocker.code == "BILLING_OPERATION_INVARIANT_FAILURE"
        and blocker.record_ids == (operation_id,)
        for blocker in valid_report.blockers
    )

    operation.result_id = f"missing-{action}-order"
    db_session.commit()
    corrupt_report = pricing_closure_readiness(db_session, production_mode=False)
    assert any(
        blocker.code == "BILLING_OPERATION_INVARIANT_FAILURE"
        and blocker.record_ids == (operation_id,)
        for blocker in corrupt_report.blockers
    )

    if action == "fulfill":
        registry = db_session.get(BrandVoiceProviderId, order.fulfilled_provider_id)
        voice = db_session.get(BrandVoice, order.fulfilled_brand_voice_id)
        assert registry.provider == "doubao-voice-clone"
        assert registry.normalized_provider_id == "exact-terminal-provider"
        assert registry.kind == "customer"
        assert registry.brand_voice_id == voice.id
        assert registry.first_order_id == order_id
        assert registry.status == "active"
        assert voice.owner_user_id == "user-a"
        assert voice.source_audio_asset_id == "invariant-audio"
        assert voice.speaker_id == "exact-terminal-provider"
        assert voice.status == "ready"
        assert voice.activated_at.replace(tzinfo=UTC) == resolved_at
        assert voice.expires_at.replace(tzinfo=UTC) == resolved_at + timedelta(days=365)
        # SQLite cannot DROP the intentional fulfilled-order/provider-ID RESTRICT cycle.
        db_session.commit()
        db_session.close()
        with db_session.get_bind().connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    else:
        assert order.fulfilled_brand_voice_id is None
        assert order.fulfilled_provider_id is None
        assert order.rejected_at.replace(tzinfo=UTC) == resolved_at
        assert order.rejection_reason == "exact terminal rejection"
        assert db_session.scalar(select(func.count()).select_from(BrandVoiceProviderId)) == 0
        assert db_session.scalar(select(func.count()).select_from(BrandVoice)) == 0


@pytest.mark.parametrize("swap_result_id", [True, False])
def test_lookup_rejects_same_user_brand_voice_orders_with_swapped_results(
    db_session,
    swap_result_id,
) -> None:
    from app.services.billing_operations import BillingInvariantError, lookup_operation
    from app.services.brand_voice_orders import resolve_brand_voice_order

    source = db_session.get(Subscription, "subscription-a")
    resolved_at = source.period_start.replace(tzinfo=UTC) + timedelta(minutes=1)
    first_order_id, first_operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-first",
    )
    resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=first_order_id,
        action="fulfill",
        provider_voice_id="swapped-result-first-provider",
        now=resolved_at,
    )
    second_order_id, second_operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-second",
    )
    resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=second_order_id,
        action="reject",
        rejection_reason="swapped result rejection",
        now=resolved_at + timedelta(minutes=1),
    )
    db_session.expire_all()
    first = db_session.get(BillingOperation, first_operation_id)
    second = db_session.get(BillingOperation, second_operation_id)
    first_result_id = first.result_id
    first_payload = deepcopy(first.result_payload)
    if swap_result_id:
        first.result_id = second.result_id
        second.result_id = first_result_id
    first.result_payload = deepcopy(second.result_payload)
    second.result_payload = first_payload
    db_session.commit()

    try:
        with pytest.raises(BillingInvariantError):
            lookup_operation(
                db_session,
                tenant_id=first.tenant_id,
                user_id=first.user_id,
                operation=first.operation,
                idempotency_key=UUID(first.idempotency_key),
            )
    finally:
        db_session.rollback()
        db_session.close()
        with db_session.get_bind().connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")


def test_terminal_lookup_rejects_every_immutable_manual_order_snapshot_drift(
    db_session,
) -> None:
    from app.services.billing_operations import BillingInvariantError, lookup_operation
    from app.services.brand_voice_orders import resolve_brand_voice_order

    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-stored-fields",
    )
    resolved_at = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="fulfill",
        provider_voice_id="stored-field-provider",
        now=resolved_at,
    )
    db_session.expire_all()
    operation = db_session.get(BillingOperation, operation_id)
    original = deepcopy(operation.result_payload)
    immutable_drifts = (
        (("id",), "different-order"),
        (("tenant_id",), "different-tenant"),
        (("ordered_by_user_id",), "different-user"),
        (("order_type",), "renew"),
        (("requested_name",), "Different name"),
        (("source_audio_asset_id",), "different-asset"),
        (("existing_brand_voice_id",), "different-existing-voice"),
        (("status",), "rejected"),
        (("fulfilled_brand_voice_id",), "different-delivered-voice"),
        (("fulfilled_provider_voice_id",), "different-provider-voice"),
        (("rejection_reason",), "different rejection"),
        (("fulfilled_at",), "2026-08-29T13:00:00Z"),
        (("expires_at",), "2027-08-30T12:00:00Z"),
        (("rejected_at",), "2026-08-29T13:00:00Z"),
        (("created_at",), "2026-08-28T12:00:00Z"),
        (("updated_at",), "2026-08-29T13:00:00Z"),
        (("billing", "operation_id"), "different-operation"),
        (("billing", "idempotency_key"), str(uuid4())),
        (("billing", "status"), "released"),
        (("billing", "requested_credits"), 29_999),
        (("billing", "held_credits"), 1),
        (("billing", "settled_credits"), 29_999),
        (("billing", "released_credits"), 1),
    )
    lookup_args = {
        "tenant_id": operation.tenant_id,
        "user_id": operation.user_id,
        "operation": operation.operation,
        "idempotency_key": UUID(operation.idempotency_key),
    }

    try:
        for path, value in immutable_drifts:
            payload = deepcopy(original)
            target = payload
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            operation.result_payload = payload
            db_session.commit()

            with pytest.raises(BillingInvariantError):
                lookup_operation(db_session, **lookup_args)
    finally:
        db_session.rollback()
        db_session.close()
        with db_session.get_bind().connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")


def test_direct_order_read_rejects_operation_type_mismatch(db_session) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read

    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-type",
    )
    operation = db_session.get(BillingOperation, operation_id)
    operation.operation = "doubao_brand_voice_order_renew"
    db_session.commit()

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(
            db_session,
            order=db_session.get(BrandVoiceOrder, order_id),
        )


@pytest.mark.parametrize("corruption", ["result-id", "state", "amount", "user"])
def test_direct_order_read_rejects_cross_table_billing_drift(
    db_session,
    corruption,
) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read

    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix=f"-{corruption}",
    )
    operation = db_session.get(BillingOperation, operation_id)
    if corruption == "result-id":
        operation.result_id = "different-order"
    elif corruption == "state":
        operation.status = "completed"
        operation.completion_kind = "rejected"
        operation.completed_at = datetime(2026, 8, 29, 12, 1, tzinfo=UTC)
        operation.released_credits = Decimal("30000")
    elif corruption == "amount":
        operation.requested_credits = Decimal("29999")
    elif corruption == "user":
        db_session.add(
            User(
                id="direct-read-other-user",
                tenant_id="tenant-a",
                email="direct-read-other@example.com",
                password_hash="hash",
                role="creator",
            )
        )
        db_session.flush()
        operation.user_id = "direct-read-other-user"
    else:  # pragma: no cover - parameter boundary
        raise AssertionError(corruption)
    db_session.commit()

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(
            db_session,
            order=db_session.get(BrandVoiceOrder, order_id),
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
        db.get(
            BrandVoice, "renew-revalidation-voice"
        ).source_audio_asset_id = "renew-candidate-audio"
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


def test_customer_submit_registry_503_redacts_inventory_while_ops_audit_keeps_diagnostics(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import provider_voice_registry
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    official_id = "sensitive-official-provider-id"
    unknown_id = "sensitive-unknown-provider-id"
    blocker_id = "sensitive-blocker-provider-id"
    blocker_row_id = "sensitive-blocker-registry-row"
    blocker_provider = "sensitive-noncanonical-provider"
    monkeypatch.setattr(provider_voice_registry.settings, "environment", "production")
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        [official_id],
    )
    with auth_db() as db:
        subscription = db.scalar(select(Subscription))
        db.get(Plan, subscription.plan_id).code = "huading"
        subscription.quota_credits_total = 100_000
        db.add(
            Asset(
                id="registry-redaction-audio",
                tenant_id=auth_context["tenant_id"],
                type="audio",
                source="upload",
                storage_key=(f"tenants/{auth_context['tenant_id']}/uploads/registry-redaction.wav"),
                mime_type="audio/wav",
                duration_ms=10_000,
                status="ready",
            )
        )
        provider_voice_registry.register_official_provider_voice_ids(
            db,
            provider_voice_ids=[official_id],
        )
        db.add_all(
            [
                BrandVoiceProviderId(
                    id=blocker_row_id,
                    provider=blocker_provider,
                    normalized_provider_id=blocker_id,
                    kind="official",
                    status="active",
                ),
                ProviderConfig(
                    id="registry-redaction-provider-config",
                    tenant_id=None,
                    capability="voice_clone",
                    provider="registry-redaction-config-provider",
                    config={"speaker_ids": [unknown_id]},
                    is_active=False,
                ),
            ]
        )
        db.commit()

    client = TestClient(app)
    payload = {
        "order_type": "create",
        "requested_name": "Registry redaction voice",
        "source_audio_asset_id": "registry-redaction-audio",
        "consent_confirmed": True,
    }
    quote = client.post(
        "/api/v1/brand-voice-orders/estimate",
        headers=auth_context["headers"],
        json=payload,
    )
    assert quote.status_code == 200, quote.text
    response = client.post(
        "/api/v1/brand-voice-orders",
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote.json()["data"]["quote_token"],
        },
        json=payload,
    )

    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "DOUBAO_REGISTRY_NOT_READY"
    assert error["message"] == "Doubao provider voice registry is not ready."
    assert error.get("detail") is None
    for private_value in (
        official_id,
        unknown_id,
        blocker_id,
        blocker_row_id,
        blocker_provider,
    ):
        assert private_value not in response.text

    with auth_db() as db:
        report = pricing_closure_readiness(db, production_mode=True)
        assert official_id in report.provider_inventory["official_configured_ids"]
        assert official_id in report.provider_inventory["active_official_registry_ids"]
        assert [item.provider_voice_id for item in report.inventory_unknown_ids] == [unknown_id]
        assert ("DOUBAO_REGISTRY_INVARIANT_FAILURE", (blocker_row_id,)) in {
            (item.code, item.record_ids) for item in report.blockers
        }
        with pytest.raises(AppError) as internal:
            provider_voice_registry.assert_doubao_registry_ready(db)
        assert internal.value.detail == {
            "official_configured_ids": [official_id],
            "official_registered_ids": [official_id],
            "registry_blockers": [
                {
                    "provider_voice_id": blocker_id,
                    "provider": blocker_provider,
                    "kind": "official",
                    "status": "active",
                    "reason": "provider_not_canonical",
                }
            ],
            "unknown_ids": [unknown_id],
        }


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
    from app.api.v1.routes.brand_voices import _brand_voice_read
    from app.services.brand_voice_orders import (
        brand_voice_order_read,
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
    assert brand_voice_order_read(db_session, order=resolved).expires_at == voice.expires_at
    assert _brand_voice_read(db_session, voice).expires_at == voice.expires_at
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


@pytest.mark.parametrize(
    "invalid_delivery",
    [
        "missing",
        "wrong_owner",
        "missing_expiry",
        "wrong_expiry",
        "wrong_activated_at",
    ],
)
def test_fulfilled_order_read_fails_closed_for_invalid_delivered_voice(
    db_session,
    invalid_delivery: str,
) -> None:
    from app.services.brand_voice_orders import (
        brand_voice_order_read,
        resolve_brand_voice_order,
    )

    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    resolved = resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="fulfill",
        provider_voice_id=f"invalid-read-{invalid_delivery}",
        now=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
    )
    voice = db_session.get(BrandVoice, resolved.fulfilled_brand_voice_id)
    if invalid_delivery == "missing":
        resolved.fulfilled_brand_voice_id = "missing-delivered-voice"
    elif invalid_delivery == "wrong_owner":
        voice.owner_user_id = None
    elif invalid_delivery == "missing_expiry":
        voice.expires_at = None
    elif invalid_delivery == "wrong_expiry":
        voice.expires_at = resolved.fulfilled_at + timedelta(days=364)
    else:
        voice.activated_at = resolved.fulfilled_at + timedelta(seconds=1)

    with db_session.no_autoflush, pytest.raises(AppError) as captured:
        brand_voice_order_read(db_session, order=resolved)

    assert captured.value.code == "BILLING_INVARIANT_VIOLATION"
    db_session.rollback()
    db_session.close()
    with db_session.get_bind().connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")


@pytest.mark.parametrize(
    "invalid_terminal_field",
    ["missing_fulfilled_at", "missing_provider", "rejected_at", "rejection_reason"],
)
def test_fulfilled_order_read_fails_closed_for_inconsistent_terminal_fields(
    db_session,
    invalid_terminal_field: str,
) -> None:
    from app.services.brand_voice_orders import brand_voice_order_read, resolve_brand_voice_order

    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    resolved = resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="fulfill",
        provider_voice_id=f"invalid-terminal-{invalid_terminal_field}",
        now=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
    )
    if invalid_terminal_field == "missing_fulfilled_at":
        resolved.fulfilled_at = None
    elif invalid_terminal_field == "missing_provider":
        resolved.fulfilled_provider_id = None
    elif invalid_terminal_field == "rejected_at":
        resolved.rejected_at = datetime(2026, 8, 29, 12, 1, tzinfo=UTC)
    else:
        resolved.rejection_reason = "must stay null"

    with db_session.no_autoflush, pytest.raises(AppError) as captured:
        brand_voice_order_read(db_session, order=resolved)

    assert captured.value.code == "BILLING_INVARIANT_VIOLATION"
    db_session.rollback()
    db_session.close()
    with db_session.get_bind().connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")


@pytest.mark.parametrize(
    "entrypoint",
    ["brand_voice_order_read", "lookup_operation", "pricing_closure_readiness"],
)
def test_fulfilled_order_rejects_delivery_clock_shift_from_billing_completion(
    db_session,
    entrypoint: str,
) -> None:
    from app.services.billing_operations import BillingInvariantError, lookup_operation
    from app.services.brand_voice_orders import brand_voice_order_read, resolve_brand_voice_order
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    suffix = {
        "brand_voice_order_read": "read",
        "lookup_operation": "lookup",
        "pricing_closure_readiness": "ready",
    }[entrypoint]
    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix=f"-clock-{suffix}",
    )
    resolved_at = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="fulfill",
        provider_voice_id=f"clock-shift-{suffix}",
        now=resolved_at,
    )
    db_session.expire_all()
    order = db_session.get(BrandVoiceOrder, order_id)
    operation = db_session.get(BillingOperation, operation_id)
    voice = db_session.get(BrandVoice, order.fulfilled_brand_voice_id)
    shifted_at = resolved_at + timedelta(hours=1)
    order.fulfilled_at = shifted_at
    voice.activated_at = shifted_at
    voice.expires_at = shifted_at + timedelta(days=365)
    db_session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))"))
    db_session.execute(text("DELETE FROM alembic_version"))
    db_session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20260829_0038')"))
    db_session.commit()

    try:
        if entrypoint == "brand_voice_order_read":
            with pytest.raises(BillingInvariantError):
                brand_voice_order_read(
                    db_session,
                    order=db_session.get(BrandVoiceOrder, order_id),
                )
        elif entrypoint == "lookup_operation":
            with pytest.raises(BillingInvariantError):
                lookup_operation(
                    db_session,
                    tenant_id=operation.tenant_id,
                    user_id=operation.user_id,
                    operation=operation.operation,
                    idempotency_key=UUID(operation.idempotency_key),
                )
        else:
            report = pricing_closure_readiness(db_session, production_mode=False)
            assert report.ready is False
            assert (
                "BILLING_OPERATION_INVARIANT_FAILURE",
                (operation_id,),
            ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}
    finally:
        db_session.rollback()
        db_session.close()
        with db_session.get_bind().connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")


def test_non_fulfilled_order_reads_never_expose_an_expiry(db_session) -> None:
    from app.services.brand_voice_orders import (
        brand_voice_order_read,
        resolve_brand_voice_order,
    )

    order_id, _operation_id = _seed_manual_order_for_invariant_test(db_session)
    order = db_session.get(BrandVoiceOrder, order_id)
    assert brand_voice_order_read(db_session, order=order).expires_at is None

    rejected = resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="reject",
        rejection_reason="invalid source audio",
        now=datetime(2026, 8, 29, 12, 1, tzinfo=UTC),
    )

    assert brand_voice_order_read(db_session, order=rejected).expires_at is None


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
    db_session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))"))
    db_session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20260829_0038')"))
    db_session.commit()

    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    report = pricing_closure_readiness(db_session, production_mode=False)
    assert (
        "BILLING_OPERATION_INVARIANT_FAILURE",
        (order.billing_operation_id,),
    ) not in {(blocker.code, blocker.record_ids) for blocker in report.blockers}

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
        select(CreditRefundGrant).where(CreditRefundGrant.billing_operation_id == operation_id)
    )
    assert grant.status == "applied"
    assert grant.source_subscription_id == source.id
    assert grant.target_subscription_id == current.id


def test_same_period_rejection_without_grant_exposes_source_release(db_session) -> None:
    from app.services.brand_voice_orders import brand_voice_order_read, resolve_brand_voice_order

    source = db_session.get(Subscription, "subscription-a")
    rejected_at = source.period_start.replace(tzinfo=UTC) + timedelta(minutes=1)
    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-same-release",
    )

    resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="reject",
        rejection_reason="same-period release",
        now=rejected_at,
    )
    db_session.expire_all()

    assert (
        db_session.scalar(
            select(CreditRefundGrant).where(CreditRefundGrant.billing_operation_id == operation_id)
        )
        is None
    )
    read = brand_voice_order_read(
        db_session,
        order=db_session.get(BrandVoiceOrder, order_id),
    )
    assert read.refund_disposition == "source_subscription_released"


def test_cross_period_rejection_without_grant_fails_read_and_readiness(db_session) -> None:
    from app.services.billing_operations import BillingInvariantError, lookup_operation
    from app.services.brand_voice_orders import brand_voice_order_read
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    order_id, operation_id, _source, _current, grant = _seed_cross_period_refund_grant(
        db_session,
        suffix="-missing-grant",
    )
    db_session.delete(grant)
    db_session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))"))
    db_session.execute(text("DELETE FROM alembic_version"))
    db_session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20260829_0038')"))
    db_session.commit()

    operation = db_session.get(BillingOperation, operation_id)
    with pytest.raises(BillingInvariantError):
        lookup_operation(
            db_session,
            tenant_id=operation.tenant_id,
            user_id=operation.user_id,
            operation=operation.operation,
            idempotency_key=UUID(operation.idempotency_key),
        )

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(
            db_session,
            order=db_session.get(BrandVoiceOrder, order_id),
        )

    report = pricing_closure_readiness(db_session, production_mode=False)
    assert report.ready is False
    assert (
        "BILLING_OPERATION_INVARIANT_FAILURE",
        (operation_id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


@pytest.mark.parametrize("grant_status", ["pending", "applied"])
@pytest.mark.parametrize(
    "entrypoint",
    ["brand_voice_order_read", "lookup_operation", "pricing_closure_readiness"],
)
def test_grant_backed_rejection_rejects_rejected_at_drift_at_every_read_boundary(
    db_session,
    grant_status,
    entrypoint,
) -> None:
    from app.services.billing_operations import BillingInvariantError, lookup_operation
    from app.services.brand_voice_orders import brand_voice_order_read
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    entrypoint_suffix = {
        "brand_voice_order_read": "r",
        "lookup_operation": "l",
        "pricing_closure_readiness": "c",
    }[entrypoint]
    order_id, operation_id, _source, _current, grant = _seed_cross_period_refund_grant(
        db_session,
        suffix=f"-rt-{grant_status[0]}-{entrypoint_suffix}",
    )
    order = db_session.get(BrandVoiceOrder, order_id)
    operation = db_session.get(BillingOperation, operation_id)
    order.rejected_at = operation.completed_at.replace(tzinfo=UTC) + timedelta(hours=1)
    if grant_status == "pending":
        grant.status = "pending"
        grant.target_subscription_id = None
        grant.applied_at = None
    db_session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))"))
    db_session.execute(text("DELETE FROM alembic_version"))
    db_session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20260829_0038')"))
    db_session.commit()

    if entrypoint == "brand_voice_order_read":
        with pytest.raises(BillingInvariantError):
            brand_voice_order_read(
                db_session,
                order=db_session.get(BrandVoiceOrder, order_id),
            )
    elif entrypoint == "lookup_operation":
        with pytest.raises(BillingInvariantError):
            lookup_operation(
                db_session,
                tenant_id=operation.tenant_id,
                user_id=operation.user_id,
                operation=operation.operation,
                idempotency_key=UUID(operation.idempotency_key),
            )
    else:
        report = pricing_closure_readiness(db_session, production_mode=False)
        assert report.ready is False
        assert (
            "BILLING_OPERATION_INVARIANT_FAILURE",
            (operation_id,),
        ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


@pytest.mark.parametrize(
    "entrypoint",
    ["brand_voice_order_read", "lookup_operation", "pricing_closure_readiness"],
)
def test_applied_refund_rejects_applied_at_before_rejection_at_every_read_boundary(
    db_session,
    entrypoint,
) -> None:
    from app.services.billing_operations import BillingInvariantError, lookup_operation
    from app.services.brand_voice_orders import brand_voice_order_read
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    entrypoint_suffix = {
        "brand_voice_order_read": "r",
        "lookup_operation": "l",
        "pricing_closure_readiness": "c",
    }[entrypoint]
    order_id, operation_id, _source, target, grant = _seed_cross_period_refund_grant(
        db_session,
        suffix=f"-at-{entrypoint_suffix}",
    )
    operation = db_session.get(BillingOperation, operation_id)
    applied_at = operation.completed_at.replace(tzinfo=UTC) - timedelta(hours=1)
    grant.applied_at = applied_at
    target.period_start = applied_at - timedelta(hours=1)
    db_session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))"))
    db_session.execute(text("DELETE FROM alembic_version"))
    db_session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20260829_0038')"))
    db_session.commit()

    if entrypoint == "brand_voice_order_read":
        with pytest.raises(BillingInvariantError):
            brand_voice_order_read(
                db_session,
                order=db_session.get(BrandVoiceOrder, order_id),
            )
    elif entrypoint == "lookup_operation":
        with pytest.raises(BillingInvariantError):
            lookup_operation(
                db_session,
                tenant_id=operation.tenant_id,
                user_id=operation.user_id,
                operation=operation.operation,
                idempotency_key=UUID(operation.idempotency_key),
            )
    else:
        report = pricing_closure_readiness(db_session, production_mode=False)
        assert report.ready is False
        assert (
            "BILLING_OPERATION_INVARIANT_FAILURE",
            (operation_id,),
        ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_rejected_order_read_rejects_refund_grant_amount_drift(db_session) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read

    order_id, _operation_id, _source, _current, grant = _seed_cross_period_refund_grant(
        db_session,
        suffix="-refund-amount",
    )
    grant.amount_credits = Decimal("29999")
    db_session.commit()

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(
            db_session,
            order=db_session.get(BrandVoiceOrder, order_id),
        )


def test_rejected_order_read_rejects_refund_grant_tenant_drift(db_session) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read

    order_id, _operation_id, _source, _current, grant = _seed_cross_period_refund_grant(
        db_session,
        suffix="-refund-tenant",
    )
    db_session.add(
        Tenant(
            id="refund-grant-other-tenant",
            slug="refund-grant-other-tenant",
            name="Refund Grant Other Tenant",
        )
    )
    db_session.flush()
    grant.tenant_id = "refund-grant-other-tenant"
    db_session.commit()

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(
            db_session,
            order=db_session.get(BrandVoiceOrder, order_id),
        )


def test_rejected_order_read_rejects_refund_grant_user_drift(db_session) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read

    order_id, _operation_id, _source, _current, grant = _seed_cross_period_refund_grant(
        db_session,
        suffix="-refund-user",
    )
    db_session.add(
        User(
            id="refund-grant-other-user",
            tenant_id="tenant-a",
            email="refund-grant-other@example.com",
            password_hash="hash",
            role="creator",
        )
    )
    db_session.flush()
    grant.user_id = "refund-grant-other-user"
    db_session.commit()

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(
            db_session,
            order=db_session.get(BrandVoiceOrder, order_id),
        )


def test_rejected_order_read_rejects_refund_grant_source_drift(db_session) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read

    order_id, _operation_id, _source, current, grant = _seed_cross_period_refund_grant(
        db_session,
        suffix="-refund-source",
    )
    grant.source_subscription_id = current.id
    db_session.commit()

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(
            db_session,
            order=db_session.get(BrandVoiceOrder, order_id),
        )


def test_non_rejected_order_read_rejects_refund_grant(db_session) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read

    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-grant",
    )
    source_subscription_id = db_session.scalar(
        select(UsageRecord.subscription_id).where(UsageRecord.billing_operation_id == operation_id)
    )
    db_session.add(
        CreditRefundGrant(
            billing_operation_id=operation_id,
            tenant_id="tenant-a",
            user_id="user-a",
            source_subscription_id=source_subscription_id,
            amount_credits=Decimal("30000"),
            status="pending",
        )
    )
    db_session.commit()

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(
            db_session,
            order=db_session.get(BrandVoiceOrder, order_id),
        )


def test_rejected_order_read_rejects_cross_tenant_refund_target(db_session) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read

    order_id, _operation_id, source, _current, grant = _seed_cross_period_refund_grant(
        db_session,
        suffix="-refund-target",
    )
    other_tenant = Tenant(
        id="refund-target-other-tenant",
        slug="refund-target-other-tenant",
        name="Refund Target Other Tenant",
    )
    other_target = Subscription(
        id="refund-target-other-subscription",
        tenant_id=other_tenant.id,
        plan_id=source.plan_id,
        status="active",
        period_start=source.period_start,
        period_end=source.period_end,
        quota_credits_total=100_000,
        quota_credits_used=0,
        quota_credits_reserved=0,
    )
    db_session.add(other_tenant)
    db_session.flush()
    db_session.add(other_target)
    db_session.flush()
    grant.target_subscription_id = other_target.id
    db_session.commit()

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(
            db_session,
            order=db_session.get(BrandVoiceOrder, order_id),
        )


def test_applied_refund_target_must_cover_applied_at_in_read_and_readiness(db_session) -> None:
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    order_id, _operation_id, _source, target, grant = _seed_cross_period_refund_grant(
        db_session,
        suffix="-target-period",
    )
    applied_at = grant.applied_at.replace(tzinfo=UTC)
    target.period_start = applied_at + timedelta(days=1)
    target.period_end = applied_at + timedelta(days=31)
    db_session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))"))
    db_session.execute(text("DELETE FROM alembic_version"))
    db_session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20260829_0038')"))
    db_session.commit()

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(
            db_session,
            order=db_session.get(BrandVoiceOrder, order_id),
        )

    report = pricing_closure_readiness(db_session, production_mode=False)
    assert report.ready is False
    assert (
        "BILLING_REFUND_GRANT_INVARIANT_FAILURE",
        (grant.id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_pending_refund_grant_is_static_on_read_and_current_at_readiness(db_session) -> None:
    from app.services.brand_voice_orders import brand_voice_order_read, resolve_brand_voice_order
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-pending",
    )
    source = db_session.get(Subscription, "subscription-a")
    source.status = "expired"
    source.period_end = now - timedelta(seconds=1)
    db_session.commit()
    resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="reject",
        rejection_reason="pending refund",
        now=now,
    )
    db_session.expire_all()
    grant = db_session.scalar(
        select(CreditRefundGrant).where(CreditRefundGrant.billing_operation_id == operation_id)
    )
    assert grant.status == "pending"
    db_session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))"))
    db_session.execute(text("DELETE FROM alembic_version"))
    db_session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20260829_0038')"))
    db_session.commit()

    pending_read = brand_voice_order_read(
        db_session,
        order=db_session.get(BrandVoiceOrder, order_id),
    )
    assert pending_read.refund_disposition == "pending_next_subscription"
    no_target_report = pricing_closure_readiness(db_session, production_mode=False)
    assert (
        "BILLING_REFUND_GRANT_INVARIANT_FAILURE",
        (grant.id,),
    ) not in {(blocker.code, blocker.record_ids) for blocker in no_target_report.blockers}

    audit_window_start = datetime.now(UTC) - timedelta(minutes=1)
    db_session.add(
        Subscription(
            id="pending-refund-active-subscription",
            tenant_id=source.tenant_id,
            plan_id=source.plan_id,
            status="active",
            period_start=audit_window_start,
            period_end=audit_window_start + timedelta(days=30),
            quota_credits_total=100_000,
            quota_credits_used=0,
            quota_credits_reserved=0,
        )
    )
    db_session.commit()

    pending_read_with_later_subscription = brand_voice_order_read(
        db_session,
        order=db_session.get(BrandVoiceOrder, order_id),
    )
    assert pending_read_with_later_subscription.refund_disposition == "pending_next_subscription"
    report = pricing_closure_readiness(db_session, production_mode=False)
    assert report.ready is False
    assert report.generated_at >= audit_window_start
    assert (
        "BILLING_REFUND_GRANT_INVARIANT_FAILURE",
        (grant.id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_terminal_lookup_allows_only_pending_refund_fields_to_become_applied(
    db_session,
) -> None:
    from app.services.billing_operations import lookup_operation
    from app.services.brand_voice_orders import resolve_brand_voice_order
    from app.services.subscription import activate_subscription

    rejected_at = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-lk-ref",
    )
    source = db_session.get(Subscription, "subscription-a")
    source.status = "expired"
    source.period_end = rejected_at - timedelta(seconds=1)
    plan = db_session.get(Plan, source.plan_id)
    db_session.commit()
    resolve_brand_voice_order(
        db_session,
        actor=db_session.get(User, "user-a"),
        order_id=order_id,
        action="reject",
        rejection_reason="wait for next subscription",
        now=rejected_at,
    )
    db_session.expire_all()
    operation = db_session.get(BillingOperation, operation_id)
    lookup_args = {
        "tenant_id": operation.tenant_id,
        "user_id": operation.user_id,
        "operation": operation.operation,
        "idempotency_key": UUID(operation.idempotency_key),
    }

    pending = lookup_operation(db_session, **lookup_args)
    assert pending.resource.refund_disposition == "pending_next_subscription"
    assert pending.resource.refund_grant_status == "pending"
    assert pending.resource.refund_applied_at is None
    assert operation.result_payload["refund_grant_status"] == "pending"

    activate_subscription(
        db_session,
        tenant_id=operation.tenant_id,
        plan=plan,
        period_start=rejected_at,
    )
    db_session.commit()
    db_session.expire_all()

    applied = lookup_operation(db_session, **lookup_args)
    assert applied.resource.refund_disposition == "current_subscription_credited"
    assert applied.resource.refund_grant_status == "applied"
    assert applied.resource.refund_applied_at.replace(tzinfo=UTC) == rejected_at
    assert (
        db_session.get(BillingOperation, operation_id).result_payload["refund_grant_status"]
        == "pending"
    )


@pytest.mark.parametrize("read_surface", ["get", "list", "lookup"])
def test_manual_order_reads_recover_from_stale_identity_map_after_resolve_commit(
    db_session,
    read_surface: str,
) -> None:
    from sqlalchemy.orm import sessionmaker

    from app.services.billing_operations import lookup_operation
    from app.services.brand_voice_orders import (
        brand_voice_order_read,
        get_user_brand_voice_order,
        list_user_brand_voice_orders,
        resolve_brand_voice_order,
    )

    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix=f"-stale-{read_surface[0]}",
    )
    db_session.expire_all()
    stale_order = db_session.get(BrandVoiceOrder, order_id)
    stale_operation = None
    if read_surface == "lookup":
        stale_operation = db_session.get(BillingOperation, operation_id)
        assert stale_operation.status == "in_progress"
    assert stale_order.status == "awaiting_fulfillment"

    writer_factory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
    )
    with writer_factory() as writer:
        resolve_brand_voice_order(
            writer,
            actor=writer.get(User, "user-a"),
            order_id=order_id,
            action="fulfill",
            provider_voice_id=f"stale-{read_surface}-provider",
            now=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
        )

    assert stale_order.status == "awaiting_fulfillment"
    try:
        if read_surface == "get":
            selected = get_user_brand_voice_order(
                db_session,
                tenant_id="tenant-a",
                user_id="user-a",
                order_id=order_id,
            )
            result = brand_voice_order_read(db_session, order=selected)
        elif read_surface == "list":
            selected = next(
                order
                for order in list_user_brand_voice_orders(
                    db_session,
                    tenant_id="tenant-a",
                    user_id="user-a",
                )
                if order.id == order_id
            )
            result = brand_voice_order_read(db_session, order=selected)
        else:
            result = lookup_operation(
                db_session,
                tenant_id=stale_operation.tenant_id,
                user_id=stale_operation.user_id,
                operation=stale_operation.operation,
                idempotency_key=UUID(stale_operation.idempotency_key),
            ).resource

        assert result.status == "fulfilled"
        assert result.fulfilled_provider_voice_id == f"stale-{read_surface}-provider"
    finally:
        db_session.rollback()
        db_session.close()
        with db_session.get_bind().connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")


def test_lookup_operation_shares_one_reread_budget_with_manual_order_resource(
    db_session,
    monkeypatch,
) -> None:
    """A stale order and a bad terminal receipt must not cause nested rereads."""
    from sqlalchemy.orm import sessionmaker

    from app.services.billing_operations import BillingInvariantError, lookup_operation
    from app.services.brand_voice_orders import resolve_brand_voice_order

    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-reread-budget",
    )
    db_session.expire_all()
    stale_order = db_session.get(BrandVoiceOrder, order_id)
    operation = db_session.get(BillingOperation, operation_id)
    lookup_args = {
        "tenant_id": operation.tenant_id,
        "user_id": operation.user_id,
        "operation": operation.operation,
        "idempotency_key": UUID(operation.idempotency_key),
    }
    # Keep only the stale order attached.  The outer lookup therefore sees the
    # fresh terminal operation, while its nested resource read must reread.
    db_session.expunge(operation)
    assert stale_order.status == "awaiting_fulfillment"

    writer_factory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
    )
    with writer_factory() as writer:
        resolve_brand_voice_order(
            writer,
            actor=writer.get(User, "user-a"),
            order_id=order_id,
            action="fulfill",
            provider_voice_id="single-reread-provider",
            now=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
        )
    with writer_factory() as corruptor:
        terminal_operation = corruptor.get(BillingOperation, operation_id)
        terminal_payload = deepcopy(terminal_operation.result_payload)
        terminal_payload["requested_name"] = "tampered terminal receipt"
        terminal_operation.result_payload = terminal_payload
        corruptor.commit()

    reread_count = 0
    original_expire_all = db_session.expire_all

    def count_reread() -> None:
        nonlocal reread_count
        reread_count += 1
        original_expire_all()

    monkeypatch.setattr(db_session, "expire_all", count_reread)

    try:
        with pytest.raises(BillingInvariantError):
            lookup_operation(db_session, **lookup_args)

        assert reread_count == 1
    finally:
        db_session.rollback()
        db_session.close()
        with db_session.get_bind().connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")


def test_order_read_does_not_reread_current_committed_result_type_invariant(
    db_session,
    monkeypatch,
) -> None:
    """Only an observable lifecycle mismatch may qualify for a stale reread."""
    from sqlalchemy.orm import sessionmaker

    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read

    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-result-type",
    )
    db_session.expunge_all()
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
    with factory() as corruptor:
        operation = corruptor.get(BillingOperation, operation_id)
        original_result_type = operation.result_type
        operation.result_type = None
        corruptor.commit()

    order = db_session.get(BrandVoiceOrder, order_id)
    reread_count = 0
    original_expire_all = db_session.expire_all

    def repair_during_reread() -> None:
        nonlocal reread_count
        reread_count += 1
        with factory() as repairer:
            operation = repairer.get(BillingOperation, operation_id)
            operation.result_type = original_result_type
            repairer.commit()
        original_expire_all()

    monkeypatch.setattr(db_session, "expire_all", repair_during_reread)

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(db_session, order=order)

    assert reread_count == 0


def test_order_read_does_not_reread_current_lifecycle_mismatch(
    db_session,
    monkeypatch,
) -> None:
    """A current order/operation state mismatch is not evidence of staleness."""
    from app.services.billing_operations import BillingInvariantError
    from app.services.brand_voice_orders import brand_voice_order_read

    order_id, operation_id = _seed_manual_order_for_invariant_test(
        db_session,
        suffix="-lifecycle",
    )
    db_session.expunge_all()
    original_status = "in_progress"
    original_completion_kind = None
    engine = db_session.get_bind()
    with engine.connect() as corruptor:
        corruptor.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        corruptor.execute(
            text(
                "UPDATE billing_operations "
                "SET status = 'completed', completion_kind = 'succeeded' WHERE id = :operation_id"
            ),
            {"operation_id": operation_id},
        )
        corruptor.commit()
        corruptor.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")

    order = db_session.get(BrandVoiceOrder, order_id)
    reread_count = 0
    original_expire_all = db_session.expire_all

    def repair_during_reread() -> None:
        nonlocal reread_count
        reread_count += 1
        with engine.connect() as repairer:
            repairer.execute(
                text(
                    "UPDATE billing_operations "
                    "SET status = :status, completion_kind = :completion_kind "
                    "WHERE id = :operation_id"
                ),
                {
                    "status": original_status,
                    "completion_kind": original_completion_kind,
                    "operation_id": operation_id,
                },
            )
            repairer.commit()
        original_expire_all()

    monkeypatch.setattr(db_session, "expire_all", repair_during_reread)

    with pytest.raises(BillingInvariantError):
        brand_voice_order_read(db_session, order=order)

    assert reread_count == 0
