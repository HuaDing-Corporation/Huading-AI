from __future__ import annotations

import ast
import inspect
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import AppError
from app.db.models import (
    AdminAuditLog,
    Asset,
    Base,
    BillingOperation,
    BrandVoice,
    BrandVoiceOrder,
    BrandVoiceProviderId,
    CreditRefundGrant,
    Plan,
    Subscription,
    Tenant,
    UsageRecord,
    User,
)
from app.schemas.brand_voice_orders import BrandVoiceOrderCreateRequest
from app.services import brand_voice_orders


def test_manual_resolve_uses_the_canonical_global_lock_stages() -> None:
    source = inspect.getsource(brand_voice_orders._resolve_in_transaction)
    stages = [
        "lock_tenant_for_subscription_lifecycle",
        "_operation_for_update",
        "refund_subscriptions_for_update",
        "_usage_for_update",
        "decide_credit_refund",
        "_lock_provider_stage",
        "select(BrandVoiceOrder)",
    ]
    offsets = [source.index(stage) for stage in stages]
    assert offsets == sorted(offsets)


def test_provider_readiness_is_rechecked_inside_the_locked_stage_for_both_actions() -> None:
    stage_source = inspect.getsource(brand_voice_orders._lock_provider_stage)
    assert "lock_provider_voice_ids" not in stage_source
    assert "lock_provider_voice_registry_snapshot" not in stage_source
    assert "assert_doubao_registry_ready" in stage_source

    resolve_source = inspect.getsource(brand_voice_orders._resolve_in_transaction)
    assert resolve_source.index("_lock_provider_stage") < resolve_source.index(
        'if action == "fulfill":'
    )

    create_source = inspect.getsource(
        brand_voice_orders._create_brand_voice_order_in_transaction
    )
    assert "assert_doubao_registry_ready" in create_source


def test_resolution_time_is_computed_per_retry_unless_explicit(monkeypatch) -> None:
    class FakeDb:
        @staticmethod
        def get_bind():
            return object()

        @staticmethod
        def rollback() -> None:
            return None

    class AttemptClock:
        calls = 0

        @classmethod
        def now(cls, timezone):
            assert timezone is UTC
            cls.calls += 1
            return datetime(2026, 8, 29, tzinfo=UTC) + timedelta(seconds=cls.calls)

    observed: list[datetime] = []
    monkeypatch.setattr(brand_voice_orders, "datetime", AttemptClock)
    monkeypatch.setattr(
        brand_voice_orders,
        "_resolve_locator",
        lambda db, *, order_id: SimpleNamespace(id=order_id),
    )
    monkeypatch.setattr(brand_voice_orders, "sessionmaker", lambda **kwargs: object())
    monkeypatch.setattr(
        brand_voice_orders,
        "_resolve_in_transaction",
        lambda transaction_db, **kwargs: observed.append(kwargs["transaction_now"])
        or SimpleNamespace(id="resolved"),
    )

    def retry_twice(factory, operation):
        operation(object())
        return operation(object())

    monkeypatch.setattr(
        brand_voice_orders,
        "run_db_transaction_with_retry",
        retry_twice,
    )
    actor = SimpleNamespace(id="admin", tenant_id="admin-tenant")

    brand_voice_orders.resolve_brand_voice_order(
        FakeDb(),
        actor=actor,
        order_id="order-a",
        action="reject",
        rejection_reason="invalid audio",
    )
    explicit = datetime(2026, 8, 30, tzinfo=UTC)
    brand_voice_orders.resolve_brand_voice_order(
        FakeDb(),
        actor=actor,
        order_id="order-b",
        action="reject",
        rejection_reason="invalid audio",
        now=explicit,
    )

    assert observed == [
        datetime(2026, 8, 29, 0, 0, 1, tzinfo=UTC),
        datetime(2026, 8, 29, 0, 0, 2, tzinfo=UTC),
        explicit,
        explicit,
    ]
    assert AttemptClock.calls == 2


def test_fulfill_locks_business_rows_in_canonical_order() -> None:
    source = inspect.getsource(brand_voice_orders._resolve_in_transaction)
    offsets = [
        source.index("select(BrandVoiceOrder)"),
        source.index("voice = BrandVoice("),
        source.index("select(Asset)"),
    ]
    assert offsets == sorted(offsets)


def test_production_code_has_no_unprotected_user_deactivation_writer() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in {"is_active", "status"}
                    and isinstance(target.value, ast.Name)
                    and target.value.id in {"user", "locked_user", "current_user"}
                ):
                    offenders.append(f"{path.relative_to(app_root)}:{node.lineno}")
    assert offenders == []


@pytest.fixture
def postgres_registry_factory(monkeypatch):
    from app.services import provider_voice_registry

    raw_url = os.getenv("TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("isolated TEST_POSTGRES_URL is required for row-lock race coverage")
    url = make_url(raw_url)
    if url.host not in {"localhost", "127.0.0.1"} or not str(url.database).startswith(
        "huading_pricing_test_"
    ):
        pytest.fail("TEST_POSTGRES_URL must target a local huading_pricing_test_* database")
    engine = create_engine(url, pool_pre_ping=True)
    monkeypatch.setattr(provider_voice_registry.settings, "environment", "test")
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        [],
    )
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _seed_postgres_order_lifecycle(factory) -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    with factory.begin() as db:
        db.add(Tenant(id="lifecycle-tenant", slug="lifecycle", name="Lifecycle"))
        db.add(
            Plan(
                id="lifecycle-plan",
                code="huading",
                name="Huading",
                price_cents=0,
                period="monthly",
                quota_credits=100_000,
            )
        )
        db.flush()
        db.add(
            User(
                id="lifecycle-user",
                tenant_id="lifecycle-tenant",
                email="lifecycle@example.com",
                password_hash="hash",
                role="admin",
            )
        )
        db.add(
            Subscription(
                id="lifecycle-subscription",
                tenant_id="lifecycle-tenant",
                plan_id="lifecycle-plan",
                status="active",
                period_start=now - timedelta(days=1),
                period_end=now + timedelta(days=30),
                quota_credits_total=100_000,
                quota_credits_used=0,
                quota_credits_reserved=0,
            )
        )
        db.add_all(
            [
                Asset(
                    id="lifecycle-create-audio",
                    tenant_id="lifecycle-tenant",
                    type="audio",
                    source="upload",
                    storage_key="lifecycle/create.wav",
                    mime_type="audio/wav",
                    duration_ms=10_000,
                    status="ready",
                ),
                Asset(
                    id="lifecycle-renew-old-audio",
                    tenant_id="lifecycle-tenant",
                    type="audio",
                    source="upload",
                    storage_key="lifecycle/renew-old.wav",
                    mime_type="audio/wav",
                    duration_ms=10_000,
                    status="ready",
                ),
                Asset(
                    id="lifecycle-renew-new-audio",
                    tenant_id="lifecycle-tenant",
                    type="audio",
                    source="upload",
                    storage_key="lifecycle/renew-new.wav",
                    mime_type="audio/wav",
                    duration_ms=10_000,
                    status="ready",
                ),
            ]
        )
        db.flush()
        db.add(
            BrandVoice(
                id="lifecycle-renew-voice",
                tenant_id="lifecycle-tenant",
                owner_user_id="lifecycle-user",
                name="Expired lifecycle voice",
                source_audio_asset_id="lifecycle-renew-old-audio",
                provider="doubao-voice-clone",
                speaker_id="lifecycle-old-provider-id",
                status="ready",
                consent_confirmed=True,
                expires_at=now - timedelta(days=1),
            )
        )


def _postgres_verified_quote(db, *, payload: BrandVoiceOrderCreateRequest):
    from decimal import Decimal

    from app.services.billing_quotes import VerifiedQuote, _validated_snapshot
    from app.services.pricing import PRICING_POLICIES, build_simple_pricing, resolve_rate

    operation = brand_voice_orders.brand_voice_order_operation(payload)
    draft = build_simple_pricing(
        policy=PRICING_POLICIES[operation],
        rate=resolve_rate(
            db,
            tenant_id="lifecycle-tenant",
            policy=PRICING_POLICIES[operation],
        ),
        quantity=Decimal("1"),
    )
    return VerifiedQuote(
        snapshot=_validated_snapshot(draft)[1],
        quote_hash="a" * 64,
        pricing_payload_hash="b" * 64,
    )


def _create_postgres_order(db, *, renew: bool = False) -> BrandVoiceOrder:
    from uuid import uuid4

    from app.api.deps import BillingSubmissionHeaders

    payload = BrandVoiceOrderCreateRequest(
        order_type="renew" if renew else "create",
        requested_name="Lifecycle renewal" if renew else "Lifecycle create",
        source_audio_asset_id=(
            "lifecycle-renew-new-audio" if renew else "lifecycle-create-audio"
        ),
        consent_confirmed=True,
        existing_brand_voice_id="lifecycle-renew-voice" if renew else None,
    )
    return brand_voice_orders._create_brand_voice_order_in_transaction(
        db,
        user=db.get(User, "lifecycle-user"),
        payload=payload,
        verified_quote=_postgres_verified_quote(db, payload=payload),
        submission_headers=BillingSubmissionHeaders(
            idempotency_key=uuid4(),
            quote_token="quoted",
        ),
        now=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
    )


def _run_ordered_postgres_race(
    factory,
    *,
    first: str,
    prelock: dict[str, Callable[[Session], None]],
    actions: dict[str, Callable[[Session], None]],
) -> dict[str, tuple[str, str | None]]:
    first_locked = Event()
    barrier = Barrier(2)

    def run(name: str) -> tuple[str, str | None]:
        with factory() as db:
            try:
                if name == first:
                    prelock[name](db)
                    first_locked.set()
                else:
                    assert first_locked.wait(timeout=10)
                barrier.wait(timeout=10)
                actions[name](db)
                db.commit()
                return ("committed", None)
            except AppError as exc:
                db.rollback()
                return ("rejected", exc.code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {name: pool.submit(run, name) for name in actions}
        return {name: future.result(timeout=30) for name, future in futures.items()}


def _prelock_tenant(db) -> None:
    from app.services.subscription import lock_tenant_for_subscription_lifecycle

    lock_tenant_for_subscription_lifecycle(db, tenant_id="lifecycle-tenant")


def _prelock_create_resource(db, *, asset_id: str, voice_id: str | None = None) -> None:
    from app.services import quota
    from app.services.subscription import lock_tenant_for_subscription_lifecycle

    lock_tenant_for_subscription_lifecycle(db, tenant_id="lifecycle-tenant")
    quota.lock_active_subscription(db, tenant_id="lifecycle-tenant")
    db.scalar(
        select(User)
        .where(User.id == "lifecycle-user")
        .with_for_update()
    )
    db.scalar(select(Asset).where(Asset.id == asset_id).with_for_update())
    if voice_id is not None:
        db.scalar(select(BrandVoice).where(BrandVoice.id == voice_id).with_for_update())


@pytest.mark.parametrize("first", ["create", "deactivate"])
def test_postgresql_order_create_vs_tenant_deactivation_is_always_legal(
    postgres_registry_factory,
    first,
) -> None:
    from app.services import admin_console

    _seed_postgres_order_lifecycle(postgres_registry_factory)

    def deactivate(db) -> None:
        admin_console.change_tenant_status(
            db,
            actor=db.get(User, "lifecycle-user"),
            tenant_id="lifecycle-tenant",
            active=False,
            reason="race test",
        )

    outcomes = _run_ordered_postgres_race(
        postgres_registry_factory,
        first=first,
        prelock={"create": _prelock_tenant, "deactivate": _prelock_tenant},
        actions={
            "create": lambda db: _create_postgres_order(db),
            "deactivate": deactivate,
        },
    )

    assert outcomes[first] == ("committed", None)
    assert outcomes["deactivate" if first == "create" else "create"] == (
        "rejected",
        "BRAND_VOICE_ORDER_NOT_CANCELLABLE"
        if first == "create"
        else "ORDER_PRINCIPAL_INACTIVE",
    )
    with postgres_registry_factory() as db:
        tenant = db.get(Tenant, "lifecycle-tenant")
        awaiting = db.scalar(
            select(func.count())
            .select_from(BrandVoiceOrder)
            .where(BrandVoiceOrder.status == "awaiting_fulfillment")
        )
        assert (tenant.status, awaiting) in {("active", 1), ("suspended", 0)}


@pytest.mark.parametrize("first", ["create", "deactivate"])
def test_postgresql_order_create_vs_future_user_deactivation_is_always_legal(
    postgres_registry_factory,
    first,
) -> None:
    from app.services.asset_retention import assert_no_awaiting_orders_for_principal

    _seed_postgres_order_lifecycle(postgres_registry_factory)

    def prelock_user_deactivation(db) -> None:
        _prelock_tenant(db)
        db.scalar(
            select(User)
            .where(User.id == "lifecycle-user")
            .with_for_update()
        )

    def deactivate(db) -> None:
        _prelock_tenant(db)
        user = db.scalar(
            select(User)
            .where(User.id == "lifecycle-user")
            .with_for_update()
        )
        assert_no_awaiting_orders_for_principal(
            db,
            tenant_id="lifecycle-tenant",
            user_id=user.id,
        )
        user.status = "disabled"
        user.is_active = False

    outcomes = _run_ordered_postgres_race(
        postgres_registry_factory,
        first=first,
        prelock={"create": _prelock_tenant, "deactivate": prelock_user_deactivation},
        actions={
            "create": lambda db: _create_postgres_order(db),
            "deactivate": deactivate,
        },
    )

    assert outcomes[first] == ("committed", None)
    assert outcomes["deactivate" if first == "create" else "create"] == (
        "rejected",
        "BRAND_VOICE_ORDER_NOT_CANCELLABLE"
        if first == "create"
        else "ORDER_PRINCIPAL_INACTIVE",
    )
    with postgres_registry_factory() as db:
        user = db.get(User, "lifecycle-user")
        awaiting = db.scalar(
            select(func.count())
            .select_from(BrandVoiceOrder)
            .where(BrandVoiceOrder.status == "awaiting_fulfillment")
        )
        assert (user.status, user.is_active, awaiting) in {
            ("active", True, 1),
            ("disabled", False, 0),
        }


@pytest.mark.parametrize("first", ["create", "delete"])
def test_postgresql_order_create_vs_source_asset_deletion_is_always_legal(
    postgres_registry_factory,
    first,
) -> None:
    from app.services.asset_retention import assert_asset_not_held_by_manual_order

    _seed_postgres_order_lifecycle(postgres_registry_factory)

    def prelock_create(db) -> None:
        _prelock_create_resource(db, asset_id="lifecycle-create-audio")

    def prelock_delete(db) -> None:
        db.scalar(
            select(Asset)
            .where(Asset.id == "lifecycle-create-audio")
            .with_for_update()
        )

    def delete(db) -> None:
        asset = db.scalar(
            select(Asset)
            .where(Asset.id == "lifecycle-create-audio")
            .with_for_update()
        )
        assert_asset_not_held_by_manual_order(db, asset_id=asset.id)
        db.delete(asset)
        db.flush()

    outcomes = _run_ordered_postgres_race(
        postgres_registry_factory,
        first=first,
        prelock={"create": prelock_create, "delete": prelock_delete},
        actions={"create": lambda db: _create_postgres_order(db), "delete": delete},
    )

    assert outcomes[first] == ("committed", None)
    assert outcomes["delete" if first == "create" else "create"] == (
        "rejected",
        "BRAND_VOICE_ORDER_NOT_CANCELLABLE"
        if first == "create"
        else "SOURCE_AUDIO_ASSET_NOT_FOUND",
    )
    with postgres_registry_factory() as db:
        awaiting = db.scalar(select(func.count()).select_from(BrandVoiceOrder))
        asset_exists = db.get(Asset, "lifecycle-create-audio") is not None
        assert (awaiting, asset_exists) in {(1, True), (0, False)}


@pytest.mark.parametrize("first", ["create", "delete"])
def test_postgresql_renewal_create_vs_target_voice_deletion_is_always_legal(
    postgres_registry_factory,
    first,
) -> None:
    from app.services.asset_retention import assert_brand_voice_not_held_by_manual_order

    _seed_postgres_order_lifecycle(postgres_registry_factory)

    def prelock_create(db) -> None:
        _prelock_create_resource(
            db,
            asset_id="lifecycle-renew-new-audio",
            voice_id="lifecycle-renew-voice",
        )

    def prelock_delete(db) -> None:
        db.scalar(
            select(BrandVoice)
            .where(BrandVoice.id == "lifecycle-renew-voice")
            .with_for_update()
        )

    def delete(db) -> None:
        voice = db.scalar(
            select(BrandVoice)
            .where(BrandVoice.id == "lifecycle-renew-voice")
            .with_for_update()
        )
        assert_brand_voice_not_held_by_manual_order(db, brand_voice_id=voice.id)
        voice.deleted_at = datetime(2026, 8, 29, 12, 1, tzinfo=UTC)

    outcomes = _run_ordered_postgres_race(
        postgres_registry_factory,
        first=first,
        prelock={"create": prelock_create, "delete": prelock_delete},
        actions={
            "create": lambda db: _create_postgres_order(db, renew=True),
            "delete": delete,
        },
    )

    assert outcomes[first] == ("committed", None)
    assert outcomes["delete" if first == "create" else "create"] == (
        "rejected",
        "BRAND_VOICE_ORDER_NOT_CANCELLABLE"
        if first == "create"
        else "BRAND_VOICE_RENEWAL_NOT_ELIGIBLE",
    )
    with postgres_registry_factory() as db:
        awaiting = db.scalar(select(func.count()).select_from(BrandVoiceOrder))
        voice = db.get(BrandVoice, "lifecycle-renew-voice")
        assert (awaiting, voice.deleted_at is None) in {(1, True), (0, False)}


@pytest.mark.parametrize("first", ["fulfill", "reject"])
def test_postgresql_fulfill_vs_reject_has_one_terminal_financial_change(
    postgres_registry_factory,
    first,
) -> None:
    _seed_postgres_order_lifecycle(postgres_registry_factory)
    with postgres_registry_factory.begin() as db:
        order = _create_postgres_order(db)
        order_id = order.id
    with postgres_registry_factory() as db:
        locator = brand_voice_orders._resolve_locator(db, order_id=order_id)

    def resolve(db, action: str) -> None:
        brand_voice_orders._resolve_in_transaction(
            db,
            locator=locator,
            actor_id="lifecycle-user",
            actor_tenant_id="lifecycle-tenant",
            action=action,
            provider_voice_id="lifecycle-new-provider-id" if action == "fulfill" else None,
            rejection_reason="race rejection" if action == "reject" else None,
            transaction_now=datetime(2026, 8, 29, 12, 2, tzinfo=UTC),
        )

    outcomes = _run_ordered_postgres_race(
        postgres_registry_factory,
        first=first,
        prelock={"fulfill": _prelock_tenant, "reject": _prelock_tenant},
        actions={
            "fulfill": lambda db: resolve(db, "fulfill"),
            "reject": lambda db: resolve(db, "reject"),
        },
    )

    assert outcomes[first] == ("committed", None)
    assert outcomes["reject" if first == "fulfill" else "fulfill"] == (
        "rejected",
        "BRAND_VOICE_ORDER_ALREADY_RESOLVED",
    )
    with postgres_registry_factory() as db:
        order = db.get(BrandVoiceOrder, order_id)
        operation = db.get(BillingOperation, order.billing_operation_id)
        usage = db.scalar(
            select(UsageRecord).where(UsageRecord.billing_operation_id == operation.id)
        )
        subscription = db.get(Subscription, usage.subscription_id)
        assert order.status in {"fulfilled", "rejected"}
        assert operation.status == "completed"
        assert usage.status == ("settled" if order.status == "fulfilled" else "released")
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == (
            30_000 if order.status == "fulfilled" else 0
        )
        assert db.scalar(select(func.count()).select_from(AdminAuditLog)) == 1
        assert db.scalar(select(func.count()).select_from(CreditRefundGrant)) == 0
        assert db.scalar(select(func.count()).select_from(BrandVoiceProviderId)) == (
            1 if order.status == "fulfilled" else 0
        )


def _seed_postgres_customer_claims(factory) -> None:
    now = datetime.now(UTC)
    with factory.begin() as db:
        db.add(Tenant(id="race-tenant", slug="race-tenant", name="Race Tenant"))
        db.flush()
        db.add_all(
            [
                User(
                    id="race-user-a",
                    tenant_id="race-tenant",
                    email="race-a@example.com",
                    password_hash="hash",
                    role="creator",
                ),
                User(
                    id="race-user-b",
                    tenant_id="race-tenant",
                    email="race-b@example.com",
                    password_hash="hash",
                    role="creator",
                ),
            ]
        )
        db.flush()
        for suffix in ("a", "b"):
            db.add_all(
                [
                    Asset(
                        id=f"race-asset-{suffix}",
                        tenant_id="race-tenant",
                        type="audio",
                        source="upload",
                        storage_key=f"race/{suffix}.wav",
                        status="ready",
                    ),
                    BillingOperation(
                        id=f"race-billing-{suffix}",
                        tenant_id="race-tenant",
                        user_id=f"race-user-{suffix}",
                        operation="cosyvoice_brand_voice_create",
                        idempotency_key=f"race-key-{suffix}",
                        request_hash=suffix * 64,
                        quote_hash=suffix * 64,
                        pricing_snapshot={},
                        requested_credits=0,
                        settled_credits=0,
                        released_credits=0,
                        status="completed",
                        completion_kind="succeeded",
                        completed_at=now,
                    ),
                    BrandVoice(
                        id=f"race-voice-{suffix}",
                        tenant_id="race-tenant",
                        owner_user_id=f"race-user-{suffix}",
                        name=f"Race {suffix}",
                        status="ready",
                        consent_confirmed=True,
                    ),
                ]
            )
        db.flush()
        for suffix in ("a", "b"):
            db.add(
                BrandVoiceOrder(
                    id=f"race-order-{suffix}",
                    tenant_id="race-tenant",
                    user_id=f"race-user-{suffix}",
                    order_type="create",
                    requested_name=f"Race {suffix}",
                    source_audio_asset_id=f"race-asset-{suffix}",
                    source_metadata_snapshot={},
                    consent_confirmed_at=now,
                    billing_operation_id=f"race-billing-{suffix}",
                    status="awaiting_fulfillment",
                )
            )


def test_postgresql_competing_customer_claims_have_one_winner(
    postgres_registry_factory,
) -> None:
    from app.services import provider_voice_registry

    _seed_postgres_customer_claims(postgres_registry_factory)
    barrier = Barrier(2)

    def claim(suffix: str) -> str:
        with postgres_registry_factory() as db:
            barrier.wait(timeout=10)
            try:
                provider_voice_registry.claim_customer_provider_voice_id(
                    db,
                    provider_voice_id="race-shared-provider-id",
                    brand_voice_id=f"race-voice-{suffix}",
                    order_id=f"race-order-{suffix}",
                )
                db.commit()
                return "claimed"
            except AppError as exc:
                db.rollback()
                assert exc.code == "PROVIDER_VOICE_ID_CONFLICT"
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ("a", "b")))

    assert sorted(outcomes) == ["claimed", "conflict"]
    with postgres_registry_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(BrandVoiceProviderId)
                .where(
                    BrandVoiceProviderId.normalized_provider_id
                    == "race-shared-provider-id"
                )
            )
            == 1
        )


def test_postgresql_concurrent_official_registration_is_idempotent(
    postgres_registry_factory,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry

    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["race-official-id"],
    )
    barrier = Barrier(2)

    def register(_index: int) -> str:
        with postgres_registry_factory() as db:
            barrier.wait(timeout=10)
            row = provider_voice_registry.register_official_provider_voice_ids(
                db,
                provider_voice_ids=["race-official-id"],
            )[0]
            db.commit()
            return row.normalized_provider_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(register, (1, 2)))

    assert outcomes == ["race-official-id", "race-official-id"]
    with postgres_registry_factory() as db:
        assert db.scalar(select(func.count()).select_from(BrandVoiceProviderId)) == 1
