from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260719_0028_scene_prompt_provider.py"
    )
    spec = importlib.util.spec_from_file_location("scene_prompt_migration_runtime", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_scene_prompt_migration_downgrade_cleans_runtime_rows_before_legacy_checks() -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE provider_configs ("
                "id VARCHAR(36) PRIMARY KEY, "
                "tenant_id VARCHAR(36), "
                "capability VARCHAR(32) NOT NULL, "
                "provider VARCHAR(64) NOT NULL, "
                "config JSON NOT NULL, "
                "is_active BOOLEAN NOT NULL, "
                "CONSTRAINT ck_provider_configs_capability CHECK ("
                "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
                "'publish', 'voice_clone', 'reverse_prompt')))"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE usage_records ("
                "id VARCHAR(36) PRIMARY KEY, "
                "capability VARCHAR(32) NOT NULL, "
                "CONSTRAINT ck_usage_records_capability CHECK ("
                "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
                "'publish', 'voice_clone', 'video_gen', 'reverse_prompt', "
                "'reverse_prompt_video')))"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO provider_configs "
                "(id, tenant_id, capability, provider, config, is_active) "
                "VALUES ('legacy-provider', NULL, 'llm', 'deepseek', '{}', 1)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO usage_records (id, capability) "
                "VALUES ('legacy-usage', 'llm')"
            )
        )

        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO provider_configs "
                "(id, tenant_id, capability, provider, config, is_active) "
                "VALUES ('tenant-scene-provider', 'tenant-1', 'scene_prompt', "
                "'apimart-luna', '{}', 1)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO usage_records (id, capability) "
                "VALUES ('runtime-scene-usage', 'scene_prompt')"
            )
        )

        migration.downgrade()

        assert connection.scalar(
            sa.text("SELECT count(*) FROM provider_configs WHERE capability = 'scene_prompt'")
        ) == 0
        assert connection.scalar(
            sa.text("SELECT count(*) FROM usage_records WHERE capability = 'scene_prompt'")
        ) == 0
        assert connection.scalar(sa.text("SELECT count(*) FROM provider_configs")) == 1
        assert connection.scalar(sa.text("SELECT count(*) FROM usage_records")) == 1
        assert all(
            "scene_prompt" not in str(constraint.get("sqltext") or "")
            for table_name in ("provider_configs", "usage_records")
            for constraint in sa.inspect(connection).get_check_constraints(table_name)
        )
