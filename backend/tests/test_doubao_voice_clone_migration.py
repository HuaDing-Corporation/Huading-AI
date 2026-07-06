from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


def _migration_module():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260706_0020_doubao_voice_clone_slot_pool.py"
    )
    spec = importlib.util.spec_from_file_location("doubao_slot_pool_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_doubao_slot_pool_migration_preserves_env_fallback_config():
    migration = _migration_module()
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
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            provider_configs.insert(),
            {
                "id": "doubao-platform-config",
                "tenant_id": None,
                "capability": "voice_clone",
                "provider": "doubao-voice-clone",
                "config": {},
            },
        )

        class _Op:
            def get_bind(self):
                return conn

        migration.op = _Op()
        migration.upgrade()

        config = conn.scalar(
            sa.select(provider_configs.c.config).where(
                provider_configs.c.id == "doubao-platform-config"
            )
        )

    assert config == {}
