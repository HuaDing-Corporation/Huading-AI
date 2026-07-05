"""Reprice platform default credit rates."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260706_0018"
down_revision = "20260706_0017"
branch_labels = None
depends_on = None

_TABLE = "credit_rates"
_UNIT_CONSTRAINT = "ck_credit_rates_unit"
_UNIT_WITH_CHARACTER = "unit IN ('second', 'call', 'token', 'image', 'character')"
_UNIT_LEGACY = "unit IN ('second', 'call', 'token', 'image')"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_constraint(_UNIT_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_UNIT_CONSTRAINT, _UNIT_WITH_CHARACTER)

    _update_platform_rate("avatar", "second", "150.0000")
    op.execute(
        sa.text(
            """
            UPDATE credit_rates
            SET unit = 'character', credits_per_unit = 0.1000
            WHERE tenant_id IS NULL
              AND capability = 'tts'
              AND unit IN ('second', 'character')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE credit_rates AS legacy
            SET unit = 'character'
            WHERE legacy.tenant_id IS NOT NULL
              AND legacy.capability = 'tts'
              AND legacy.unit = 'second'
              AND NOT EXISTS (
                  SELECT 1
                  FROM credit_rates AS existing
                  WHERE existing.tenant_id = legacy.tenant_id
                    AND existing.capability = 'tts'
                    AND existing.unit = 'character'
                    AND existing.is_active IS TRUE
              )
            """
        )
    )
    _update_platform_rate("video", "second", "80.0000")
    _update_platform_rate("video_gen", "second", "80.0000")
    _update_platform_rate("image", "image", "10.0000")


def downgrade() -> None:
    _update_platform_rate("avatar", "second", "1.0000")
    op.execute(
        sa.text(
            """
            UPDATE credit_rates
            SET unit = 'second', credits_per_unit = 0.2000
            WHERE tenant_id IS NULL
              AND capability = 'tts'
              AND unit IN ('second', 'character')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE credit_rates AS migrated
            SET unit = 'second'
            WHERE migrated.tenant_id IS NOT NULL
              AND migrated.capability = 'tts'
              AND migrated.unit = 'character'
              AND NOT EXISTS (
                  SELECT 1
                  FROM credit_rates AS existing
                  WHERE existing.tenant_id = migrated.tenant_id
                    AND existing.capability = 'tts'
                    AND existing.unit = 'second'
                    AND existing.is_active IS TRUE
              )
            """
        )
    )
    _update_platform_rate("video", "second", "2.0000")
    _update_platform_rate("video_gen", "second", "2.0000")
    _update_platform_rate("image", "image", "5.0000")

    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_constraint(_UNIT_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_UNIT_CONSTRAINT, _UNIT_LEGACY)


def _update_platform_rate(capability: str, unit: str, credits_per_unit: str) -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE credit_rates
            SET credits_per_unit = {credits_per_unit}
            WHERE tenant_id IS NULL
              AND capability = '{capability}'
              AND unit = '{unit}'
            """
        )
    )
