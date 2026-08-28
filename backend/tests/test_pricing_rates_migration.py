from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.db.models import Base, CreditRate, UsageRecord

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
                effective_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    assert "script_generate" in model_credit_checks
    assert "scene_prompt" in model_credit_checks
    assert "script_generate" in model_usage_checks
    assert "character" in model_usage_checks
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
            "usage_records",
            "id, capability, unit, quantity, credits",
            "'negative-usage', 'image', 'image', -1, 80",
            "negative-usage",
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
    assert "'char'" in statements
    assert "'character'" in statements
    assert "NaN" in statements and "Infinity" in statements
    assert "WHERE tenant_id IS NULL AND is_active" in statements
    assert "WHERE tenant_id IS NOT NULL AND is_active" in statements
