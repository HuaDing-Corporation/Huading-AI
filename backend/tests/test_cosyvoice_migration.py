from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa


def _cosyvoice_migration_module():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260706_0021_cosyvoice_voice_clone_provider.py"
    )
    spec = importlib.util.spec_from_file_location("cosyvoice_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_cosyvoice_migration_seeds_provider_and_relaxes_unique_indexes():
    migration = _cosyvoice_migration_module()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    metadata = sa.MetaData()
    provider_configs = sa.Table(
        "provider_configs",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=True),
        sa.Column("capability", sa.String, nullable=False),
        sa.Column("provider", sa.String, nullable=False),
        sa.Column("config", sa.JSON, nullable=False, default=dict),
        sa.Column("is_active", sa.Boolean, nullable=False, default=True),
    )
    credit_rates = sa.Table(
        "credit_rates",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=True),
        sa.Column("capability", sa.String, nullable=False),
        sa.Column("unit", sa.String, nullable=False),
        sa.Column("credits_per_unit", sa.Numeric(12, 4), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, default=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Index(
        "uq_provider_configs_tenant_capability",
        provider_configs.c.tenant_id,
        provider_configs.c.capability,
        unique=True,
        sqlite_where=provider_configs.c.tenant_id.is_not(None),
    )
    sa.Index(
        "uq_provider_configs_platform_capability",
        provider_configs.c.capability,
        unique=True,
        sqlite_where=provider_configs.c.tenant_id.is_(None),
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            provider_configs.insert(),
            {
                "id": "00000000-0000-0000-0000-000000000407",
                "tenant_id": None,
                "capability": "voice_clone",
                "provider": "doubao-voice-clone",
                "config": {},
                "is_active": True,
            },
        )
        conn.execute(
            credit_rates.insert(),
            [
                {
                    "id": "platform-voice-clone-rate",
                    "tenant_id": None,
                    "capability": "voice_clone",
                    "unit": "call",
                    "credits_per_unit": Decimal("30.0000"),
                    "is_active": True,
                    "effective_at": datetime.now(UTC),
                },
                {
                    "id": "tenant-voice-clone-rate",
                    "tenant_id": "tenant-custom",
                    "capability": "voice_clone",
                    "unit": "call",
                    "credits_per_unit": Decimal("42.0000"),
                    "is_active": True,
                    "effective_at": datetime.now(UTC),
                },
            ],
        )

        class _Op:
            def get_bind(self):
                return conn

            def drop_index(self, index_name: str, *, table_name: str):
                conn.execute(sa.text(f"DROP INDEX {index_name}"))

            def create_index(
                self,
                index_name: str,
                table_name: str,
                columns: list[str],
                *,
                unique: bool = False,
                postgresql_where=None,
                sqlite_where=None,
            ):
                index = sa.Index(
                    index_name,
                    *[provider_configs.c[column] for column in columns],
                    unique=unique,
                    sqlite_where=sqlite_where,
                )
                index.create(conn)

            def bulk_insert(self, table, rows):
                assert table.name == "provider_configs"
                conn.execute(provider_configs.insert(), rows)

            def execute(self, statement):
                return conn.execute(statement)

        migration.op = _Op()
        migration.upgrade()

        rows = conn.execute(
            sa.select(
                provider_configs.c.capability,
                provider_configs.c.provider,
                provider_configs.c.is_active,
            ).order_by(provider_configs.c.provider)
        ).all()
        index_names = {
            index["name"] for index in sa.inspect(conn).get_indexes("provider_configs")
        }
        platform_rate = conn.scalar(
            sa.select(credit_rates.c.credits_per_unit).where(
                credit_rates.c.tenant_id.is_(None),
                credit_rates.c.capability == "voice_clone",
                credit_rates.c.unit == "call",
            )
        )
        tenant_rate = conn.scalar(
            sa.select(credit_rates.c.credits_per_unit).where(
                credit_rates.c.tenant_id == "tenant-custom",
                credit_rates.c.capability == "voice_clone",
                credit_rates.c.unit == "call",
            )
        )

    assert rows == [
        ("voice_clone", "cosyvoice-voice-clone", True),
        ("voice_clone", "doubao-voice-clone", True),
    ]
    assert "uq_provider_configs_platform_capability" not in index_names
    assert "uq_provider_configs_platform_capability_provider" in index_names
    assert "uq_provider_configs_platform_capability_non_voice_clone" in index_names
    assert Decimal(str(platform_rate)) == Decimal("30000.0000")
    assert Decimal(str(tenant_rate)) == Decimal("42.0000")
