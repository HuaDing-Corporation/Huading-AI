"""Centralize authoritative pricing rates and numeric guards."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "20260829_0037"
down_revision = "20260829_0036"
branch_labels = None
depends_on = None


_CREDIT_RATE_CAPABILITIES = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt', "
    "'reverse_prompt_video', 'script_generate', 'scene_prompt')"
)
_CREDIT_RATE_CAPABILITIES_LEGACY = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt', "
    "'reverse_prompt_video')"
)
_USAGE_CAPABILITIES = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt', "
    "'reverse_prompt_video', 'scene_prompt', 'chat', 'script_generate')"
)
_USAGE_CAPABILITIES_LEGACY = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt', "
    "'reverse_prompt_video', 'scene_prompt', 'chat')"
)
_USAGE_UNITS = "unit IN ('second', 'call', 'token', 'image', 'char', 'character')"
_USAGE_UNITS_LEGACY = "unit IN ('second', 'call', 'token', 'image', 'char')"
_RATE_AMOUNT_GUARD = (
    "credits_per_unit >= 0 AND "
    "CAST(credits_per_unit AS TEXT) NOT IN ('NaN', 'Infinity', '-Infinity')"
)
_USAGE_AMOUNT_GUARD = (
    "quantity >= 0 AND credits >= 0 AND "
    "CAST(quantity AS TEXT) NOT IN ('NaN', 'Infinity', '-Infinity') AND "
    "CAST(credits AS TEXT) NOT IN ('NaN', 'Infinity', '-Infinity')"
)
_PLATFORM_ACTIVE_INDEX = "uq_credit_rates_platform_active_capability_unit"
_TENANT_ACTIVE_INDEX = "uq_credit_rates_tenant_active_capability_unit"

_TARGETS = (
    (
        "00000000-0000-0000-0000-000000000371",
        "script_generate",
        "call",
        Decimal("1.0000"),
        frozenset({Decimal("1.0000")}),
    ),
    (
        "00000000-0000-0000-0000-000000000372",
        "scene_prompt",
        "call",
        Decimal("30.0000"),
        frozenset({Decimal("30.0000")}),
    ),
    (
        "00000000-0000-0000-0000-000000000373",
        "image",
        "image",
        Decimal("80.0000"),
        frozenset({Decimal("10.0000"), Decimal("80.0000")}),
    ),
    (
        "00000000-0000-0000-0000-000000000374",
        "voice_clone",
        "call",
        Decimal("30000.0000"),
        frozenset({Decimal("30.0000"), Decimal("30000.0000")}),
    ),
    (
        "00000000-0000-0000-0000-000000000375",
        "tts",
        "character",
        Decimal("0.1000"),
        frozenset({Decimal("0.1000")}),
    ),
)

_POSITIVE_RATE_PAIRS = {
    ("script_generate", "call"),
    ("scene_prompt", "call"),
    ("image", "image"),
    ("tts", "character"),
    ("avatar", "second"),
    ("video", "second"),
    ("video_gen", "second"),
    ("reverse_prompt", "call"),
}
_PLATFORM_POSITIVE_RATE_PAIRS = {("voice_clone", "call")}


def _ids(rows) -> str:
    return ", ".join(sorted(str(row[0]) for row in rows))


def _audit_existing_data() -> None:
    connection = op.get_bind()
    duplicate_groups = connection.execute(
        sa.text(
            """
            SELECT tenant_id, capability, unit
            FROM credit_rates
            WHERE is_active IS TRUE
            GROUP BY tenant_id, capability, unit
            HAVING COUNT(*) > 1
            """
        )
    ).all()
    duplicate_ids: list[tuple[str]] = []
    for tenant_id, capability, unit in duplicate_groups:
        tenant_predicate = "tenant_id IS NULL" if tenant_id is None else "tenant_id = :tenant_id"
        duplicate_ids.extend(
            connection.execute(
                sa.text(
                    "SELECT id FROM credit_rates "
                    f"WHERE {tenant_predicate} AND capability = :capability AND unit = :unit "
                    "AND is_active IS TRUE ORDER BY id"
                ),
                {"tenant_id": tenant_id, "capability": capability, "unit": unit},
            ).all()
        )
    if duplicate_ids:
        raise RuntimeError(f"Duplicate active credit rate IDs: {_ids(duplicate_ids)}")

    invalid_rate_rows = connection.execute(
        sa.text(
            """
            SELECT id FROM credit_rates
            WHERE credits_per_unit < 0
               OR LOWER(CAST(credits_per_unit AS TEXT)) IN
                  ('nan', 'infinity', '-infinity', 'inf', '-inf')
            ORDER BY id
            """
        )
    ).all()
    if invalid_rate_rows:
        raise RuntimeError(f"Invalid credit rate amount IDs: {_ids(invalid_rate_rows)}")

    invalid_usage_rows = connection.execute(
        sa.text(
            """
            SELECT id FROM usage_records
            WHERE quantity < 0 OR credits < 0
               OR LOWER(CAST(quantity AS TEXT)) IN
                  ('nan', 'infinity', '-infinity', 'inf', '-inf')
               OR LOWER(CAST(credits AS TEXT)) IN
                  ('nan', 'infinity', '-infinity', 'inf', '-inf')
            ORDER BY id
            """
        )
    ).all()
    if invalid_usage_rows:
        raise RuntimeError(f"Invalid usage amount IDs: {_ids(invalid_usage_rows)}")

    zero_ids: list[tuple[str]] = []
    for capability, unit in sorted(_POSITIVE_RATE_PAIRS):
        zero_ids.extend(
            connection.execute(
                sa.text(
                    "SELECT id FROM credit_rates "
                    "WHERE capability = :capability AND unit = :unit "
                    "AND is_active IS TRUE AND credits_per_unit = 0 ORDER BY id"
                ),
                {"capability": capability, "unit": unit},
            ).all()
        )
    for capability, unit in sorted(_PLATFORM_POSITIVE_RATE_PAIRS):
        zero_ids.extend(
            connection.execute(
                sa.text(
                    "SELECT id FROM credit_rates "
                    "WHERE tenant_id IS NULL AND capability = :capability AND unit = :unit "
                    "AND is_active IS TRUE AND credits_per_unit = 0 ORDER BY id"
                ),
                {"capability": capability, "unit": unit},
            ).all()
        )
    if zero_ids:
        raise RuntimeError(f"Active zero credit rate IDs: {_ids(zero_ids)}")

    drift_ids: list[tuple[str]] = []
    for _, capability, unit, _, allowed_values in _TARGETS:
        row = connection.execute(
            sa.text(
                "SELECT id, credits_per_unit FROM credit_rates "
                "WHERE tenant_id IS NULL AND capability = :capability AND unit = :unit "
                "AND is_active IS TRUE"
            ),
            {"capability": capability, "unit": unit},
        ).first()
        if row is not None and Decimal(str(row.credits_per_unit)) not in allowed_values:
            drift_ids.append((row.id,))
    if drift_ids:
        raise RuntimeError(f"Unexpected platform credit rate IDs: {_ids(drift_ids)}")


def _apply_schema_changes() -> None:
    with op.batch_alter_table("credit_rates") as batch_op:
        batch_op.drop_constraint("ck_credit_rates_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_rates_capability", _CREDIT_RATE_CAPABILITIES
        )
        batch_op.create_check_constraint(
            "ck_credit_rates_credits_per_unit_valid", _RATE_AMOUNT_GUARD
        )

    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("ck_usage_records_capability", type_="check")
        batch_op.drop_constraint("ck_usage_records_unit", type_="check")
        batch_op.create_check_constraint(
            "ck_usage_records_capability", _USAGE_CAPABILITIES
        )
        batch_op.create_check_constraint("ck_usage_records_unit", _USAGE_UNITS)
        batch_op.create_check_constraint(
            "ck_usage_records_amounts_valid", _USAGE_AMOUNT_GUARD
        )

    active_platform = sa.text("tenant_id IS NULL AND is_active")
    active_tenant = sa.text("tenant_id IS NOT NULL AND is_active")
    op.create_index(
        _PLATFORM_ACTIVE_INDEX,
        "credit_rates",
        ["capability", "unit"],
        unique=True,
        postgresql_where=active_platform,
        sqlite_where=active_platform,
    )
    op.create_index(
        _TENANT_ACTIVE_INDEX,
        "credit_rates",
        ["tenant_id", "capability", "unit"],
        unique=True,
        postgresql_where=active_tenant,
        sqlite_where=active_tenant,
    )


def _seed_platform_rates() -> None:
    connection = op.get_bind()
    effective_at = datetime.now(UTC)
    for rate_id, capability, unit, target, _ in _TARGETS:
        row = connection.execute(
            sa.text(
                "SELECT id FROM credit_rates "
                "WHERE tenant_id IS NULL AND capability = :capability AND unit = :unit "
                "AND is_active IS TRUE"
            ),
            {"capability": capability, "unit": unit},
        ).first()
        if row is None:
            insert_rate = sa.text(
                "INSERT INTO credit_rates "
                "(id, tenant_id, capability, unit, credits_per_unit, is_active, effective_at) "
                "VALUES (:id, NULL, :capability, :unit, :target, TRUE, :effective_at)"
            ).bindparams(
                sa.bindparam("target", type_=sa.Numeric(12, 4)),
                sa.bindparam("effective_at", type_=sa.DateTime(timezone=True)),
            )
            connection.execute(
                insert_rate,
                {
                    "id": rate_id,
                    "capability": capability,
                    "unit": unit,
                    "target": target,
                    "effective_at": effective_at,
                },
            )
        else:
            update_rate = sa.text(
                "UPDATE credit_rates SET credits_per_unit = :target "
                "WHERE id = :id AND is_active IS TRUE"
            ).bindparams(sa.bindparam("target", type_=sa.Numeric(12, 4)))
            connection.execute(
                update_rate,
                {"id": row.id, "target": target},
            )


def upgrade() -> None:
    _audit_existing_data()
    _apply_schema_changes()
    _seed_platform_rates()


def downgrade() -> None:
    connection = op.get_bind()
    incompatible_usage = connection.execute(
        sa.text(
            "SELECT id FROM usage_records "
            "WHERE capability = 'script_generate' OR unit = 'character' ORDER BY id"
        )
    ).all()
    incompatible_tenant_rates = connection.execute(
        sa.text(
            "SELECT id FROM credit_rates WHERE tenant_id IS NOT NULL "
            "AND capability IN ('script_generate', 'scene_prompt') ORDER BY id"
        )
    ).all()
    if incompatible_usage or incompatible_tenant_rates:
        raise RuntimeError(
            "Cannot downgrade 20260829_0037 with incompatible IDs: "
            f"{_ids([*incompatible_usage, *incompatible_tenant_rates])}"
        )

    for rate_id, capability, unit, target, _ in _TARGETS[:2]:
        row = connection.execute(
            sa.text(
                "SELECT credits_per_unit FROM credit_rates WHERE id = :id "
                "AND tenant_id IS NULL AND capability = :capability AND unit = :unit "
                "AND is_active IS TRUE"
            ),
            {"id": rate_id, "capability": capability, "unit": unit},
        ).first()
        if row is not None and Decimal(str(row.credits_per_unit)) != target:
            raise RuntimeError(f"Cannot remove modified seeded credit rate ID: {rate_id}")
        connection.execute(sa.text("DELETE FROM credit_rates WHERE id = :id"), {"id": rate_id})

    op.drop_index(_TENANT_ACTIVE_INDEX, table_name="credit_rates")
    op.drop_index(_PLATFORM_ACTIVE_INDEX, table_name="credit_rates")
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("ck_usage_records_amounts_valid", type_="check")
        batch_op.drop_constraint("ck_usage_records_capability", type_="check")
        batch_op.drop_constraint("ck_usage_records_unit", type_="check")
        batch_op.create_check_constraint(
            "ck_usage_records_capability", _USAGE_CAPABILITIES_LEGACY
        )
        batch_op.create_check_constraint("ck_usage_records_unit", _USAGE_UNITS_LEGACY)
    with op.batch_alter_table("credit_rates") as batch_op:
        batch_op.drop_constraint("ck_credit_rates_credits_per_unit_valid", type_="check")
        batch_op.drop_constraint("ck_credit_rates_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_rates_capability", _CREDIT_RATE_CAPABILITIES_LEGACY
        )
