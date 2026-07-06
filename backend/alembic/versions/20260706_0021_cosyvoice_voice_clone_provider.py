"""Add CosyVoice voice clone provider config."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "20260706_0021"
down_revision = "20260706_0020"
branch_labels = None
depends_on = None

_COSYVOICE_PROVIDER_ID = "00000000-0000-0000-0000-000000000420"
_VOICE_CLONE_RATE_ID = "00000000-0000-0000-0000-000000000408"
_DOUBAO_VOICE_CLONE_CREDITS = Decimal("30000.0000")
_OLD_DOUBAO_VOICE_CLONE_CREDITS = Decimal("30.0000")
_OLD_TENANT_INDEX = "uq_provider_configs_tenant_capability"
_OLD_PLATFORM_INDEX = "uq_provider_configs_platform_capability"
_NEW_TENANT_SINGLE_INDEX = "uq_provider_configs_tenant_capability_non_voice_clone"
_NEW_PLATFORM_SINGLE_INDEX = "uq_provider_configs_platform_capability_non_voice_clone"
_NEW_TENANT_INDEX = "uq_provider_configs_tenant_capability_provider"
_NEW_PLATFORM_INDEX = "uq_provider_configs_platform_capability_provider"


def _index_exists(table_name: str, index_name: str) -> bool:
    indexes = sa.inspect(op.get_bind()).get_indexes(table_name)
    return any(index["name"] == index_name for index in indexes)


def _drop_index_if_exists(index_name: str) -> None:
    if _index_exists("provider_configs", index_name):
        op.drop_index(index_name, table_name="provider_configs")


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _create_provider_indexes() -> None:
    if not _index_exists("provider_configs", _NEW_TENANT_SINGLE_INDEX):
        op.create_index(
            _NEW_TENANT_SINGLE_INDEX,
            "provider_configs",
            ["tenant_id", "capability"],
            unique=True,
            postgresql_where=sa.text(
                "tenant_id IS NOT NULL AND capability != 'voice_clone'"
            ),
            sqlite_where=sa.text(
                "tenant_id IS NOT NULL AND capability != 'voice_clone'"
            ),
        )
    if not _index_exists("provider_configs", _NEW_TENANT_INDEX):
        op.create_index(
            _NEW_TENANT_INDEX,
            "provider_configs",
            ["tenant_id", "capability", "provider"],
            unique=True,
            postgresql_where=sa.text("tenant_id IS NOT NULL"),
            sqlite_where=sa.text("tenant_id IS NOT NULL"),
        )
    if not _index_exists("provider_configs", _NEW_PLATFORM_SINGLE_INDEX):
        op.create_index(
            _NEW_PLATFORM_SINGLE_INDEX,
            "provider_configs",
            ["capability"],
            unique=True,
            postgresql_where=sa.text("tenant_id IS NULL AND capability != 'voice_clone'"),
            sqlite_where=sa.text("tenant_id IS NULL AND capability != 'voice_clone'"),
        )
    if not _index_exists("provider_configs", _NEW_PLATFORM_INDEX):
        op.create_index(
            _NEW_PLATFORM_INDEX,
            "provider_configs",
            ["capability", "provider"],
            unique=True,
            postgresql_where=sa.text("tenant_id IS NULL"),
            sqlite_where=sa.text("tenant_id IS NULL"),
        )


def _insert_cosyvoice_provider_config() -> None:
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM provider_configs "
            "WHERE tenant_id IS NULL AND capability = 'voice_clone' "
            "AND provider = 'cosyvoice-voice-clone' LIMIT 1"
        )
    )
    if row.first() is not None:
        return
    provider_configs = sa.table(
        "provider_configs",
        sa.column("id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("capability", sa.String),
        sa.column("provider", sa.String),
        sa.column("config", sa.JSON),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        provider_configs,
        [
            {
                "id": _COSYVOICE_PROVIDER_ID,
                "tenant_id": None,
                "capability": "voice_clone",
                "provider": "cosyvoice-voice-clone",
                "config": {},
                "is_active": True,
            }
        ],
    )


def _credit_rates_table() -> sa.Table:
    return sa.table(
        "credit_rates",
        sa.column("id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("capability", sa.String),
        sa.column("unit", sa.String),
        sa.column("credits_per_unit", sa.Numeric),
        sa.column("is_active", sa.Boolean),
        sa.column("effective_at", sa.DateTime(timezone=True)),
    )


def _upsert_doubao_voice_clone_rate() -> None:
    if not _table_exists("credit_rates"):
        return
    credit_rates = _credit_rates_table()
    bind = op.get_bind()
    row = bind.execute(
        sa.select(credit_rates.c.id).where(
            credit_rates.c.tenant_id.is_(None),
            credit_rates.c.capability == "voice_clone",
            credit_rates.c.unit == "call",
            credit_rates.c.is_active.is_(True),
        )
    ).first()
    if row is None:
        op.bulk_insert(
            credit_rates,
            [
                {
                    "id": _VOICE_CLONE_RATE_ID,
                    "tenant_id": None,
                    "capability": "voice_clone",
                    "unit": "call",
                    "credits_per_unit": _DOUBAO_VOICE_CLONE_CREDITS,
                    "is_active": True,
                    "effective_at": datetime.now(UTC),
                }
            ],
        )
        return
    bind.execute(
        credit_rates.update()
        .where(credit_rates.c.id == row.id)
        .values(credits_per_unit=_DOUBAO_VOICE_CLONE_CREDITS)
    )


def _downgrade_doubao_voice_clone_rate() -> None:
    if not _table_exists("credit_rates"):
        return
    credit_rates = _credit_rates_table()
    bind = op.get_bind()
    bind.execute(
        credit_rates.update()
        .where(
            credit_rates.c.tenant_id.is_(None),
            credit_rates.c.capability == "voice_clone",
            credit_rates.c.unit == "call",
            credit_rates.c.is_active.is_(True),
            credit_rates.c.credits_per_unit == _DOUBAO_VOICE_CLONE_CREDITS,
        )
        .values(credits_per_unit=_OLD_DOUBAO_VOICE_CLONE_CREDITS)
    )


def upgrade() -> None:
    _drop_index_if_exists(_OLD_TENANT_INDEX)
    _drop_index_if_exists(_OLD_PLATFORM_INDEX)
    _create_provider_indexes()
    _insert_cosyvoice_provider_config()
    _upsert_doubao_voice_clone_rate()


def downgrade() -> None:
    _downgrade_doubao_voice_clone_rate()
    op.execute(
        sa.text("DELETE FROM provider_configs WHERE id = :provider_id").bindparams(
            provider_id=_COSYVOICE_PROVIDER_ID
        )
    )
    _drop_index_if_exists(_NEW_TENANT_INDEX)
    _drop_index_if_exists(_NEW_PLATFORM_INDEX)
    _drop_index_if_exists(_NEW_TENANT_SINGLE_INDEX)
    _drop_index_if_exists(_NEW_PLATFORM_SINGLE_INDEX)
    if not _index_exists("provider_configs", _OLD_TENANT_INDEX):
        op.create_index(
            _OLD_TENANT_INDEX,
            "provider_configs",
            ["tenant_id", "capability"],
            unique=True,
            postgresql_where=sa.text("tenant_id IS NOT NULL"),
            sqlite_where=sa.text("tenant_id IS NOT NULL"),
        )
    if not _index_exists("provider_configs", _OLD_PLATFORM_INDEX):
        op.create_index(
            _OLD_PLATFORM_INDEX,
            "provider_configs",
            ["capability"],
            unique=True,
            postgresql_where=sa.text("tenant_id IS NULL"),
            sqlite_where=sa.text("tenant_id IS NULL"),
        )
