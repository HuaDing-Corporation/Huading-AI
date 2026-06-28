"""Add brand voice cloning support."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "20260628_0008"
down_revision = "20260625_0007"
branch_labels = None
depends_on = None

_BRAND_VOICE_PROVIDER_ID = "00000000-0000-0000-0000-000000000407"
_VOICE_CLONE_RATE_ID = "00000000-0000-0000-0000-000000000408"
_BRAND_VOICE_FK = "fk_video_tasks_brand_voice_id_brand_voices"
_CAPABILITY_CHECK = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone')"
)
_LEGACY_CAPABILITY_CHECK = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', 'publish')"
)


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def _check_exists(table_name: str, check_name: str) -> bool:
    checks = sa.inspect(op.get_bind()).get_check_constraints(table_name)
    return any(check["name"] == check_name for check in checks)


def _fk_exists(table_name: str, fk_name: str) -> bool:
    fks = sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    return any(fk["name"] == fk_name for fk in fks)


def _replace_check(table_name: str, check_name: str, condition: str) -> None:
    dialect = op.get_bind().dialect.name
    check_exists = _check_exists(table_name, check_name)
    if dialect == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            if check_exists:
                batch.drop_constraint(check_name, type_="check")
            batch.create_check_constraint(check_name, condition)
        return

    if check_exists:
        op.drop_constraint(check_name, table_name, type_="check")
    op.create_check_constraint(check_name, table_name, condition)


def _insert_platform_provider_config() -> None:
    if _provider_config_exists("voice_clone"):
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
                "id": _BRAND_VOICE_PROVIDER_ID,
                "tenant_id": None,
                "capability": "voice_clone",
                "provider": "doubao-voice-clone",
                "config": {},
                "is_active": True,
            }
        ],
    )


def _insert_voice_clone_rate() -> None:
    if _credit_rate_exists("voice_clone", "call"):
        return
    credit_rates = sa.table(
        "credit_rates",
        sa.column("id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("capability", sa.String),
        sa.column("unit", sa.String),
        sa.column("credits_per_unit", sa.Numeric),
        sa.column("is_active", sa.Boolean),
        sa.column("effective_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        credit_rates,
        [
            {
                "id": _VOICE_CLONE_RATE_ID,
                "tenant_id": None,
                "capability": "voice_clone",
                "unit": "call",
                "credits_per_unit": Decimal("30.0000"),
                "is_active": True,
                "effective_at": datetime.now(UTC),
            }
        ],
    )


def _provider_config_exists(capability: str) -> bool:
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM provider_configs "
            "WHERE tenant_id IS NULL AND capability = :capability LIMIT 1"
        ),
        {"capability": capability},
    )
    return row.first() is not None


def _credit_rate_exists(capability: str, unit: str) -> bool:
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM credit_rates "
            "WHERE tenant_id IS NULL AND capability = :capability AND unit = :unit "
            "AND is_active = true LIMIT 1"
        ),
        {"capability": capability, "unit": unit},
    )
    return row.first() is not None


def upgrade() -> None:
    _replace_check("credit_rates", "ck_credit_rates_capability", _CAPABILITY_CHECK)
    _replace_check("provider_configs", "ck_provider_configs_capability", _CAPABILITY_CHECK)
    _replace_check("usage_records", "ck_usage_records_capability", _CAPABILITY_CHECK)

    if not _table_exists("brand_voices"):
        op.create_table(
            "brand_voices",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=30), nullable=False),
            sa.Column("source_audio_asset_id", sa.String(length=36), nullable=True),
            sa.Column(
                "provider",
                sa.String(length=40),
                nullable=False,
                server_default="doubao-voice-clone",
            ),
            sa.Column("speaker_id", sa.String(length=160), nullable=True),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="processing",
            ),
            sa.Column(
                "consent_confirmed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("consent_confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_code", sa.String(length=40), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "status IN ('processing', 'ready', 'failed')",
                name="ck_brand_voices_status",
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["source_audio_asset_id"], ["assets.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_brand_voices_tenant_status",
            "brand_voices",
            ["tenant_id", "status"],
        )
        op.create_index(
            "ix_brand_voices_tenant_created_at",
            "brand_voices",
            ["tenant_id", "created_at"],
        )

    if not _column_exists("video_tasks", "brand_voice_id"):
        op.add_column(
            "video_tasks",
            sa.Column("brand_voice_id", sa.String(length=36), nullable=True),
        )
    if not _fk_exists("video_tasks", _BRAND_VOICE_FK):
        op.create_foreign_key(
            _BRAND_VOICE_FK,
            "video_tasks",
            "brand_voices",
            ["brand_voice_id"],
            ["id"],
            ondelete="SET NULL",
        )

    _insert_platform_provider_config()
    _insert_voice_clone_rate()


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM provider_configs WHERE id = :provider_id").bindparams(
            provider_id=_BRAND_VOICE_PROVIDER_ID
        )
    )
    op.execute(
        sa.text("DELETE FROM credit_rates WHERE id = :rate_id").bindparams(
            rate_id=_VOICE_CLONE_RATE_ID
        )
    )
    if _fk_exists("video_tasks", _BRAND_VOICE_FK):
        op.drop_constraint(_BRAND_VOICE_FK, "video_tasks", type_="foreignkey")
    if _column_exists("video_tasks", "brand_voice_id"):
        op.drop_column("video_tasks", "brand_voice_id")

    if _table_exists("brand_voices"):
        op.drop_index("ix_brand_voices_tenant_created_at", table_name="brand_voices")
        op.drop_index("ix_brand_voices_tenant_status", table_name="brand_voices")
        op.drop_table("brand_voices")

    _replace_check("usage_records", "ck_usage_records_capability", _LEGACY_CAPABILITY_CHECK)
    _replace_check("provider_configs", "ck_provider_configs_capability", _LEGACY_CAPABILITY_CHECK)
    _replace_check("credit_rates", "ck_credit_rates_capability", _LEGACY_CAPABILITY_CHECK)
