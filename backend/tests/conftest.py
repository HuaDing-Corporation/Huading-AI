import os
from collections.abc import Callable, Generator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, String, cast, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-test-secret-test-secret-32")

from app.api.deps import get_db_session
from app.core.config import settings
from app.db.models import (
    Asset,
    Base,
    BillingOperation,
    BrandVoice,
    BrandVoiceOrder,
    BrandVoiceProviderId,
    CreditRate,
    CreditRefundGrant,
    Plan,
    ProviderConfig,
    Subscription,
    Tenant,
    UsageRecord,
    User,
)
from app.main import app


@pytest.fixture(autouse=True)
def jwt_test_secret(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-test-secret-test-secret-32")
    monkeypatch.setattr(settings, "engine_bgm_seed_on_startup", False)


@pytest.fixture(scope="session")
def cloned_model_metadata_factory() -> Callable[[], MetaData]:
    """Return disposable DDL metadata without mutating the ORM's shared metadata."""

    def build() -> MetaData:
        metadata = MetaData()
        for table in Base.metadata.tables.values():
            table.to_metadata(metadata)
        return metadata

    return build


@pytest.fixture
def auth_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Enforce foreign keys like Postgres so FK-ordering bugs (e.g. inserting a
    # task_asset before its video_task is persisted) surface in tests too.
    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    # This fixture materializes the current model schema directly instead of
    # running Alembic, so stamp it at the release revision that schema represents.
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": "20260829_0038"},
        )
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db() -> Generator[Session, None, None]:
        db = SessionTesting()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    try:
        yield SessionTesting
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        Base.metadata.drop_all(engine)


@pytest.fixture
def db_session():
    """Small wallet-backed database used by billing service tests."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_billing_sqlite_fk(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    now = datetime.now(UTC)
    tenant = Tenant(id="tenant-a", slug="billing-a", name="Billing A")
    user = User(
        id="user-a",
        tenant_id=tenant.id,
        email="billing-a@example.com",
        password_hash="hash",
        role="creator",
    )
    plan = Plan(
        id="plan-a",
        code="billing-plan-a",
        name="Billing Plan",
        price_cents=0,
        period="monthly",
        quota_credits=100,
    )
    subscription = Subscription(
        id="subscription-a",
        tenant_id=tenant.id,
        plan_id=plan.id,
        status="active",
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=30),
        quota_credits_total=100,
        quota_credits_used=0,
        quota_credits_reserved=0,
    )
    session.add_all([tenant, plan])
    session.flush()
    session.add_all([user, subscription])
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _pricing_closure_snapshot(db: Session) -> dict[str, tuple[tuple[object, ...], ...]]:
    def rows(*columns) -> tuple[tuple[object, ...], ...]:
        return tuple(tuple(row) for row in db.execute(select(*columns).order_by(columns[0])))

    return deepcopy(
        {
            "credit_rates": rows(
                CreditRate.id,
                CreditRate.tenant_id,
                CreditRate.capability,
                CreditRate.unit,
                cast(CreditRate.credits_per_unit, String),
                CreditRate.is_active,
                CreditRate.effective_at,
            ),
            "subscriptions": rows(
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
            ),
            "billing_operations": rows(
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
            ),
            "usage_records": rows(
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
            ),
            "credit_refund_grants": rows(
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
            ),
            "provider_configs": rows(
                ProviderConfig.id,
                ProviderConfig.tenant_id,
                ProviderConfig.capability,
                ProviderConfig.provider,
                ProviderConfig.config,
                ProviderConfig.is_active,
            ),
            "provider_voice_registry": rows(
                BrandVoiceProviderId.id,
                BrandVoiceProviderId.provider,
                BrandVoiceProviderId.normalized_provider_id,
                BrandVoiceProviderId.kind,
                BrandVoiceProviderId.brand_voice_id,
                BrandVoiceProviderId.first_order_id,
                BrandVoiceProviderId.status,
                BrandVoiceProviderId.created_at,
                BrandVoiceProviderId.updated_at,
            ),
            "brand_voices": rows(
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
            ),
            "brand_voice_orders": rows(
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
            ),
            "source_assets": rows(
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
            ),
        }
    )


@pytest.fixture
def pricing_closure_snapshot():
    return _pricing_closure_snapshot


@pytest.fixture
def seed_pricing_closure_state(monkeypatch):
    seeded_sessions: list[Session] = []

    def seed(db: Session) -> dict[str, str]:
        from uuid import UUID

        from app.api.deps import BillingSubmissionHeaders
        from app.schemas.brand_voice_orders import BrandVoiceOrderCreateRequest
        from app.services import brand_voice_orders, pricing
        from app.services.billing_quotes import VerifiedQuote, _validated_snapshot

        now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
        secret = "provider-config-secret-value"
        source_url = "https://private.example/source-audio.wav?signature=secret"
        subscription = db.get(Subscription, "subscription-a")
        plan = db.get(Plan, subscription.plan_id)
        user = db.get(User, "user-a")
        subscription.quota_credits_total = 100_000
        plan.code = "huading"
        user.role = "admin"
        db.add_all(
            [
                Asset(
                    id="cli-source-asset",
                    tenant_id="tenant-a",
                    type="audio",
                    source="upload",
                    provider="private-storage",
                    storage_key="tenants/tenant-a/private/source-audio.wav",
                    mime_type="audio/wav",
                    size_bytes=1234,
                    duration_ms=5000,
                    status="ready",
                    metadata_={"source_url": source_url},
                ),
                CreditRate(
                    id="cli-preserved-rate",
                    capability="image",
                    unit="image",
                    credits_per_unit=Decimal("7.0000"),
                    is_active=False,
                ),
                ProviderConfig(
                    id="cli-preserved-provider-config",
                    capability="chat",
                    provider="private-provider",
                    config={
                        "api_key": secret,
                        "source_url": source_url,
                        "nested": {"preserve": "exact-config-content"},
                    },
                    is_active=False,
                ),
            ]
        )
        db.commit()

        operation_name = "doubao_brand_voice_order_create"
        policy = pricing.PRICING_POLICIES[operation_name]
        quote = VerifiedQuote(
            snapshot=_validated_snapshot(
                pricing.build_simple_pricing(
                    policy=policy,
                    rate=pricing.resolve_rate(
                        db,
                        tenant_id=user.tenant_id,
                        policy=policy,
                    ),
                    quantity=Decimal("1"),
                )
            )[1],
            quote_hash="q" * 64,
            pricing_payload_hash="p" * 64,
        )
        order = brand_voice_orders.create_brand_voice_order(
            db,
            user=db.get(User, "user-a"),
            payload=BrandVoiceOrderCreateRequest(
                order_type="create",
                requested_name="CLI Customer Voice",
                source_audio_asset_id="cli-source-asset",
                consent_confirmed=True,
            ),
            verified_quote=quote,
            submission_headers=BillingSubmissionHeaders(
                idempotency_key=UUID("00000000-0000-0000-0000-000000000013"),
                quote_token="quoted",
            ),
            now=now,
        )
        monkeypatch.setattr(settings, "engine_platform_tenant_slugs", {"billing-a"})
        fulfilled = brand_voice_orders.resolve_brand_voice_order(
            db,
            actor=db.get(User, "user-a"),
            order_id=order.id,
            action="fulfill",
            provider_voice_id="cli-customer-provider-id",
            now=now,
        )

        db.expire_all()
        customer = db.get(BrandVoiceProviderId, fulfilled.fulfilled_provider_id)
        subscription = db.get(Subscription, "subscription-a")
        subscription.quota_credits_reserved = 3
        db.add(
            UsageRecord(
                id="cli-preserved-usage",
                tenant_id="tenant-a",
                subscription_id="subscription-a",
                capability="image",
                provider="private-provider",
                model="private-model",
                unit="image",
                quantity=Decimal("1"),
                credits=Decimal("3"),
                cost_cents=0,
                provider_cost_usd=Decimal("0.01000000"),
                provider_usage={"trace": "usage-trace-secret"},
                status="reserved",
            )
        )
        db.commit()
        seeded_sessions.append(db)
        return {
            "secret": secret,
            "source_url": source_url,
            "customer_provider_id": customer.normalized_provider_id,
            "other_provider": "private-provider",
        }

    yield seed

    # The registry and fulfilled order intentionally form the production FK
    # cycle. Clear this in-memory fixture after assertions so metadata teardown
    # does not have to break that committed cycle.
    for db in seeded_sessions:
        db.rollback()
        dbapi_connection = db.connection().connection.driver_connection
        dbapi_connection.execute("PRAGMA foreign_keys=OFF")
        for table in reversed(Base.metadata.sorted_tables):
            dbapi_connection.execute(f'DELETE FROM "{table.name}"')
        dbapi_connection.commit()
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


@pytest.fixture
def seed_plan(auth_db):
    """Seed a default 'basic' plan so register-tenant can attach a subscription."""
    session = auth_db()
    plan = Plan(
        code="basic",
        name="Basic",
        price_cents=0,
        period="monthly",
        quota_credits=10_000_000,
        is_active=True,
    )
    session.add(plan)
    session.commit()
    session.close()
    return plan


@pytest.fixture
def auth_context(auth_db, seed_plan):
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": "acme",
            "tenant_name": "Acme Studio",
            "email": "owner@example.com",
            "password": "secret-pass",
        },
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    token = data["token"]["access_token"]
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "tenant_id": data["tenant"]["id"],
        "user_id": data["user"]["id"],
    }
