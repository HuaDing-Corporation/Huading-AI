"""Seed Doubao voice clone speaker slot pool."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "20260706_0020"
down_revision = "20260706_0019"
branch_labels = None
depends_on = None

_DEFAULT_SPEAKER_ID = "S_9iMeXN482"


def _provider_configs_table() -> sa.Table:
    return sa.table(
        "provider_configs",
        sa.column("id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("capability", sa.String),
        sa.column("provider", sa.String),
        sa.column("config", sa.JSON),
    )


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def upgrade() -> None:
    if not _table_exists("provider_configs"):
        return
    provider_configs = _provider_configs_table()
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(provider_configs.c.id, provider_configs.c.config).where(
            provider_configs.c.tenant_id.is_(None),
            provider_configs.c.capability == "voice_clone",
            provider_configs.c.provider == "doubao-voice-clone",
        )
    ).mappings()
    for row in rows:
        config = _as_dict(row["config"])
        if "speaker_ids" in config:
            continue
        config["speaker_ids"] = [_DEFAULT_SPEAKER_ID]
        bind.execute(
            provider_configs.update()
            .where(provider_configs.c.id == row["id"])
            .values(config=config)
        )


def downgrade() -> None:
    if not _table_exists("provider_configs"):
        return
    provider_configs = _provider_configs_table()
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(provider_configs.c.id, provider_configs.c.config).where(
            provider_configs.c.tenant_id.is_(None),
            provider_configs.c.capability == "voice_clone",
            provider_configs.c.provider == "doubao-voice-clone",
        )
    ).mappings()
    for row in rows:
        config = _as_dict(row["config"])
        if config.get("speaker_ids") != [_DEFAULT_SPEAKER_ID]:
            continue
        config.pop("speaker_ids", None)
        bind.execute(
            provider_configs.update()
            .where(provider_configs.c.id == row["id"])
            .values(config=config)
        )
