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
    / "20260804_0032_credit_rate_resolution_reprice.py"
)
_OLD_RATES = {
    ("image", "image"): Decimal("10.0000"),
    ("video", "second"): Decimal("80.0000"),
    ("video_gen", "second"): Decimal("80.0000"),
}
_NEW_RATES = {
    ("image", "image"): Decimal("80.0000"),
    ("video", "second"): Decimal("100.0000"),
    ("video_gen", "second"): Decimal("100.0000"),
}


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "credit_rate_resolution_reprice_migration",
        _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_credit_rate_resolution_reprice_revision_follows_0031() -> None:
    migration = _load_migration()
    source = _MIGRATION_PATH.read_text(encoding="utf-8")

    assert migration.revision == "20260804_0032"
    assert migration.down_revision == "20260724_0031"
    assert "INTO STRICT" in source
    assert "GET DIAGNOSTICS affected = ROW_COUNT" in source
    assert "tenant_id IS NOT NULL" in source


@pytest.fixture
def postgres_rate_schema():
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the real PostgreSQL migration tests.")

    engine = sa.create_engine(database_url, pool_pre_ping=True)
    schema = f"credit_rate_resolution_reprice_{uuid4().hex}"
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


def _seed_rates(connection, rates: dict[tuple[str, str], Decimal] | None = None) -> None:
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


def _read_rates(engine, schema: str) -> dict[tuple[str, str], Decimal]:
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


def test_credit_rate_resolution_reprice_round_trips_on_postgres(
    postgres_rate_schema,
) -> None:
    engine, schema = postgres_rate_schema
    with _schema_transaction(engine, schema) as connection:
        _seed_rates(connection)

    _run_migration(engine, schema, "upgrade")
    assert _read_rates(engine, schema) == _NEW_RATES

    _run_migration(engine, schema, "downgrade")
    assert _read_rates(engine, schema) == _OLD_RATES


def test_upgrade_rejects_duplicate_active_platform_rate_and_rolls_back(
    postgres_rate_schema,
) -> None:
    engine, schema = postgres_rate_schema
    with _schema_transaction(engine, schema) as connection:
        _seed_rates(connection)
        connection.execute(
            sa.text(
                """
                INSERT INTO credit_rates (
                    id, tenant_id, capability, unit, credits_per_unit, is_active
                ) VALUES (
                    'duplicate-platform-image', NULL, 'image', 'image', 10.0000, TRUE
                )
                """
            )
        )

    with pytest.raises(sa.exc.DBAPIError):
        _run_migration(engine, schema, "upgrade")

    assert _read_rates(engine, schema) == _OLD_RATES
    with _schema_transaction(engine, schema) as connection:
        duplicate_values = list(
            connection.scalars(
                sa.text(
                    """
                    SELECT credits_per_unit
                    FROM credit_rates
                    WHERE tenant_id IS NULL
                      AND capability = 'image'
                      AND unit = 'image'
                      AND is_active IS TRUE
                    ORDER BY id
                    """
                )
            )
        )
    assert duplicate_values == [Decimal("10.0000"), Decimal("10.0000")]


def test_upgrade_rejects_unexpected_old_rate_and_rolls_back_prior_updates(
    postgres_rate_schema,
) -> None:
    engine, schema = postgres_rate_schema
    drifted_rates = {**_OLD_RATES, ("image", "image"): Decimal("11.0000")}
    with _schema_transaction(engine, schema) as connection:
        _seed_rates(connection, drifted_rates)

    with pytest.raises(sa.exc.DBAPIError):
        _run_migration(engine, schema, "upgrade")

    assert _read_rates(engine, schema) == drifted_rates


def test_upgrade_rejects_any_tenant_rate_before_changing_platform_rates(
    postgres_rate_schema,
) -> None:
    engine, schema = postgres_rate_schema
    with _schema_transaction(engine, schema) as connection:
        _seed_rates(connection)
        connection.execute(
            sa.text(
                """
                INSERT INTO credit_rates (
                    id, tenant_id, capability, unit, credits_per_unit, is_active
                ) VALUES (
                    'tenant-image-rate', 'tenant-one', 'image', 'image', 5.0000, TRUE
                )
                """
            )
        )

    with pytest.raises(sa.exc.DBAPIError):
        _run_migration(engine, schema, "upgrade")

    assert _read_rates(engine, schema) == _OLD_RATES


def test_downgrade_rejects_manual_drift_and_preserves_current_rates(
    postgres_rate_schema,
) -> None:
    engine, schema = postgres_rate_schema
    with _schema_transaction(engine, schema) as connection:
        _seed_rates(connection)
    _run_migration(engine, schema, "upgrade")

    with _schema_transaction(engine, schema) as connection:
        connection.execute(
            sa.text(
                """
                UPDATE credit_rates
                SET credits_per_unit = 81.0000
                WHERE capability = 'image' AND unit = 'image'
                """
            )
        )
    drifted_rates = {**_NEW_RATES, ("image", "image"): Decimal("81.0000")}

    with pytest.raises(sa.exc.DBAPIError):
        _run_migration(engine, schema, "downgrade")

    assert _read_rates(engine, schema) == drifted_rates
