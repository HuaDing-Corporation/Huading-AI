from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from test_manual_brand_voice_migration import _create_0037_schema

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load(filename: str):
    path = VERSIONS / filename
    assert path.exists(), f"{filename} is required for the pricing closure chain"
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pricing_closure_migration_chain_ends_at_0038():
    billing = _load("20260829_0036_billing_core.py")
    rates = _load("20260829_0037_pricing_rates.py")
    manual_voice = _load("20260829_0038_manual_brand_voice.py")
    assert (billing.revision, rates.down_revision, rates.revision, manual_voice.down_revision) == (
        "20260829_0036",
        "20260829_0036",
        "20260829_0037",
        "20260829_0037",
    )


def test_0038_downgrade_refuses_to_erase_order_history():
    migration = _load("20260829_0038_manual_brand_voice.py")
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            _create_0037_schema(connection)
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            connection.execute(
                sa.text("""INSERT INTO brand_voice_orders
                (id, tenant_id, user_id, order_type, requested_name, source_audio_asset_id,
                source_metadata_snapshot, consent_confirmed_at, billing_operation_id, status, created_at, updated_at)
                VALUES ('history', 'tenant', 'user', 'create', 'Voice', 'asset', '{}', '2025-01-01',
                'operation', 'awaiting_fulfillment', '2025-01-01', '2025-01-01')""")
            )
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            with pytest.raises(RuntimeError, match="Cannot downgrade 20260829_0038"):
                migration.downgrade()
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM brand_voice_orders")) == 1
    finally:
        engine.dispose()
