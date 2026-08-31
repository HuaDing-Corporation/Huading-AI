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
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.db.models import Base, BillingOperation, CreditRate, UsageRecord

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260829_0037_pricing_rates.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("pricing_rates_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _create_legacy_schema(connection) -> None:
    connection.execute(
        sa.text(
            """
            CREATE TABLE credit_rates (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36),
                capability VARCHAR(32) NOT NULL,
                unit VARCHAR(32) NOT NULL,
                credits_per_unit NUMERIC(12, 4) NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                effective_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT ck_credit_rates_capability CHECK (
                    capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr',
                    'publish', 'voice_clone', 'video_gen', 'reverse_prompt',
                    'reverse_prompt_video')
                ),
                CONSTRAINT ck_credit_rates_unit CHECK (
                    unit IN ('second', 'call', 'token', 'image', 'character')
                )
            )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            CREATE TABLE billing_operations (
                id VARCHAR(36) PRIMARY KEY,
                requested_credits NUMERIC(18, 6) NOT NULL,
                settled_credits NUMERIC(18, 6) NOT NULL,
                released_credits NUMERIC(18, 6) NOT NULL,
                CONSTRAINT ck_billing_operations_amounts_finite CHECK (
                    CAST(requested_credits AS TEXT) NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND CAST(settled_credits AS TEXT) NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND CAST(released_credits AS TEXT) NOT IN ('NaN', 'Infinity', '-Infinity')
                ),
                CONSTRAINT ck_billing_operations_amounts_nonnegative CHECK (
                    requested_credits >= 0
                    AND settled_credits >= 0
                    AND released_credits >= 0
                )
            )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            CREATE TABLE usage_records (
                id VARCHAR(36) PRIMARY KEY,
                capability VARCHAR(32) NOT NULL,
                unit VARCHAR(32) NOT NULL,
                quantity NUMERIC(12, 3) NOT NULL,
                credits NUMERIC(18, 6) NOT NULL,
                CONSTRAINT ck_usage_records_capability CHECK (
                    capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr',
                    'publish', 'voice_clone', 'video_gen', 'reverse_prompt',
                    'reverse_prompt_video', 'scene_prompt', 'chat')
                ),
                CONSTRAINT ck_usage_records_unit CHECK (
                    unit IN ('second', 'call', 'token', 'image', 'char')
                )
            )
            """
        )
    )


@pytest.fixture
def sqlite_engine():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _create_legacy_schema(connection)
    try:
        yield engine
    finally:
        engine.dispose()


def _run_upgrade(engine) -> None:
    migration = _load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()


@contextmanager
def _schema_transaction(engine, schema: str):
    with engine.begin() as connection:
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        yield connection


@pytest.fixture
def postgres_pricing_domain_schema():
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the real PostgreSQL pricing test.")
    url = make_url(database_url)
    if url.host not in {"localhost", "127.0.0.1"} or not str(url.database).startswith(
        "huading_pricing_test_"
    ):
        pytest.fail("TEST_POSTGRES_URL must target an isolated local huading_pricing_test_* DB")

    engine = sa.create_engine(url, pool_pre_ping=True)
    schema = f"pricing_amount_domain_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    try:
        with _schema_transaction(engine, schema) as connection:
            _create_legacy_schema(connection)
            migration = _load_migration()
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
        yield engine, schema
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def _seed_rate(connection, **overrides: object) -> None:
    values = {
        "id": "rate-image",
        "tenant_id": None,
        "capability": "image",
        "unit": "image",
        "credits_per_unit": Decimal("10.0000"),
        "is_active": True,
        "effective_at": datetime(2025, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    values["credits_per_unit"] = str(values["credits_per_unit"])
    connection.execute(
        sa.text(
            "INSERT INTO credit_rates "
            "(id, tenant_id, capability, unit, credits_per_unit, is_active, effective_at) "
            "VALUES (:id, :tenant_id, :capability, :unit, :credits_per_unit, :is_active, "
            ":effective_at)"
        ),
        values,
    )


def test_migration_follows_billing_core_and_is_self_contained() -> None:
    migration = _load_migration()
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert migration.revision == "20260829_0037"
    assert migration.down_revision == "20260829_0036"
    assert "from app" not in source
    assert "import app" not in source


def test_upgrade_seeds_canonical_rates_and_preserves_tenant_and_history(sqlite_engine) -> None:
    with sqlite_engine.begin() as connection:
        _seed_rate(connection)
        _seed_rate(
            connection,
            id="platform-voice-old",
            capability="voice_clone",
            unit="call",
            credits_per_unit=Decimal("30.0000"),
        )
        _seed_rate(
            connection,
            id="platform-tts",
            capability="tts",
            unit="character",
            credits_per_unit=Decimal("0.1000"),
        )
        _seed_rate(
            connection,
            id="tenant-image",
            tenant_id="tenant-a",
            credits_per_unit=Decimal("42.0000"),
        )
        _seed_rate(
            connection,
            id="historic-tenant-voice-zero",
            tenant_id="tenant-a",
            capability="voice_clone",
            unit="call",
            credits_per_unit=Decimal("0.0000"),
        )
        _seed_rate(
            connection,
            id="historic-image",
            credits_per_unit=Decimal("7.0000"),
            is_active=False,
        )

    _run_upgrade(sqlite_engine)

    with sqlite_engine.begin() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT id, tenant_id, capability, unit, credits_per_unit, is_active "
                "FROM credit_rates ORDER BY id"
            )
        ).mappings()
        values = {row["id"]: row for row in rows}
    platform = {
        (row["capability"], row["unit"]): Decimal(str(row["credits_per_unit"]))
        for row in values.values()
        if row["tenant_id"] is None and row["is_active"]
    }
    assert platform == {
        ("image", "image"): Decimal("80"),
        ("voice_clone", "call"): Decimal("30000"),
        ("tts", "character"): Decimal("0.1"),
        ("script_generate", "call"): Decimal("1"),
        ("scene_prompt", "call"): Decimal("30"),
    }
    assert Decimal(str(values["tenant-image"]["credits_per_unit"])) == Decimal("42")
    assert Decimal(str(values["historic-tenant-voice-zero"]["credits_per_unit"])) == 0
    assert values["historic-tenant-voice-zero"]["is_active"]
    assert Decimal(str(values["historic-image"]["credits_per_unit"])) == Decimal("7")
    assert not values["historic-image"]["is_active"]


@pytest.mark.parametrize(
    ("old_id", "capability", "unit", "old_price", "target_price"),
    [
        ("legacy-platform-image", "image", "image", "10.0000", "80.0000"),
        (
            "legacy-platform-voice",
            "voice_clone",
            "call",
            "30.0000",
            "30000.0000",
        ),
    ],
)
def test_upgrade_closes_old_platform_rate_and_inserts_new_effective_period(
    sqlite_engine,
    old_id: str,
    capability: str,
    unit: str,
    old_price: str,
    target_price: str,
) -> None:
    old_effective_at = datetime(2024, 2, 3, 4, 5, 6, tzinfo=UTC)
    with sqlite_engine.begin() as connection:
        _seed_rate(
            connection,
            id=old_id,
            capability=capability,
            unit=unit,
            credits_per_unit=Decimal(old_price),
            effective_at=old_effective_at,
        )

    _run_upgrade(sqlite_engine)

    with sqlite_engine.begin() as connection:
        rate_rows = connection.execute(
            sa.text(
                "SELECT id, credits_per_unit, is_active, effective_at FROM credit_rates "
                "WHERE tenant_id IS NULL AND capability = :capability AND unit = :unit "
                "ORDER BY effective_at, id"
            ),
            {"capability": capability, "unit": unit},
        ).mappings().all()

    assert len(rate_rows) == 2
    old_row = next(row for row in rate_rows if row["id"] == old_id)
    new_row = next(row for row in rate_rows if row["id"] != old_id)
    assert Decimal(str(old_row["credits_per_unit"])) == Decimal(old_price)
    assert not old_row["is_active"]
    assert str(old_row["effective_at"]).startswith("2024-02-03")
    assert Decimal(str(new_row["credits_per_unit"])) == Decimal(target_price)
    assert new_row["is_active"]
    assert str(new_row["effective_at"]) != str(old_row["effective_at"])


def test_model_metadata_matches_0037_capabilities_units_guards_and_indexes() -> None:
    credit_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in CreditRate.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    usage_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in UsageRecord.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    billing_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in BillingOperation.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    indexes = {index.name: index for index in CreditRate.__table__.indexes}

    assert "script_generate" in credit_checks["ck_credit_rates_capability"]
    assert "scene_prompt" in credit_checks["ck_credit_rates_capability"]
    assert "credits_per_unit >= 0" in credit_checks[
        "ck_credit_rates_credits_per_unit_valid"
    ]
    assert "script_generate" in usage_checks["ck_usage_records_capability"]
    assert "'char'" in usage_checks["ck_usage_records_unit"]
    assert "'character'" in usage_checks["ck_usage_records_unit"]
    assert "quantity >= 0" in usage_checks["ck_usage_records_amounts_valid"]
    assert "credits >= 0" in usage_checks["ck_usage_records_amounts_valid"]
    assert "credits < 1000000000000" in usage_checks["ck_usage_records_amounts_valid"]
    assert "'inf'" in billing_checks["ck_billing_operations_amounts_finite"]
    assert "requested_credits < 1000000000000" in billing_checks[
        "ck_billing_operations_amounts_nonnegative"
    ]
    assert "requested_credits = ROUND(requested_credits, 6)" in billing_checks[
        "ck_billing_operations_amounts_nonnegative"
    ]
    for name in (
        "uq_credit_rates_platform_active_capability_unit",
        "uq_credit_rates_tenant_active_capability_unit",
    ):
        assert indexes[name].unique
        assert indexes[name].dialect_options["postgresql"]["where"] is not None
        assert indexes[name].dialect_options["sqlite"]["where"] is not None

    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = sa.inspect(engine)
    model_credit_checks = " ".join(
        str(item["sqltext"]) for item in inspector.get_check_constraints("credit_rates")
    )
    model_usage_checks = " ".join(
        str(item["sqltext"]) for item in inspector.get_check_constraints("usage_records")
    )
    model_billing_checks = {
        str(item["name"]): str(item["sqltext"])
        for item in inspector.get_check_constraints("billing_operations")
    }
    assert "script_generate" in model_credit_checks
    assert "scene_prompt" in model_credit_checks
    assert "script_generate" in model_usage_checks
    assert "character" in model_usage_checks
    assert "credits < 1000000000000" in model_usage_checks
    assert set(model_billing_checks) >= {
        "ck_billing_operations_amounts_finite",
        "ck_billing_operations_amounts_nonnegative",
    }
    assert "requested_credits < 1000000000000" in model_billing_checks[
        "ck_billing_operations_amounts_nonnegative"
    ]
    engine.dispose()


def test_upgrade_reports_duplicate_active_ids_and_performs_no_updates(sqlite_engine) -> None:
    with sqlite_engine.begin() as connection:
        _seed_rate(connection, id="duplicate-b")
        _seed_rate(connection, id="duplicate-a")

    with pytest.raises(RuntimeError, match="duplicate-a.*duplicate-b"):
        _run_upgrade(sqlite_engine)

    with sqlite_engine.begin() as connection:
        rates = list(
            connection.scalars(
                sa.text("SELECT credits_per_unit FROM credit_rates ORDER BY id")
            )
        )
    assert [Decimal(str(value)) for value in rates] == [Decimal("10"), Decimal("10")]


@pytest.mark.parametrize(
    ("table", "columns", "values", "expected_id"),
    [
        (
            "credit_rates",
            "id, tenant_id, capability, unit, credits_per_unit, is_active",
            "'negative-rate', NULL, 'image', 'image', -1, FALSE",
            "negative-rate",
        ),
        (
            "credit_rates",
            "id, tenant_id, capability, unit, credits_per_unit, is_active",
            "'zero-active-rate', NULL, 'image', 'image', 0, TRUE",
            "zero-active-rate",
        ),
        (
            "credit_rates",
            "id, tenant_id, capability, unit, credits_per_unit, is_active",
            "'overflow-rate', NULL, 'image', 'image', 100000000, FALSE",
            "overflow-rate",
        ),
        (
            "usage_records",
            "id, capability, unit, quantity, credits",
            "'negative-usage', 'image', 'image', -1, 80",
            "negative-usage",
        ),
        (
            "usage_records",
            "id, capability, unit, quantity, credits",
            "'overflow-usage', 'image', 'image', 1, 1000000000000",
            "overflow-usage",
        ),
        (
            "credit_rates",
            "id, tenant_id, capability, unit, credits_per_unit, is_active",
            "'scale-rate', NULL, 'image', 'image', 1.00001, FALSE",
            "scale-rate",
        ),
        (
            "usage_records",
            "id, capability, unit, quantity, credits",
            "'scale-quantity', 'image', 'image', 1.0001, 1",
            "scale-quantity",
        ),
        (
            "usage_records",
            "id, capability, unit, quantity, credits",
            "'scale-credits', 'image', 'image', 1, 1.0000001",
            "scale-credits",
        ),
        (
            "billing_operations",
            "id, requested_credits, settled_credits, released_credits",
            "'scale-billing', 1.0000001, 0, 0",
            "scale-billing",
        ),
    ],
)
def test_upgrade_reports_invalid_amount_ids_before_writes(
    sqlite_engine, table, columns, values, expected_id
) -> None:
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
        connection.execute(sa.text(f"INSERT INTO {table} ({columns}) VALUES ({values})"))
        connection.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))

    with pytest.raises(RuntimeError, match=expected_id):
        _run_upgrade(sqlite_engine)

    with sqlite_engine.begin() as connection:
        assert connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM credit_rates "
                "WHERE capability IN ('script_generate', 'scene_prompt')"
            )
        ) == 0


def test_compare_and_set_rejects_unknown_platform_value_without_partial_seed(sqlite_engine) -> None:
    with sqlite_engine.begin() as connection:
        _seed_rate(connection, credits_per_unit=Decimal("11.0000"))

    with pytest.raises(RuntimeError, match="rate-image"):
        _run_upgrade(sqlite_engine)

    with sqlite_engine.begin() as connection:
        assert connection.scalar(
            sa.text("SELECT credits_per_unit FROM credit_rates WHERE id = 'rate-image'")
        ) == Decimal("11.0000")
        assert connection.scalar(
            sa.text("SELECT COUNT(*) FROM credit_rates WHERE capability = 'script_generate'")
        ) == 0


def test_sqlite_constraints_accept_both_tts_units_and_reject_invalid_amounts(
    sqlite_engine,
) -> None:
    _run_upgrade(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO usage_records (id, capability, unit, quantity, credits) VALUES "
                "('historic-char', 'tts', 'char', 1, 0.1), "
                "('canonical-character', 'tts', 'character', 1, 0.1), "
                "('script', 'script_generate', 'call', 1, 1)"
            )
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO usage_records "
                    "(id, capability, unit, quantity, credits) "
                    "VALUES ('negative', 'tts', 'character', -1, 0.1)"
                )
            )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(float("nan"), id="nan"),
        pytest.param("1.00001", id="fractional-scale-overflow"),
        pytest.param(100_000_000.0, id="integer-boundary-overflow"),
        pytest.param(1e100, id="precision-overflow"),
    ],
)
def test_upgraded_sqlite_rejects_credit_rates_outside_numeric_12_4_domain(
    sqlite_engine,
    value: object,
) -> None:
    _run_upgrade(sqlite_engine)

    with sqlite_engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text(
                "INSERT INTO credit_rates "
                "(id, tenant_id, capability, unit, credits_per_unit, is_active) "
                "VALUES ('invalid-domain-rate', 'tenant-domain', 'image', 'image', "
                ":value, FALSE)"
            ),
            {"value": value},
        )


@pytest.mark.parametrize("column", ["quantity", "credits"])
def test_upgraded_sqlite_rejects_positive_infinity_usage_amounts(
    sqlite_engine,
    column: str,
) -> None:
    _run_upgrade(sqlite_engine)
    values = {"quantity": 1.0, "credits": 1.0, column: float("inf")}

    with sqlite_engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text(
                "INSERT INTO usage_records (id, capability, unit, quantity, credits) "
                "VALUES ('invalid-domain-usage', 'image', 'image', :quantity, :credits)"
            ),
            values,
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        pytest.param("quantity", "1.0001", id="quantity-scale"),
        pytest.param("credits", "1.0000001", id="credits-scale"),
    ],
)
def test_upgraded_sqlite_rejects_usage_fractional_scale_overflow(
    sqlite_engine,
    column: str,
    value: str,
) -> None:
    _run_upgrade(sqlite_engine)
    values = {"quantity": "1", "credits": "1", column: value}

    with sqlite_engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text(
                "INSERT INTO usage_records (id, capability, unit, quantity, credits) "
                "VALUES ('invalid-scale-usage', 'image', 'image', :quantity, :credits)"
            ),
            values,
        )


@pytest.mark.parametrize(
    "credits",
    [
        pytest.param("1000000000000.000000", id="first-integer-overflow"),
        pytest.param("1000000000000.000001", id="rounded-fractional-overflow"),
    ],
)
def test_upgraded_sqlite_rejects_usage_credits_outside_numeric_18_6_domain(
    sqlite_engine,
    credits: str,
) -> None:
    _run_upgrade(sqlite_engine)

    with sqlite_engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text(
                "INSERT INTO usage_records (id, capability, unit, quantity, credits) "
                "VALUES ('invalid-domain-usage', 'image', 'image', 1, :credits)"
            ),
            {"credits": credits},
        )


def test_upgraded_sqlite_accepts_numeric_12_4_maximum_and_rejects_next_integer(
    sqlite_engine,
) -> None:
    _run_upgrade(sqlite_engine)

    with sqlite_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO credit_rates "
                "(id, tenant_id, capability, unit, credits_per_unit, is_active) "
                "VALUES ('maximum-domain-rate', 'tenant-domain', 'image', 'image', "
                ":value, FALSE)"
            ),
            {"value": "99999999.9999"},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO credit_rates "
                    "(id, tenant_id, capability, unit, credits_per_unit, is_active) "
                    "VALUES ('overflow-domain-rate', 'tenant-domain', 'image', 'image', "
                    ":value, FALSE)"
                ),
                {"value": "100000000.0000"},
            )


def test_partial_unique_indexes_enforce_platform_and_tenant_active_rates(
    sqlite_engine,
) -> None:
    _run_upgrade(sqlite_engine)
    with sqlite_engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO credit_rates "
                    "(id, tenant_id, capability, unit, credits_per_unit, is_active) "
                    "VALUES ('platform-image-duplicate', NULL, 'image', 'image', 80, TRUE)"
                )
            )
    with sqlite_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO credit_rates "
                "(id, tenant_id, capability, unit, credits_per_unit, is_active) VALUES "
                "('tenant-one', 'tenant-a', 'image', 'image', 40, TRUE)"
            )
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO credit_rates "
                    "(id, tenant_id, capability, unit, credits_per_unit, is_active) "
                    "VALUES ('tenant-two', 'tenant-a', 'image', 'image', 41, TRUE)"
                )
            )


def test_postgresql_dialect_emits_finite_guards_partial_indexes_and_both_tts_units() -> None:
    migration = _load_migration()
    output = StringIO()
    migration.op = Operations(
        MigrationContext.configure(
            dialect=postgresql.dialect(),
            opts={"as_sql": True, "output_buffer": output},
        )
    )

    migration._apply_schema_changes()

    statements = output.getvalue()
    lowered = statements.lower()
    assert "'char'" in statements
    assert "'character'" in statements
    assert "'nan'" in lowered and "'infinity'" in lowered and "'inf'" in lowered
    assert "99999999.9999" in statements
    assert "credits < 1000000000000" in statements
    assert "ALTER COLUMN requested_credits TYPE NUMERIC" in statements
    assert "DROP CONSTRAINT ck_billing_operations_amounts_finite" in statements
    assert "DROP CONSTRAINT ck_billing_operations_amounts_nonnegative" in statements
    assert "requested_credits < 1000000000000" in statements
    assert "requested_credits = ROUND(requested_credits, 6)" in statements
    assert "WHERE tenant_id IS NULL AND is_active" in statements
    assert "WHERE tenant_id IS NOT NULL AND is_active" in statements


def test_real_postgresql_0037_widens_already_applied_0036_billing_amounts(
    postgres_pricing_domain_schema,
) -> None:
    engine, schema = postgres_pricing_domain_schema
    with _schema_transaction(engine, schema) as connection:
        columns = {
            str(column["name"]): column["type"]
            for column in sa.inspect(connection).get_columns("billing_operations")
        }
        for name in ("requested_credits", "settled_credits", "released_credits"):
            amount_type = columns[name]
            assert isinstance(amount_type, sa.Numeric)
            assert amount_type.precision is None
            assert amount_type.scale is None


@pytest.mark.parametrize(
    ("table", "columns", "values"),
    [
        pytest.param(
            "credit_rates",
            "id, tenant_id, capability, unit, credits_per_unit, is_active",
            {
                "id": "postgres-scale-rate",
                "tenant_id": "tenant-scale",
                "capability": "image",
                "unit": "image",
                "credits_per_unit": Decimal("1.00001"),
                "is_active": False,
            },
            id="rate-scale",
        ),
        pytest.param(
            "usage_records",
            "id, capability, unit, quantity, credits",
            {
                "id": "postgres-scale-quantity",
                "capability": "image",
                "unit": "image",
                "quantity": Decimal("1.0001"),
                "credits": Decimal("1"),
            },
            id="quantity-scale",
        ),
        pytest.param(
            "usage_records",
            "id, capability, unit, quantity, credits",
            {
                "id": "postgres-scale-credits",
                "capability": "image",
                "unit": "image",
                "quantity": Decimal("1"),
                "credits": Decimal("1.0000001"),
            },
            id="credits-scale",
        ),
        pytest.param(
            "billing_operations",
            "id, requested_credits, settled_credits, released_credits",
            {
                "id": "postgres-scale-billing",
                "requested_credits": Decimal("1.0000001"),
                "settled_credits": Decimal("0"),
                "released_credits": Decimal("0"),
            },
            id="billing-scale",
        ),
    ],
)
def test_real_postgresql_rejects_fractional_scale_before_fixed_scale_rounding(
    postgres_pricing_domain_schema,
    table: str,
    columns: str,
    values: dict[str, object],
) -> None:
    engine, schema = postgres_pricing_domain_schema
    placeholders = ", ".join(f":{name}" for name in values)
    with _schema_transaction(engine, schema) as connection, pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                sa.text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"),
                values,
            )


def test_0037_downgrade_preserves_billing_hardening_from_amended_0036(
    sqlite_engine,
) -> None:
    migration = _load_migration()
    with sqlite_engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.downgrade()

        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO billing_operations "
                    "(id, requested_credits, settled_credits, released_credits) "
                    "VALUES ('downgrade-scale-billing', 1.0000001, 0, 0)"
                )
            )
