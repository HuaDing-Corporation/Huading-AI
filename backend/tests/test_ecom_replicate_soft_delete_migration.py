import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect


def test_ecom_replicate_soft_delete_migration_round_trips() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260724_0031_ecom_replicate_job_soft_delete.py"
    )
    spec = importlib.util.spec_from_file_location(
        "ecom_replicate_job_soft_delete_migration",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260724_0031"
    assert migration.down_revision == "20260721_0030"

    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    Table("ecom_replicate_jobs", metadata, Column("id", String(36), primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()
        assert {
            column["name"]
            for column in inspect(connection).get_columns("ecom_replicate_jobs")
        } == {"id", "deleted_at"}

        migration.downgrade()
        assert {
            column["name"]
            for column in inspect(connection).get_columns("ecom_replicate_jobs")
        } == {"id"}

        migration.upgrade()
        assert {
            column["name"]
            for column in inspect(connection).get_columns("ecom_replicate_jobs")
        } == {"id", "deleted_at"}
