"""Apply the A1 reverse-prompt and avatar credit rates."""

from __future__ import annotations

from decimal import Decimal

from alembic import op

revision = "20260806_0035"
down_revision = "20260805_0034"
branch_labels = None
depends_on = None

_UPGRADE_RATES = (
    ("reverse_prompt", "call", Decimal("30.0000"), Decimal("100.0000")),
    ("avatar", "second", Decimal("150.0000"), Decimal("180.0000")),
)
_DOWNGRADE_RATES = (
    ("reverse_prompt", "call", Decimal("100.0000"), Decimal("30.0000")),
    ("avatar", "second", Decimal("180.0000"), Decimal("150.0000")),
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
        DO $pricing_a1_rates$
        DECLARE
            target RECORD;
            current_rate NUMERIC(12, 4);
            affected INTEGER;
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM credit_rates AS rates
                JOIN (VALUES
{values_sql}
                ) AS targets(capability, unit, expected_rate, replacement_rate)
                  ON targets.capability = rates.capability
                 AND targets.unit = rates.unit
                WHERE rates.tenant_id IS NOT NULL
                  AND rates.is_active IS TRUE
            ) THEN
                RAISE EXCEPTION
                    'A1 credit-rate migration requires zero active tenant overrides '
                    'for reverse_prompt/call and avatar/second';
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

                IF current_rate IS DISTINCT FROM target.expected_rate
                   AND current_rate IS DISTINCT FROM target.replacement_rate THEN
                    RAISE EXCEPTION
                        'Unexpected active platform credit rate for %/%: expected % or %, found %',
                        target.capability,
                        target.unit,
                        target.expected_rate,
                        target.replacement_rate,
                        current_rate;
                END IF;

                UPDATE credit_rates AS rates
                SET credits_per_unit = target.replacement_rate
                WHERE rates.tenant_id IS NULL
                  AND rates.capability = target.capability
                  AND rates.unit = target.unit
                  AND rates.is_active IS TRUE;
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
        $pricing_a1_rates$;
        """
    )
