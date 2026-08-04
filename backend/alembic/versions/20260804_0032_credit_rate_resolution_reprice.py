"""Reprice resolution-aware image and video credit rates."""

from __future__ import annotations

from decimal import Decimal

from alembic import op

revision = "20260804_0032"
down_revision = "20260724_0031"
branch_labels = None
depends_on = None

_UPGRADE_RATES = (
    ("video", "second", Decimal("80.0000"), Decimal("100.0000")),
    ("video_gen", "second", Decimal("80.0000"), Decimal("100.0000")),
    ("image", "image", Decimal("10.0000"), Decimal("80.0000")),
)
_DOWNGRADE_RATES = (
    ("video", "second", Decimal("100.0000"), Decimal("80.0000")),
    ("video_gen", "second", Decimal("100.0000"), Decimal("80.0000")),
    ("image", "image", Decimal("80.0000"), Decimal("10.0000")),
)


def upgrade() -> None:
    _apply_rates(_UPGRADE_RATES)


def downgrade() -> None:
    _apply_rates(_DOWNGRADE_RATES)


def _apply_rates(rates: tuple[tuple[str, str, Decimal, Decimal], ...]) -> None:
    values_sql = ",\n".join(
        "                "
        f"('{capability}', '{unit}', {expected_rate}, {replacement_rate})"
        for capability, unit, expected_rate, replacement_rate in rates
    )
    op.execute(
        f"""
        DO $credit_rate_resolution_reprice$
        DECLARE
            target RECORD;
            current_rate NUMERIC(12, 4);
            affected INTEGER;
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM credit_rates
                WHERE tenant_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Resolution credit-rate reprice requires zero tenant overrides';
            END IF;

            FOR target IN
                SELECT *
                FROM (VALUES
{values_sql}
                ) AS targets(capability, unit, expected_rate, replacement_rate)
            LOOP
                SELECT rates.credits_per_unit
                INTO STRICT current_rate
                FROM credit_rates AS rates
                WHERE rates.tenant_id IS NULL
                  AND rates.capability = target.capability
                  AND rates.unit = target.unit
                  AND rates.is_active IS TRUE
                FOR UPDATE;

                IF current_rate IS DISTINCT FROM target.expected_rate THEN
                    RAISE EXCEPTION
                        'Unexpected active platform credit rate for %/%: expected %, found %',
                        target.capability,
                        target.unit,
                        target.expected_rate,
                        current_rate;
                END IF;

                UPDATE credit_rates AS rates
                SET credits_per_unit = target.replacement_rate
                WHERE rates.tenant_id IS NULL
                  AND rates.capability = target.capability
                  AND rates.unit = target.unit
                  AND rates.is_active IS TRUE
                  AND rates.credits_per_unit = target.expected_rate;
                GET DIAGNOSTICS affected = ROW_COUNT;
                IF affected <> 1 THEN
                    RAISE EXCEPTION
                        'Expected one active platform credit rate for %/%, updated %',
                        target.capability,
                        target.unit,
                        affected;
                END IF;
            END LOOP;
        END
        $credit_rate_resolution_reprice$;
        """
    )
