# ruff: noqa: E501
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.schemas.brand_voice_orders import (
    BrandVoiceOrderCreateRequest,
    BrandVoiceOrderResolveRequest,
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260829_0038_manual_brand_voice.py"
)


def _migration():
    assert MIGRATION_PATH.exists(), "0038 manual brand voice migration is required"
    spec = importlib.util.spec_from_file_location("manual_brand_voice_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_0037_schema(connection) -> None:
    for ddl in (
        "CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)",
        "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) REFERENCES tenants(id))",
        "CREATE TABLE assets (id VARCHAR(36) PRIMARY KEY)",
        "CREATE TABLE subscriptions (id VARCHAR(36) PRIMARY KEY)",
        """CREATE TABLE billing_operations (
            id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) REFERENCES tenants(id),
            user_id VARCHAR(36) REFERENCES users(id))""",
        """CREATE TABLE brand_voices (
            id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), name VARCHAR(30) NOT NULL,
            source_audio_asset_id VARCHAR(36), provider VARCHAR(40) NOT NULL,
            speaker_id VARCHAR(160), status VARCHAR(32) NOT NULL,
            consent_confirmed BOOLEAN NOT NULL, consent_confirmed_at DATETIME,
            error_code VARCHAR(40), error_message TEXT, created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL, deleted_at DATETIME,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id),
            FOREIGN KEY (source_audio_asset_id) REFERENCES assets(id))""",
        """CREATE TABLE admin_audit_logs (
            id VARCHAR(36) PRIMARY KEY, actor_user_id VARCHAR(36) NOT NULL,
            actor_tenant_id VARCHAR(36) NOT NULL, action VARCHAR(32) NOT NULL,
            target_tenant_id VARCHAR(36), target_id VARCHAR(36), before JSON, after JSON,
            reason TEXT, created_at DATETIME NOT NULL,
            CONSTRAINT ck_admin_audit_logs_action CHECK (action IN
            ('credits_adjust', 'plan_change', 'status_change', 'voice_slot_assign', 'task_retry')),
            FOREIGN KEY (actor_user_id) REFERENCES users(id),
            FOREIGN KEY (actor_tenant_id) REFERENCES tenants(id))""",
    ):
        connection.execute(sa.text(ddl))


@pytest.fixture
def upgraded_engine():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        _create_0037_schema(connection)
        connection.execute(sa.text("INSERT INTO tenants (id) VALUES ('tenant')"))
        connection.execute(
            sa.text(
                "INSERT INTO users (id, tenant_id) VALUES ('user', 'tenant'), ('resolver', 'tenant')"
            )
        )
        connection.execute(sa.text("INSERT INTO assets (id) VALUES ('asset')"))
        connection.execute(
            sa.text("INSERT INTO subscriptions (id) VALUES ('source-sub'), ('target-sub')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO brand_voices (id, tenant_id, name, provider, status, "
                "consent_confirmed, created_at, updated_at) VALUES "
                "('historic', 'tenant', 'Historic', 'legacy', 'ready', 1, '2025-01-01', '2025-01-01')"  # noqa: E501
            )
        )
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
    try:
        yield engine
    finally:
        engine.dispose()


def _order(connection, **changes):
    values = {
        "id": "order",
        "tenant_id": "tenant",
        "user_id": "user",
        "order_type": "create",
        "requested_name": "Voice",
        "source_audio_asset_id": "asset",
        "source_metadata_snapshot": "{}",
        "consent_confirmed_at": "2025-01-01",
        "existing_brand_voice_id": None,
        "billing_operation_id": "operation",
        "status": "awaiting_fulfillment",
        "fulfilled_brand_voice_id": None,
        "fulfilled_provider_id": None,
        "resolver_user_id": None,
        "fulfilled_at": None,
        "rejected_at": None,
        "rejection_reason": None,
        "created_at": "2025-01-01",
        "updated_at": "2025-01-01",
    }
    values.update(changes)
    connection.execute(
        sa.text(
            "INSERT OR IGNORE INTO billing_operations (id, tenant_id, user_id) "
            "VALUES (:id, 'tenant', 'user')"
        ),
        {"id": values["billing_operation_id"]},
    )
    connection.execute(
        sa.text("""INSERT INTO brand_voice_orders (
            id, tenant_id, user_id, order_type, requested_name, source_audio_asset_id,
            source_metadata_snapshot, consent_confirmed_at, existing_brand_voice_id,
            billing_operation_id, status, fulfilled_brand_voice_id, fulfilled_provider_id,
            resolver_user_id, fulfilled_at, rejected_at, rejection_reason, created_at, updated_at)
            VALUES (:id, :tenant_id, :user_id, :order_type, :requested_name, :source_audio_asset_id,
            :source_metadata_snapshot, :consent_confirmed_at, :existing_brand_voice_id,
            :billing_operation_id, :status, :fulfilled_brand_voice_id, :fulfilled_provider_id,
            :resolver_user_id, :fulfilled_at, :rejected_at, :rejection_reason, :created_at, :updated_at)"""),  # noqa: E501
        values,
    )


def test_models_and_migration_expose_manual_delivery_contract():
    assert hasattr(models, "BrandVoiceOrder")
    assert hasattr(models, "BrandVoiceProviderId")
    assert hasattr(models, "CreditRefundGrant")
    assert {"owner_user_id", "activated_at", "expires_at"} <= set(
        models.BrandVoice.__table__.columns.keys()
    )
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("20260829_0038", "20260829_0037")


def test_upgrade_leaves_historic_voice_and_financial_history_untouched(upgraded_engine):
    with upgraded_engine.begin() as connection:
        row = (
            connection.execute(
                sa.text(
                    "SELECT owner_user_id, activated_at, expires_at FROM brand_voices WHERE id = 'historic'"  # noqa: E501
                )
            )
            .mappings()
            .one()
        )
        assert dict(row) == {"owner_user_id": None, "activated_at": None, "expires_at": None}
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM brand_voice_orders")) == 0
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM billing_operations")) == 0


def test_order_checks_and_awaiting_renewal_index(upgraded_engine):
    with upgraded_engine.begin() as connection:
        _order(connection)
        invalid_orders = (
            {
                "id": "bad-create",
                "billing_operation_id": "op-1",
                "existing_brand_voice_id": "historic",
            },
            {"id": "bad-renew", "billing_operation_id": "op-2", "order_type": "renew"},
            {"id": "bad-fulfilled", "billing_operation_id": "op-3", "status": "fulfilled"},
            {
                "id": "bad-rejected",
                "billing_operation_id": "op-4",
                "status": "rejected",
                "rejected_at": "2025-01-02",
            },
            {
                "id": "bad-renew-result",
                "billing_operation_id": "op-5",
                "order_type": "renew",
                "existing_brand_voice_id": "historic",
                "status": "fulfilled",
                "fulfilled_brand_voice_id": "other",
                "fulfilled_provider_id": "provider",
                "resolver_user_id": "resolver",
                "fulfilled_at": "2025-01-02",
            },
        )
        for changes in invalid_orders:
            with pytest.raises(IntegrityError):
                _order(connection, **changes)
        _order(
            connection,
            id="renew-1",
            billing_operation_id="renew-op-1",
            order_type="renew",
            existing_brand_voice_id="historic",
        )
        with pytest.raises(IntegrityError):
            _order(
                connection,
                id="renew-2",
                billing_operation_id="renew-op-2",
                order_type="renew",
                existing_brand_voice_id="historic",
            )


def test_provider_refund_and_audit_constraints(upgraded_engine):
    with upgraded_engine.begin() as connection:
        for operation_id in ("refund-op", "refund-zero", "refund-applied"):
            connection.execute(
                sa.text(
                    "INSERT INTO billing_operations (id, tenant_id, user_id) "
                    "VALUES (:id, 'tenant', 'user')"
                ),
                {"id": operation_id},
            )
        connection.execute(
            sa.text("""INSERT INTO brand_voice_provider_ids
            (id, provider, normalized_provider_id, kind, status, created_at, updated_at)
            VALUES ('retired', 'p', 'permanent-id', 'customer', 'retired', '2025-01-01', '2025-01-01')""")  # noqa: E501
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text("""INSERT INTO brand_voice_provider_ids
                (id, provider, normalized_provider_id, kind, status, created_at, updated_at)
                VALUES ('reuse', 'other', 'permanent-id', 'official', 'active', '2025-01-01', '2025-01-01')""")  # noqa: E501
            )
        connection.execute(
            sa.text("""INSERT INTO credit_refund_grants
            (id, billing_operation_id, tenant_id, user_id, source_subscription_id,
            amount_credits, status, created_at)
            VALUES ('pending', 'refund-op', 'tenant', 'user', 'source-sub', 1, 'pending', '2025-01-01')""")  # noqa: E501
        )
        for values in (
            "'duplicate', 'refund-op', 'tenant', 'user', 'source-sub', 2, 'pending', '2025-01-01'",
            "'zero', 'refund-zero', 'tenant', 'user', 'source-sub', 0, 'pending', '2025-01-01'",
            "'invalid-applied', 'refund-applied', 'tenant', 'user', 'source-sub', 2, 'applied', '2025-01-01'",  # noqa: E501
        ):
            with pytest.raises(IntegrityError):
                connection.execute(
                    sa.text(
                        """INSERT INTO credit_refund_grants
                    (id, billing_operation_id, tenant_id, user_id, source_subscription_id,
                    amount_credits, status, created_at)
                    VALUES ("""
                        + values
                        + ")"
                    )
                )
    owner_fk = next(
        fk for fk in models.BrandVoice.__table__.foreign_keys if fk.parent.name == "owner_user_id"
    )
    tables = (
        models.BrandVoiceOrder.__table__,
        models.BrandVoiceProviderId.__table__,
        models.CreditRefundGrant.__table__,
    )
    assert owner_fk.ondelete == "RESTRICT"
    assert all(fk.ondelete == "RESTRICT" for table in tables for fk in table.foreign_keys)
    audit = " ".join(
        str(c.sqltext)
        for c in models.AdminAuditLog.__table__.constraints
        if isinstance(c, sa.CheckConstraint)
    )
    for action in (
        "brand_voice_order_audio_access",
        "brand_voice_order_fulfill",
        "brand_voice_order_reject",
    ):
        assert action in audit


@pytest.mark.parametrize("amount", ("+inf", "abc", 1.5, 0, -1))
def test_refund_amount_requires_a_positive_integer_value(upgraded_engine, amount):
    with upgraded_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO billing_operations (id, tenant_id, user_id) "
                "VALUES (:id, 'tenant', 'user')"
            ),
            {"id": f"operation-{amount}"},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO credit_refund_grants "
                    "(id, billing_operation_id, tenant_id, user_id, source_subscription_id, "
                    "amount_credits, status, created_at) VALUES "
                    "(:id, :operation, 'tenant', 'user', 'source-sub', :amount, 'pending', "
                    "'2025-01-01')"
                ),
                {"id": f"refund-{amount}", "operation": f"operation-{amount}", "amount": amount},
            )


@pytest.mark.parametrize(
    ("request_type", "payload"),
    (
        (
            BrandVoiceOrderCreateRequest,
            {
                "order_type": "renew",
                "requested_name": "Voice",
                "source_audio_asset_id": "asset",
                "consent_confirmed": True,
                "existing_brand_voice_id": "   ",
            },
        ),
        (
            BrandVoiceOrderResolveRequest,
            {
                "status": "fulfilled",
                "fulfilled_brand_voice_id": "   ",
                "fulfilled_provider_id": "provider",
            },
        ),
        (
            BrandVoiceOrderResolveRequest,
            {
                "status": "fulfilled",
                "fulfilled_brand_voice_id": "voice",
                "fulfilled_provider_id": "   ",
            },
        ),
    ),
)
def test_order_schemas_reject_blank_identifiers(request_type, payload):
    with pytest.raises(ValidationError):
        request_type.model_validate(payload)


def test_order_schemas_strip_supplied_identifiers():
    create = BrandVoiceOrderCreateRequest.model_validate(
        {
            "order_type": "renew",
            "requested_name": " Voice ",
            "source_audio_asset_id": " asset ",
            "consent_confirmed": True,
            "existing_brand_voice_id": " existing ",
        }
    )
    resolve = BrandVoiceOrderResolveRequest.model_validate(
        {
            "status": "fulfilled",
            "fulfilled_brand_voice_id": " voice ",
            "fulfilled_provider_id": " provider ",
        }
    )
    assert (create.source_audio_asset_id, create.existing_brand_voice_id) == ("asset", "existing")
    assert (resolve.fulfilled_brand_voice_id, resolve.fulfilled_provider_id) == (
        "voice",
        "provider",
    )


def test_new_foreign_keys_restrict_real_parent_deletes(upgraded_engine):
    with upgraded_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE brand_voices SET owner_user_id = 'user' WHERE id = 'historic'")
        )
        connection.execute(
            sa.text(
                "INSERT INTO brand_voice_provider_ids "
                "(id, provider, normalized_provider_id, kind, brand_voice_id, status, created_at, "
                "updated_at) VALUES ('provider-live', 'p', 'provider-live', 'customer', 'historic', "
                "'active', '2025-01-01', '2025-01-01')"
            )
        )
        _order(
            connection,
            id="fulfilled-renewal",
            billing_operation_id="fulfilled-operation",
            order_type="renew",
            existing_brand_voice_id="historic",
            status="fulfilled",
            fulfilled_brand_voice_id="historic",
            fulfilled_provider_id="provider-live",
            resolver_user_id="resolver",
            fulfilled_at="2025-01-02",
        )
        connection.execute(
            sa.text(
                "UPDATE brand_voice_provider_ids SET first_order_id = 'fulfilled-renewal' "
                "WHERE id = 'provider-live'"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO billing_operations (id, tenant_id, user_id) "
                "VALUES ('refund-operation', 'tenant', 'user')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO credit_refund_grants "
                "(id, billing_operation_id, tenant_id, user_id, source_subscription_id, "
                "target_subscription_id, amount_credits, status, created_at, applied_at) VALUES "
                "('refund-live', 'refund-operation', 'tenant', 'user', 'source-sub', 'target-sub', "
                "1, 'applied', '2025-01-01', '2025-01-02')"
            )
        )
        for statement in (
            "DELETE FROM tenants WHERE id = 'tenant'",
            "DELETE FROM users WHERE id = 'user'",
            "DELETE FROM users WHERE id = 'resolver'",
            "DELETE FROM assets WHERE id = 'asset'",
            "DELETE FROM brand_voices WHERE id = 'historic'",
            "DELETE FROM brand_voice_provider_ids WHERE id = 'provider-live'",
            "DELETE FROM billing_operations WHERE id = 'fulfilled-operation'",
            "DELETE FROM billing_operations WHERE id = 'refund-operation'",
            "DELETE FROM subscriptions WHERE id = 'source-sub'",
            "DELETE FROM subscriptions WHERE id = 'target-sub'",
        ):
            with pytest.raises(IntegrityError):
                connection.execute(sa.text(statement))


@pytest.fixture
def postgres_manual_voice_schema():
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for real PostgreSQL manual voice tests.")

    engine = sa.create_engine(database_url, pool_pre_ping=True)
    schema = f"manual_brand_voice_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        for statement in (
            "CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)",
            "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)",
            "CREATE TABLE assets (id VARCHAR(36) PRIMARY KEY)",
            "CREATE TABLE subscriptions (id VARCHAR(36) PRIMARY KEY)",
            "CREATE TABLE billing_operations (id VARCHAR(36) PRIMARY KEY)",
            """CREATE TABLE brand_voices (
                id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), name VARCHAR(30) NOT NULL,
                source_audio_asset_id VARCHAR(36), provider VARCHAR(40) NOT NULL,
                speaker_id VARCHAR(160), status VARCHAR(32) NOT NULL,
                consent_confirmed BOOLEAN NOT NULL, consent_confirmed_at TIMESTAMPTZ,
                error_code VARCHAR(40), error_message TEXT, created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL, deleted_at TIMESTAMPTZ)""",
            """CREATE TABLE admin_audit_logs (
                id VARCHAR(36) PRIMARY KEY, actor_user_id VARCHAR(36) NOT NULL,
                actor_tenant_id VARCHAR(36) NOT NULL, action VARCHAR(32) NOT NULL,
                target_tenant_id VARCHAR(36), target_id VARCHAR(36), before JSONB, after JSONB,
                reason TEXT, created_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT ck_admin_audit_logs_action CHECK (action IN
                ('credits_adjust', 'plan_change', 'status_change', 'voice_slot_assign', 'task_retry')))""",
        ):
            connection.execute(sa.text(statement))
        connection.execute(sa.text("INSERT INTO tenants (id) VALUES ('tenant')"))
        connection.execute(sa.text("INSERT INTO users (id) VALUES ('user'), ('resolver')"))
        connection.execute(sa.text("INSERT INTO assets (id) VALUES ('asset')"))
        connection.execute(sa.text("INSERT INTO subscriptions (id) VALUES ('source-sub')"))
        connection.execute(sa.text("INSERT INTO billing_operations (id) VALUES ('operation')"))
        connection.execute(
            sa.text(
                "INSERT INTO brand_voices (id, tenant_id, name, provider, status, "
                "consent_confirmed, created_at, updated_at) VALUES "
                "('voice', 'tenant', 'Voice', 'p', 'ready', TRUE, now(), now())"
            )
        )
    try:
        yield engine, schema
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_postgres_executes_manual_voice_schema_contract(postgres_manual_voice_schema):
    engine, schema = postgres_manual_voice_schema
    with engine.begin() as connection:
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO brand_voice_provider_ids "
                "(id, provider, normalized_provider_id, kind, brand_voice_id, status) VALUES "
                "('provider', 'p', 'provider', 'customer', 'voice', 'active')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO brand_voice_orders "
                "(id, tenant_id, user_id, order_type, requested_name, source_audio_asset_id, "
                "source_metadata_snapshot, consent_confirmed_at, existing_brand_voice_id, "
                "billing_operation_id, status) VALUES "
                "('order', 'tenant', 'user', 'renew', 'Voice', 'asset', '{\"safe\": true}', "
                "now(), 'voice', 'operation', 'awaiting_fulfillment')"
            )
        )
        connection.execute(
            sa.text(
                "UPDATE brand_voice_provider_ids SET first_order_id = 'order' WHERE id = 'provider'"
            )
        )

    def assert_rejected(statement: str) -> None:
        with pytest.raises(sa.exc.DBAPIError):
            with engine.begin() as connection:
                connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
                connection.execute(sa.text(statement))

    assert_rejected(
        "INSERT INTO credit_refund_grants "
        "(id, billing_operation_id, tenant_id, user_id, source_subscription_id, "
        "amount_credits, status) VALUES "
        "('bad-refund', 'operation', 'tenant', 'user', 'source-sub', 1.5, 'pending')"
    )
    for statement in (
        "DELETE FROM assets WHERE id = 'asset'",
        "DELETE FROM brand_voices WHERE id = 'voice'",
        "DELETE FROM brand_voice_orders WHERE id = 'order'",
    ):
        assert_rejected(statement)
    with engine.begin() as connection:
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        assert (
            connection.scalar(
                sa.text(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_name = 'brand_voice_orders' AND column_name = 'source_metadata_snapshot'"
                )
            )
            == "jsonb"
        )
        index_definition = connection.scalar(
            sa.text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'brand_voice_orders' "
                "AND indexname = 'uq_brand_voice_orders_awaiting_renewal_per_voice'"
            )
        )
        assert index_definition is not None
        assert "order_type" in index_definition
        assert "awaiting_fulfillment" in index_definition
