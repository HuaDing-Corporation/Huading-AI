from __future__ import annotations

import importlib.util
import os
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260806_0035_pricing_a1_rates.py"
)
_OLD_RATES = {
    ("reverse_prompt", "call"): Decimal("30.0000"),
    ("avatar", "second"): Decimal("150.0000"),
}
_NEW_RATES = {
    ("reverse_prompt", "call"): Decimal("100.0000"),
    ("avatar", "second"): Decimal("180.0000"),
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("pricing_a1_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@pytest.fixture
def postgres_rate_schema():
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the real PostgreSQL migration tests.")

    engine = sa.create_engine(database_url, pool_pre_ping=True)
    schema = f"pricing_a1_migration_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    try:
        with _schema_transaction(engine, schema) as connection:
            connection.execute(
                sa.text(
                    """
                    CREATE TABLE credit_rates (
                        id VARCHAR(36) PRIMARY KEY,
                        tenant_id VARCHAR(36),
                        capability VARCHAR(32) NOT NULL,
                        unit VARCHAR(32) NOT NULL,
                        credits_per_unit NUMERIC(12, 4) NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                    """
                )
            )
        yield engine, schema
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@contextmanager
def _schema_transaction(engine, schema: str):
    with engine.begin() as connection:
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        yield connection


def _seed_platform_rates(
    connection,
    rates: dict[tuple[str, str], Decimal] | None = None,
) -> None:
    selected_rates = rates or _OLD_RATES
    connection.execute(
        sa.text(
            """
            INSERT INTO credit_rates (
                id, tenant_id, capability, unit, credits_per_unit, is_active
            ) VALUES (
                :id, NULL, :capability, :unit, :credits_per_unit, TRUE
            )
            """
        ),
        [
            {
                "id": f"platform-{capability}-{unit}",
                "capability": capability,
                "unit": unit,
                "credits_per_unit": rate,
            }
            for (capability, unit), rate in selected_rates.items()
        ],
    )


def _run_migration(engine, schema: str, direction: str) -> None:
    migration = _load_migration()
    with _schema_transaction(engine, schema) as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        getattr(migration, direction)()


def _read_platform_rates(engine, schema: str) -> dict[tuple[str, str], Decimal]:
    with _schema_transaction(engine, schema) as connection:
        rows = connection.execute(
            sa.text(
                """
                SELECT capability, unit, credits_per_unit
                FROM credit_rates
                WHERE tenant_id IS NULL AND is_active IS TRUE
                """
            )
        ).mappings()
        return {
            (row["capability"], row["unit"]): Decimal(row["credits_per_unit"])
            for row in rows
        }


def test_upgrade_sets_a1_platform_rates(postgres_rate_schema) -> None:
    migration = _load_migration()
    source = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert migration.revision == "20260806_0035"
    assert migration.down_revision == "20260805_0034"
    assert "INTO STRICT" in source
    assert "FOR UPDATE" in source
    assert "GET DIAGNOSTICS affected = ROW_COUNT" in source
    assert "from app" not in source
    assert "import app" not in source

    engine, schema = postgres_rate_schema
    with _schema_transaction(engine, schema) as connection:
        _seed_platform_rates(connection)
        connection.execute(
            sa.text(
                """
                INSERT INTO credit_rates (
                    id, tenant_id, capability, unit, credits_per_unit, is_active
                ) VALUES (
                    'tenant-unrelated-rate', 'tenant-one', 'image', 'image', 12.0000, TRUE
                )
                """
            )
        )

    _run_migration(engine, schema, "upgrade")

    assert _read_platform_rates(engine, schema) == _NEW_RATES


def test_downgrade_restores_a1_platform_rates(postgres_rate_schema) -> None:
    engine, schema = postgres_rate_schema
    with _schema_transaction(engine, schema) as connection:
        _seed_platform_rates(connection)
    _run_migration(engine, schema, "upgrade")

    _run_migration(engine, schema, "downgrade")
    _run_migration(engine, schema, "downgrade")

    assert _read_platform_rates(engine, schema) == _OLD_RATES


def test_upgrade_accepts_rates_already_at_a1_targets(postgres_rate_schema) -> None:
    engine, schema = postgres_rate_schema
    with _schema_transaction(engine, schema) as connection:
        _seed_platform_rates(connection, _NEW_RATES)

    _run_migration(engine, schema, "upgrade")

    assert _read_platform_rates(engine, schema) == _NEW_RATES


def test_upgrade_rejects_third_value_and_rolls_back_both_rates(
    postgres_rate_schema,
) -> None:
    engine, schema = postgres_rate_schema
    drifted_rates = {**_OLD_RATES, ("avatar", "second"): Decimal("120.0000")}
    with _schema_transaction(engine, schema) as connection:
        _seed_platform_rates(connection, drifted_rates)

    with pytest.raises(sa.exc.DBAPIError):
        _run_migration(engine, schema, "upgrade")

    assert _read_platform_rates(engine, schema) == drifted_rates


def test_upgrade_rejects_active_tenant_override_for_target_rate(
    postgres_rate_schema,
) -> None:
    engine, schema = postgres_rate_schema
    with _schema_transaction(engine, schema) as connection:
        _seed_platform_rates(connection)
        connection.execute(
            sa.text(
                """
                INSERT INTO credit_rates (
                    id, tenant_id, capability, unit, credits_per_unit, is_active
                ) VALUES (
                    'tenant-avatar-rate', 'tenant-one', 'avatar', 'second', 90.0000, TRUE
                )
                """
            )
        )

    with pytest.raises(sa.exc.DBAPIError):
        _run_migration(engine, schema, "upgrade")

    assert _read_platform_rates(engine, schema) == _OLD_RATES
