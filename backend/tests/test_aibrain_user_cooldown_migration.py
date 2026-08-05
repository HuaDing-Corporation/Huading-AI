from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.db import models


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260805_0034_aibrain_user_cooldowns.py"
    )
    spec = importlib.util.spec_from_file_location(
        "aibrain_user_cooldowns_migration",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _create_parent_tables(connection: sa.Connection) -> None:
    connection.execute(sa.text("PRAGMA foreign_keys = ON"))
    connection.execute(
        sa.text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)")
    )
    connection.execute(
        sa.text(
            "CREATE TABLE users ("
            "id VARCHAR(36) PRIMARY KEY, "
            "tenant_id VARCHAR(36) NOT NULL, "
            "FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE)"
        )
    )
    connection.execute(
        sa.text(
            "CREATE TABLE chat_messages ("
            "id VARCHAR(36) PRIMARY KEY, "
            "tenant_id VARCHAR(36) NOT NULL, "
            "FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE)"
        )
    )


def _upgrade_in_memory():
    migration = _load_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    connection = engine.connect()
    transaction = connection.begin()
    _create_parent_tables(connection)
    migration.op = Operations(MigrationContext.configure(connection))
    migration.upgrade()
    return migration, engine, connection, transaction


def test_aibrain_user_cooldown_model_has_one_row_per_real_user() -> None:
    table = models.AIBrainUserCooldown.__table__

    assert {column.name for column in table.columns} == {
        "id",
        "tenant_id",
        "user_id",
        "reason",
        "source_message_id",
        "expires_at",
        "created_at",
        "updated_at",
    }
    assert {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    } == {"uq_aibrain_user_cooldowns_user_id"}
    assert {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    } == {
        "ix_aibrain_user_cooldowns_tenant_expires": ("tenant_id", "expires_at"),
        "ix_aibrain_user_cooldowns_tenant_id": ("tenant_id",),
    }
    foreign_keys = {
        column.name: (
            foreign_key.target_fullname,
            foreign_key.ondelete,
        )
        for column in table.columns
        for foreign_key in column.foreign_keys
    }
    assert foreign_keys == {
        "tenant_id": ("tenants.id", "CASCADE"),
        "user_id": ("users.id", "CASCADE"),
        "source_message_id": ("chat_messages.id", "SET NULL"),
    }


def test_aibrain_user_cooldown_revision_precedes_pricing_a1_head() -> None:
    migration = _load_migration()
    assert migration.revision == "20260805_0034"
    assert migration.down_revision == "20260805_0033"

    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))

    script = ScriptDirectory.from_config(config)
    assert script.get_revision("20260805_0034").nextrev == {"20260806_0035"}
    assert script.get_heads() == ["20260806_0035"]


def test_upgrade_creates_cooldown_contract_and_enforces_one_row_per_user() -> None:
    migration, engine, connection, transaction = _upgrade_in_memory()
    try:
        inspector = sa.inspect(connection)
        assert {column["name"] for column in inspector.get_columns(
            "aibrain_user_cooldowns"
        )} == {
            "id",
            "tenant_id",
            "user_id",
            "reason",
            "source_message_id",
            "expires_at",
            "created_at",
            "updated_at",
        }
        assert inspector.get_unique_constraints(
            "aibrain_user_cooldowns"
        ) == [
            {
                "name": "uq_aibrain_user_cooldowns_user_id",
                "column_names": ["user_id"],
            }
        ]
        assert {
            index["name"]: tuple(index["column_names"])
            for index in inspector.get_indexes("aibrain_user_cooldowns")
        } == {
            "ix_aibrain_user_cooldowns_tenant_expires": (
                "tenant_id",
                "expires_at",
            ),
            "ix_aibrain_user_cooldowns_tenant_id": ("tenant_id",),
        }
        assert {
            tuple(foreign_key["constrained_columns"]): (
                tuple(foreign_key["referred_columns"]),
                foreign_key["referred_table"],
                foreign_key["options"].get("ondelete"),
            )
            for foreign_key in inspector.get_foreign_keys(
                "aibrain_user_cooldowns"
            )
        } == {
            ("tenant_id",): (("id",), "tenants", "CASCADE"),
            ("user_id",): (("id",), "users", "CASCADE"),
            ("source_message_id",): (("id",), "chat_messages", "SET NULL"),
        }

        connection.execute(
            sa.text("INSERT INTO tenants (id) VALUES ('tenant-1')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO users (id, tenant_id) "
                "VALUES ('user-1', 'tenant-1')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO chat_messages (id, tenant_id) "
                "VALUES ('message-1', 'tenant-1')"
            )
        )
        insert_cooldown = sa.text(
            "INSERT INTO aibrain_user_cooldowns "
            "(id, tenant_id, user_id, reason, source_message_id, expires_at) "
            "VALUES (:id, 'tenant-1', 'user-1', 'provider_timeout', "
            "'message-1', '2026-08-05 23:00:00')"
        )
        connection.execute(insert_cooldown, {"id": "cooldown-1"})
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(insert_cooldown, {"id": "cooldown-2"})
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_source_message_delete_sets_null_and_user_delete_cascades() -> None:
    _, engine, connection, transaction = _upgrade_in_memory()
    try:
        connection.execute(
            sa.text("INSERT INTO tenants (id) VALUES ('tenant-1')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO users (id, tenant_id) "
                "VALUES ('user-1', 'tenant-1')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO chat_messages (id, tenant_id) "
                "VALUES ('message-1', 'tenant-1')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO aibrain_user_cooldowns "
                "(id, tenant_id, user_id, reason, source_message_id, expires_at) "
                "VALUES ('cooldown-1', 'tenant-1', 'user-1', "
                "'provider_timeout', 'message-1', '2026-08-05 23:00:00')"
            )
        )

        connection.execute(
            sa.text("DELETE FROM chat_messages WHERE id = 'message-1'")
        )
        assert connection.scalar(
            sa.text(
                "SELECT source_message_id FROM aibrain_user_cooldowns "
                "WHERE id = 'cooldown-1'"
            )
        ) is None

        connection.execute(sa.text("DELETE FROM users WHERE id = 'user-1'"))
        assert connection.scalar(
            sa.text("SELECT COUNT(*) FROM aibrain_user_cooldowns")
        ) == 0
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_downgrade_removes_cooldown_table() -> None:
    migration, engine, connection, transaction = _upgrade_in_memory()
    try:
        migration.downgrade()
        assert "aibrain_user_cooldowns" not in sa.inspect(
            connection
        ).get_table_names()
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
