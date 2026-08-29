# ruff: noqa: E501
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load(filename: str):
    path = VERSIONS / filename
    assert path.exists(), f"{filename} is required for the pricing closure chain"
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_0035_schema(connection) -> None:
    for statement in (
        "CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)",
        "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)",
        "CREATE TABLE assets (id VARCHAR(36) PRIMARY KEY)",
        "CREATE TABLE subscriptions (id VARCHAR(36) PRIMARY KEY)",
        """CREATE TABLE credit_rates (
            id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), capability VARCHAR(32) NOT NULL,
            unit VARCHAR(32) NOT NULL, credits_per_unit NUMERIC(12, 4) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE, effective_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_credit_rates_capability CHECK (capability IN
            ('llm', 'tts', 'avatar', 'video', 'image', 'asr', 'publish', 'voice_clone',
            'video_gen', 'reverse_prompt', 'reverse_prompt_video')),
            CONSTRAINT ck_credit_rates_unit CHECK (unit IN ('second', 'call', 'token', 'image', 'character')))""",
        """CREATE TABLE usage_records (
            id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), capability VARCHAR(32) NOT NULL,
            provider VARCHAR(40), unit VARCHAR(32) NOT NULL, quantity NUMERIC(12, 3) NOT NULL,
            credits NUMERIC(18, 6) NOT NULL, cost_cents INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT ck_usage_records_capability CHECK (capability IN
            ('llm', 'tts', 'avatar', 'video', 'image', 'asr', 'publish', 'voice_clone',
            'video_gen', 'reverse_prompt', 'reverse_prompt_video', 'scene_prompt', 'chat')),
            CONSTRAINT ck_usage_records_unit CHECK (unit IN ('second', 'call', 'token', 'image', 'char')))""",
        """CREATE TABLE brand_voices (
            id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), name VARCHAR(30) NOT NULL,
            source_audio_asset_id VARCHAR(36), provider VARCHAR(40) NOT NULL,
            speaker_id VARCHAR(160), status VARCHAR(32) NOT NULL,
            consent_confirmed BOOLEAN NOT NULL, consent_confirmed_at DATETIME,
            error_code VARCHAR(40), error_message TEXT, created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL, deleted_at DATETIME)""",
        """CREATE TABLE admin_audit_logs (
            id VARCHAR(36) PRIMARY KEY, actor_user_id VARCHAR(36) NOT NULL,
            actor_tenant_id VARCHAR(36) NOT NULL, action VARCHAR(32) NOT NULL,
            target_tenant_id VARCHAR(36), target_id VARCHAR(36), before JSON, after JSON,
            reason TEXT, created_at DATETIME NOT NULL,
            CONSTRAINT ck_admin_audit_logs_action CHECK (action IN
            ('credits_adjust', 'plan_change', 'status_change', 'voice_slot_assign', 'task_retry')))""",
    ):
        connection.execute(sa.text(statement))


def _upgrade_pricing_closure(connection) -> None:
    connection.execute(
        sa.text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))")
    )
    current = connection.scalar(sa.text("SELECT version_num FROM alembic_version LIMIT 1"))
    migrations = (
        _load("20260829_0036_billing_core.py"),
        _load("20260829_0037_pricing_rates.py"),
        _load("20260829_0038_manual_brand_voice.py"),
    )
    apply_next = current is None
    for migration in migrations:
        if apply_next:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            connection.execute(sa.text("DELETE FROM alembic_version"))
            connection.execute(
                sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": migration.revision},
            )
        if migration.revision == current:
            apply_next = True


def test_pricing_closure_chain_executes_and_repeat_upgrade_is_a_noop():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            _create_0035_schema(connection)
            _upgrade_pricing_closure(connection)
            tables = set(sa.inspect(connection).get_table_names())
            assert {
                "billing_operations",
                "brand_voice_orders",
                "brand_voice_provider_ids",
                "credit_refund_grants",
            } <= tables
            assert (
                connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
                == "20260829_0038"
            )
            before = {
                table: tuple(column["name"] for column in sa.inspect(connection).get_columns(table))
                for table in ("brand_voices", "brand_voice_orders", "credit_refund_grants")
            }
            _upgrade_pricing_closure(connection)
            after = {
                table: tuple(column["name"] for column in sa.inspect(connection).get_columns(table))
                for table in before
            }
            assert after == before
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "history_sql",
    (
        """INSERT INTO brand_voice_orders
        (id, tenant_id, user_id, order_type, requested_name, source_audio_asset_id,
        source_metadata_snapshot, consent_confirmed_at, billing_operation_id, status, created_at, updated_at)
        VALUES ('order-history', 'tenant', 'user', 'create', 'Voice', 'asset', '{}', '2025-01-01',
        'operation', 'awaiting_fulfillment', '2025-01-01', '2025-01-01')""",
        """INSERT INTO brand_voice_provider_ids
        (id, provider, normalized_provider_id, kind, status, created_at, updated_at)
        VALUES ('provider-history', 'p', 'provider-history', 'official', 'active', '2025-01-01', '2025-01-01')""",
        """INSERT INTO credit_refund_grants
        (id, billing_operation_id, tenant_id, user_id, source_subscription_id, amount_credits, status, created_at)
        VALUES ('refund-history', 'operation', 'tenant', 'user', 'source-sub', 1, 'pending', '2025-01-01')""",
        """INSERT INTO admin_audit_logs
        (id, actor_user_id, actor_tenant_id, action, created_at)
        VALUES ('audit-history', 'user', 'tenant', 'brand_voice_order_fulfill', '2025-01-01')""",
    ),
)
def test_0038_downgrade_refuses_all_manual_delivery_history(history_sql):
    migration = _load("20260829_0038_manual_brand_voice.py")
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            _create_0035_schema(connection)
            _upgrade_pricing_closure(connection)
            connection.execute(sa.text(history_sql))
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            with pytest.raises(RuntimeError, match="Cannot downgrade 20260829_0038"):
                migration.downgrade()
    finally:
        engine.dispose()
