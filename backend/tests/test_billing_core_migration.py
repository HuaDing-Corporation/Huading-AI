from __future__ import annotations

import importlib.util
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, BillingOperation, Tenant, UsageRecord, User


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260829_0036_billing_core.py"
    )
    spec = importlib.util.spec_from_file_location("billing_core_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@contextmanager
def _schema_transaction(engine, schema: str):
    with engine.begin() as connection:
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        yield connection


@pytest.fixture
def postgres_billing_domain_schema():
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the real PostgreSQL billing test.")
    url = make_url(database_url)
    if url.host not in {"localhost", "127.0.0.1"} or not str(url.database).startswith(
        "huading_pricing_test_"
    ):
        pytest.fail("TEST_POSTGRES_URL must target an isolated local huading_pricing_test_* DB")

    engine = sa.create_engine(url, pool_pre_ping=True)
    schema = f"billing_amount_domain_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    try:
        with _schema_transaction(engine, schema) as connection:
            connection.execute(sa.text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)"))
            connection.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
            connection.execute(
                sa.text(
                    "CREATE TABLE usage_records ("
                    "id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL)"
                )
            )
            connection.execute(sa.text("INSERT INTO tenants (id) VALUES ('tenant-a')"))
            connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))
            migration = _load_migration()
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
        yield engine, schema
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionTesting()
    session.add_all(
        [
            Tenant(id="tenant-a", slug="tenant-a", name="Tenant A"),
            User(
                id="user-a",
                tenant_id="tenant-a",
                email="user-a@example.com",
                password_hash="hash",
                role="creator",
            ),
        ]
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def make_billing_operation(**overrides: object) -> BillingOperation:
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "operation": "scene_prompt",
        "idempotency_key": "11111111-1111-4111-8111-111111111111",
        "request_hash": "a" * 64,
        "quote_hash": "b" * 64,
        "pricing_snapshot": {"pricing_lines": [{"subtotal_credits": "30.0000"}]},
        "requested_credits": Decimal("30.0000"),
        "settled_credits": Decimal("0"),
        "released_credits": Decimal("0"),
        "status": "in_progress",
        "completion_kind": None,
        "completed_at": None,
    }
    values.update(overrides)
    return BillingOperation(**values)


def make_usage_record(**overrides: object) -> UsageRecord:
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "capability": "scene_prompt",
        "provider": "test-provider",
        "unit": "call",
        "quantity": Decimal("1"),
        "credits": Decimal("30.0000"),
        "cost_cents": 0,
        "provider_usage": None,
    }
    values.update(overrides)
    return UsageRecord(**values)


def test_billing_operation_rejects_completed_without_conservation(db_session):
    operation = BillingOperation(
        tenant_id="tenant-a",
        user_id="user-a",
        operation="scene_prompt",
        idempotency_key="11111111-1111-4111-8111-111111111111",
        request_hash="a" * 64,
        quote_hash="b" * 64,
        pricing_snapshot={"pricing_lines": [{"subtotal_credits": "30.0000"}]},
        requested_credits=30,
        settled_credits=29,
        released_credits=0,
        status="completed",
        completion_kind="succeeded",
        completed_at=datetime.now(UTC),
    )
    db_session.add(operation)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_billing_operation_rejects_completed_without_completion_kind(db_session):
    db_session.add(
        make_billing_operation(
            status="completed",
            completion_kind=None,
            completed_at=datetime.now(UTC),
            settled_credits=Decimal("30"),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_usage_record_keeps_provider_usage_separate_from_billing_allocation(db_session):
    usage = make_usage_record(
        unit="call",
        quantity=Decimal("1"),
        credits=Decimal("30.0000"),
        provider_usage={"input_tokens": 41, "output_tokens": 9},
    )
    db_session.add(usage)
    db_session.commit()
    assert usage.unit == "call"
    assert usage.quantity == Decimal("1")
    assert usage.credits == Decimal("30.0000")
    assert usage.provider_usage == {"input_tokens": 41, "output_tokens": 9}


def test_billing_operation_rejects_duplicate_user_scoped_idempotency_key(db_session):
    db_session.add(make_billing_operation())
    db_session.commit()

    db_session.add(make_billing_operation(idempotency_key="11111111-1111-4111-8111-111111111111"))
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requested_credits", Decimal("-0.0001")),
        ("settled_credits", Decimal("-0.0001")),
        ("released_credits", Decimal("-0.0001")),
    ],
)
def test_billing_operation_rejects_negative_amounts(db_session, field, value):
    db_session.add(make_billing_operation(**{field: value}))
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(Decimal("Infinity"), id="sqlite-inf-spelling"),
        pytest.param(Decimal("1.0000001"), id="fractional-scale-overflow"),
        pytest.param(Decimal("1000000000000"), id="integer-boundary-overflow"),
    ],
)
def test_billing_operation_rejects_requested_amounts_outside_numeric_18_6_domain(
    db_session,
    value: Decimal,
) -> None:
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(make_billing_operation(requested_credits=value))
            db_session.flush()


@pytest.mark.parametrize(
    "overrides",
    [
        {"completion_kind": "succeeded"},
        {"completed_at": datetime.now(UTC)},
        {"settled_credits": Decimal("1")},
        {"released_credits": Decimal("30")},
    ],
)
def test_billing_operation_rejects_invalid_in_progress_completion_fields(db_session, overrides):
    db_session.add(make_billing_operation(**overrides))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_billing_operation_allows_only_cosyvoice_zero_price_exception(db_session):
    db_session.add(
        make_billing_operation(
            operation="cosyvoice_brand_voice_create",
            requested_credits=Decimal("0"),
        )
    )
    db_session.commit()

    db_session.add(
        make_billing_operation(
            idempotency_key="22222222-2222-4222-8222-222222222222",
            operation="scene_prompt",
            requested_credits=Decimal("0"),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_billing_operation_rejects_nonzero_cosyvoice_free_operation(db_session):
    db_session.add(
        make_billing_operation(operation="cosyvoice_brand_voice_create")
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize("index", ["billing_item_index", "billing_pricing_line_index"])
def test_usage_record_rejects_negative_billing_indexes(db_session, index):
    operation = make_billing_operation()
    db_session.add(operation)
    db_session.commit()

    allocation = {
        "billing_operation_id": operation.id,
        "billing_item_index": 0,
        "billing_pricing_line_index": 0,
    }
    allocation[index] = -1
    db_session.add(make_usage_record(**allocation))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_usage_record_enforces_one_billing_item_per_operation(db_session):
    operation = make_billing_operation()
    db_session.add(operation)
    db_session.commit()
    db_session.add_all(
        [
            make_usage_record(
                billing_operation_id=operation.id,
                billing_item_index=0,
                billing_pricing_line_index=0,
            ),
            make_usage_record(
                billing_operation_id=operation.id,
                billing_item_index=0,
                billing_pricing_line_index=0,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_linked_usage_record_requires_both_billing_allocation_indexes(db_session):
    operation = make_billing_operation()
    db_session.add(operation)
    db_session.commit()

    db_session.add(make_usage_record(billing_operation_id=operation.id))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_billing_operation_delete_is_restricted_when_usage_exists(db_session):
    operation = make_billing_operation()
    db_session.add(operation)
    db_session.commit()
    db_session.add(
        make_usage_record(
            billing_operation_id=operation.id,
            billing_item_index=0,
            billing_pricing_line_index=0,
        )
    )
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(
            sa.delete(BillingOperation).where(BillingOperation.id == operation.id)
        )
        db_session.commit()


def test_billing_core_migration_creates_schema_and_refuses_downgrade_with_rows():
    migration = _load_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO tenants (id) VALUES ('tenant-a')"))
        connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))
        connection.execute(
            sa.text(
                "CREATE TABLE usage_records ("
                "id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL)"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "billing_operations" in inspector.get_table_names()
        billing_checks = {
            str(constraint["name"]): str(constraint["sqltext"])
            for constraint in inspector.get_check_constraints("billing_operations")
        }
        assert {
            "ck_billing_operations_amounts_finite",
            "ck_billing_operations_amounts_nonnegative",
        } <= set(billing_checks)
        assert "ck_billing_operations_amounts_upper_bound" not in billing_checks
        assert "ck_billing_operations_amounts_scale" not in billing_checks
        assert "requested_credits < 1000000000000" in billing_checks[
            "ck_billing_operations_amounts_nonnegative"
        ]
        assert "requested_credits = ROUND(requested_credits, 6)" in billing_checks[
            "ck_billing_operations_amounts_nonnegative"
        ]
        assert {
            "result_type",
            "result_id",
            "error_code",
            "error_http_status",
        } <= {column["name"] for column in inspector.get_columns("billing_operations")}
        assert {
            "billing_operation_id",
            "billing_item_index",
            "billing_pricing_line_index",
            "provider_usage",
        } <= {column["name"] for column in inspector.get_columns("usage_records")}
        foreign_keys = inspector.get_foreign_keys("usage_records")
        assert any(
            foreign_key["constrained_columns"] == ["billing_operation_id"]
            and foreign_key["options"].get("ondelete") == "RESTRICT"
            for foreign_key in foreign_keys
        )
        connection.execute(
            sa.text(
                "INSERT INTO billing_operations ("
                "id, tenant_id, user_id, operation, idempotency_key, request_hash, quote_hash, "
                "pricing_snapshot, requested_credits, settled_credits, released_credits, status) "
                "VALUES ('op-1', 'tenant-a', 'user-a', 'scene_prompt', 'key-1', "
                "'a', 'b', '{}', 1, 0, 0, 'in_progress')"
            )
        )
        with pytest.raises(RuntimeError, match="Cannot downgrade 20260829_0036"):
            migration.downgrade()


@pytest.mark.parametrize(
    "stored_json",
    [
        pytest.param("null", id="json-null"),
        pytest.param("{}", id="empty-object"),
        pytest.param('{"input_tokens":41}', id="standalone-provider-telemetry"),
    ],
)
def test_billing_core_migration_refuses_to_drop_standalone_provider_usage(
    stored_json: str,
) -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            sa.text(
                "CREATE TABLE usage_records ("
                "id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL)"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO usage_records (id, tenant_id, provider_usage) "
                "VALUES ('standalone-usage', 'tenant-a', :provider_usage)"
            ),
            {"provider_usage": stored_json},
        )

        with pytest.raises(RuntimeError, match="provider_usage"):
            migration.downgrade()

        assert "provider_usage" in {
            column["name"] for column in sa.inspect(connection).get_columns("usage_records")
        }
        assert connection.scalar(
            sa.text(
                "SELECT provider_usage FROM usage_records WHERE id = 'standalone-usage'"
            )
        ) == stored_json
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM billing_operations")) == 0
    engine.dispose()


def test_billing_core_migration_allows_downgrade_when_provider_usage_is_sql_null() -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            sa.text(
                "CREATE TABLE usage_records ("
                "id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL)"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO usage_records (id, tenant_id, provider_usage) "
                "VALUES ('sql-null-usage', 'tenant-a', NULL)"
            )
        )

        migration.downgrade()

        assert "provider_usage" not in {
            column["name"] for column in sa.inspect(connection).get_columns("usage_records")
        }
    engine.dispose()


@pytest.mark.parametrize(
    "stored_json",
    [
        pytest.param("null", id="jsonb-null"),
        pytest.param("{}", id="jsonb-empty-object"),
        pytest.param(
            '{"input_tokens":41}',
            id="jsonb-provider-telemetry",
        ),
    ],
)
def test_real_postgresql_migration_refuses_stored_provider_usage_json(
    postgres_billing_domain_schema,
    stored_json: str,
) -> None:
    engine, schema = postgres_billing_domain_schema
    with _schema_transaction(engine, schema) as connection:
        migration = _load_migration()
        migration.op = Operations(MigrationContext.configure(connection))
        connection.execute(
            sa.text(
                "INSERT INTO usage_records (id, tenant_id, provider_usage) "
                "VALUES ('postgres-provider-usage', 'tenant-a', "
                "CAST(:provider_usage AS JSONB))"
            ),
            {"provider_usage": stored_json},
        )

        with pytest.raises(RuntimeError, match="provider_usage"):
            migration.downgrade()

        assert "provider_usage" in {
            column["name"] for column in sa.inspect(connection).get_columns("usage_records")
        }


def test_real_postgresql_migration_treats_sql_null_as_absent_provider_usage(
    postgres_billing_domain_schema,
) -> None:
    engine, schema = postgres_billing_domain_schema
    with _schema_transaction(engine, schema) as connection:
        migration = _load_migration()
        migration.op = Operations(MigrationContext.configure(connection))
        connection.execute(
            sa.text(
                "INSERT INTO usage_records (id, tenant_id, provider_usage) "
                "VALUES ('postgres-sql-null', 'tenant-a', NULL)"
            )
        )

        migration.downgrade()

        assert "provider_usage" not in {
            column["name"] for column in sa.inspect(connection).get_columns("usage_records")
        }


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("inf"), id="sqlite-inf-spelling"),
        pytest.param("1.0000001", id="fractional-scale-overflow"),
        pytest.param(1_000_000_000_000, id="integer-boundary-overflow"),
    ],
)
def test_billing_core_migration_rejects_requested_amounts_outside_domain(value: object) -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO tenants (id) VALUES ('tenant-a')"))
        connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))
        connection.execute(
            sa.text(
                "CREATE TABLE usage_records ("
                "id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL)"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO billing_operations ("
                    "id, tenant_id, user_id, operation, idempotency_key, request_hash, quote_hash, "
                    "pricing_snapshot, requested_credits, settled_credits, released_credits, "
                    "status) "
                    "VALUES ('invalid-op', 'tenant-a', 'user-a', 'scene_prompt', 'invalid-key', "
                    "'a', 'b', '{}', :value, 0, 0, 'in_progress')"
                ),
                {"value": value},
            )
    engine.dispose()


def test_real_postgresql_billing_migration_rejects_fractional_scale_before_rounding(
    postgres_billing_domain_schema,
) -> None:
    engine, schema = postgres_billing_domain_schema
    with _schema_transaction(engine, schema) as connection, pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO billing_operations ("
                    "id, tenant_id, user_id, operation, idempotency_key, request_hash, quote_hash, "
                    "pricing_snapshot, requested_credits, settled_credits, released_credits, "
                    "status) "
                    "VALUES ('postgres-invalid-op', 'tenant-a', 'user-a', 'scene_prompt', "
                    "'postgres-invalid-key', 'a', 'b', '{}', :value, 0, 0, 'in_progress')"
                ),
                {"value": Decimal("1.0000001")},
            )


def test_postgresql_schema_path_emits_finite_amount_and_allocation_guards():
    migration = _load_migration()
    billing_model_ddl = str(
        sa.schema.CreateTable(BillingOperation.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    usage_model_ddl = str(
        sa.schema.CreateTable(UsageRecord.__table__).compile(dialect=postgresql.dialect())
    )
    output = StringIO()
    migration.op = Operations(
        MigrationContext.configure(
            dialect=postgresql.dialect(),
            opts={"as_sql": True, "output_buffer": output},
        )
    )

    migration.upgrade()

    statements = output.getvalue()
    assert "JSONB" in billing_model_ddl
    assert "requested_credits NUMERIC NOT NULL" in billing_model_ddl
    assert "LOWER(CAST(requested_credits AS TEXT))" in billing_model_ddl
    assert "requested_credits < 1000000000000" in billing_model_ddl
    assert "requested_credits = ROUND(requested_credits, 6)" in billing_model_ddl
    assert "ck_billing_operations_amounts_upper_bound" not in billing_model_ddl
    assert "ck_billing_operations_amounts_scale" not in billing_model_ddl
    assert "billing_operation_id IS NULL" in usage_model_ddl
    assert "billing_item_index IS NOT NULL" in usage_model_ddl
    assert "billing_pricing_line_index IS NOT NULL" in usage_model_ddl
    assert "LOWER(CAST(requested_credits AS TEXT))" in statements
    assert "LOWER(CAST(settled_credits AS TEXT))" in statements
    assert "'nan', 'infinity', '-infinity', 'inf', '-inf'" in statements
    assert "requested_credits < 1000000000000" in statements
    assert "released_credits = ROUND(released_credits, 6)" in statements
    assert "ck_billing_operations_amounts_upper_bound" not in statements
    assert "ck_billing_operations_amounts_scale" not in statements
    assert "billing_operation_id IS NULL" in statements
    assert "billing_item_index IS NOT NULL" in statements
    assert "billing_pricing_line_index IS NOT NULL" in statements
