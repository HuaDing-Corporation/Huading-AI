from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260803_0032_reasoning_wallet_overdraft.py"
    )
    spec = importlib.util.spec_from_file_location(
        "reasoning_wallet_overdraft_migration",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_upgrade_allows_reasoning_wallet_available_balance_to_be_negative() -> None:
    migration = _load_migration()
    assert migration.revision == "20260803_0032"
    assert migration.down_revision == "20260724_0031"

    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE reasoning_wallets ("
                "tenant_id VARCHAR(36) PRIMARY KEY, "
                "available_credits NUMERIC(18, 6) NOT NULL, "
                "CONSTRAINT ck_reasoning_wallets_available_nonnegative "
                "CHECK (available_credits >= 0))"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        connection.execute(
            sa.text(
                "INSERT INTO reasoning_wallets (tenant_id, available_credits) "
                "VALUES ('tenant-in-debt', -1.250000)"
            )
        )
        assert connection.scalar(
            sa.text(
                "SELECT available_credits FROM reasoning_wallets "
                "WHERE tenant_id = 'tenant-in-debt'"
            )
        ) == Decimal("-1.250000")


def test_downgrade_refuses_to_restore_nonnegative_constraint_while_debt_exists() -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE reasoning_wallets ("
                "tenant_id VARCHAR(36) PRIMARY KEY, "
                "available_credits NUMERIC(18, 6) NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO reasoning_wallets (tenant_id, available_credits) "
                "VALUES ('tenant-in-debt', -1.250000)"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))

        with pytest.raises(
            RuntimeError,
            match=(
                r"Cannot downgrade 20260803_0032: 1 reasoning wallet has a "
                r"negative available_credits balance.*Balances were not modified"
            ),
        ):
            migration.downgrade()

        assert connection.scalar(
            sa.text(
                "SELECT available_credits FROM reasoning_wallets "
                "WHERE tenant_id = 'tenant-in-debt'"
            )
        ) == Decimal("-1.250000")
        assert all(
            constraint["name"] != "ck_reasoning_wallets_available_nonnegative"
            for constraint in sa.inspect(connection).get_check_constraints(
                "reasoning_wallets"
            )
        )


def test_downgrade_restores_nonnegative_constraint_when_no_debt_exists() -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE reasoning_wallets ("
                "tenant_id VARCHAR(36) PRIMARY KEY, "
                "available_credits NUMERIC(18, 6) NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO reasoning_wallets (tenant_id, available_credits) "
                "VALUES ('settled', 0), ('funded', 12.500000)"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))

        migration.downgrade()

        constraints = {
            constraint["name"]: str(constraint["sqltext"])
            for constraint in sa.inspect(connection).get_check_constraints(
                "reasoning_wallets"
            )
        }
        assert constraints["ck_reasoning_wallets_available_nonnegative"] == (
            "available_credits >= 0"
        )
        assert list(
            connection.execute(
                sa.text(
                    "SELECT tenant_id, available_credits "
                    "FROM reasoning_wallets ORDER BY tenant_id"
                )
            )
        ) == [
            ("funded", Decimal("12.500000")),
            ("settled", Decimal("0.000000")),
        ]
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO reasoning_wallets "
                    "(tenant_id, available_credits) "
                    "VALUES ('new-debt', -0.000001)"
                )
            )
