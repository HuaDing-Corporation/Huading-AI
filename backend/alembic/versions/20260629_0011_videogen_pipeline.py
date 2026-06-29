"""Add video generation pipeline support."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "20260629_0011"
down_revision = "20260629_0010"
branch_labels = None
depends_on = None

_BGM_TABLE = "bgm_library_tracks"
_BGM_ACTIVE_INDEX = "ix_bgm_library_tracks_active"
_VIDEO_GEN_RATE_ID = "00000000-0000-0000-0000-000000000411"
_VIDEO_GEN_PROVIDER_ID = "00000000-0000-0000-0000-000000000412"
_CAPABILITY_CHECK = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen')"
)
_LEGACY_CAPABILITY_CHECK = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone')"
)
_TASK_ASSET_ROLE_CHECK = (
    "role IN ('input_avatar', 'output_audio', 'output_subtitle', 'output_video', "
    "'output_image', 'input_reference_image', 'input_bgm')"
)
_LEGACY_TASK_ASSET_ROLE_CHECK = (
    "role IN ('input_avatar', 'output_audio', 'output_subtitle', 'output_video', "
    "'output_image')"
)


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    indexes = sa.inspect(op.get_bind()).get_indexes(table_name)
    return any(index["name"] == index_name for index in indexes)


def _check_exists(table_name: str, check_name: str) -> bool:
    checks = sa.inspect(op.get_bind()).get_check_constraints(table_name)
    return any(check["name"] == check_name for check in checks)


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


def _credit_rate_exists(capability: str, unit: str) -> bool:
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM credit_rates "
            "WHERE tenant_id IS NULL AND capability = :capability AND unit = :unit"
        ).bindparams(capability=capability, unit=unit)
    ).first()
    return row is not None


def _provider_config_exists(capability: str) -> bool:
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM provider_configs "
            "WHERE tenant_id IS NULL AND capability = :capability"
        ).bindparams(capability=capability)
    ).first()
    return row is not None


def _insert_bgm_tracks() -> None:
    tracks = sa.table(
        _BGM_TABLE,
        sa.column("track_id", sa.String),
        sa.column("name", sa.String),
        sa.column("duration_sec", sa.Integer),
        sa.column("storage_key", sa.String),
        sa.column("preview_storage_key", sa.String),
        sa.column("license", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        tracks,
        [
            {
                "track_id": "ambient-soft-loop",
                "name": "Ambient Soft Loop",
                "duration_sec": 30,
                "storage_key": "library/bgm/ambient-soft-loop.mp3",
                "preview_storage_key": "library/bgm/ambient-soft-loop.mp3",
                "license": "Royalty-free CC0 placeholder; replace media before production.",
                "is_active": True,
                "created_at": now,
            },
            {
                "track_id": "bright-product-pop",
                "name": "Bright Product Pop",
                "duration_sec": 30,
                "storage_key": "library/bgm/bright-product-pop.mp3",
                "preview_storage_key": "library/bgm/bright-product-pop.mp3",
                "license": "Royalty-free CC0 placeholder; replace media before production.",
                "is_active": True,
                "created_at": now,
            },
            {
                "track_id": "calm-tech-pulse",
                "name": "Calm Tech Pulse",
                "duration_sec": 30,
                "storage_key": "library/bgm/calm-tech-pulse.mp3",
                "preview_storage_key": "library/bgm/calm-tech-pulse.mp3",
                "license": "Royalty-free CC0 placeholder; replace media before production.",
                "is_active": True,
                "created_at": now,
            },
        ],
    )


def _insert_video_gen_rate() -> None:
    if _credit_rate_exists("video_gen", "second"):
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
                "id": _VIDEO_GEN_RATE_ID,
                "tenant_id": None,
                "capability": "video_gen",
                "unit": "second",
                "credits_per_unit": Decimal("2.0000"),
                "is_active": True,
                "effective_at": datetime.now(UTC),
            }
        ],
    )


def _insert_video_provider_config() -> None:
    if _provider_config_exists("video"):
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
                "id": _VIDEO_GEN_PROVIDER_ID,
                "tenant_id": None,
                "capability": "video",
                "provider": "seedance-mini",
                "config": {},
                "is_active": True,
            }
        ],
    )


def upgrade() -> None:
    _replace_check("credit_rates", "ck_credit_rates_capability", _CAPABILITY_CHECK)
    _replace_check("usage_records", "ck_usage_records_capability", _CAPABILITY_CHECK)
    _replace_check("task_assets", "ck_task_assets_role", _TASK_ASSET_ROLE_CHECK)

    if not _table_exists(_BGM_TABLE):
        op.create_table(
            _BGM_TABLE,
            sa.Column("track_id", sa.String(length=80), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("duration_sec", sa.Integer(), nullable=False),
            sa.Column("storage_key", sa.String(length=400), nullable=False),
            sa.Column("preview_storage_key", sa.String(length=400), nullable=True),
            sa.Column("license", sa.String(length=160), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.PrimaryKeyConstraint("track_id"),
        )
        _insert_bgm_tracks()

    if _table_exists(_BGM_TABLE) and not _index_exists(_BGM_TABLE, _BGM_ACTIVE_INDEX):
        op.create_index(_BGM_ACTIVE_INDEX, _BGM_TABLE, ["is_active", "track_id"])

    _insert_video_gen_rate()
    _insert_video_provider_config()


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM credit_rates WHERE id = :rate_id").bindparams(
            rate_id=_VIDEO_GEN_RATE_ID
        )
    )
    op.get_bind().execute(
        sa.text("DELETE FROM provider_configs WHERE id = :provider_id").bindparams(
            provider_id=_VIDEO_GEN_PROVIDER_ID
        )
    )
    if _table_exists(_BGM_TABLE):
        if _index_exists(_BGM_TABLE, _BGM_ACTIVE_INDEX):
            op.drop_index(_BGM_ACTIVE_INDEX, table_name=_BGM_TABLE)
        op.drop_table(_BGM_TABLE)
    _replace_check("task_assets", "ck_task_assets_role", _LEGACY_TASK_ASSET_ROLE_CHECK)
    _replace_check("usage_records", "ck_usage_records_capability", _LEGACY_CAPABILITY_CHECK)
    _replace_check("credit_rates", "ck_credit_rates_capability", _LEGACY_CAPABILITY_CHECK)
