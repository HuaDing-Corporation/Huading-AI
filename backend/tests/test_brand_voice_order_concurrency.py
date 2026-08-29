from __future__ import annotations

import ast
import inspect
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import AppError
from app.db.models import (
    Asset,
    Base,
    BillingOperation,
    BrandVoice,
    BrandVoiceOrder,
    BrandVoiceProviderId,
    Tenant,
    User,
)
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
def postgres_registry_factory():
    raw_url = os.getenv("TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("isolated TEST_POSTGRES_URL is required for row-lock race coverage")
    url = make_url(raw_url)
    if url.host not in {"localhost", "127.0.0.1"} or not str(url.database).startswith(
        "huading_pricing_test_"
    ):
        pytest.fail("TEST_POSTGRES_URL must target a local huading_pricing_test_* database")
    engine = create_engine(url, pool_pre_ping=True)
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
