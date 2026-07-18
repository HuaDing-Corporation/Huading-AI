"""Add the independent APIMart Luna scene-prompt provider capability."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260719_0028"
down_revision = "20260716_0027"
branch_labels = None
depends_on = None

_PROVIDER_CAPABILITY_WITH_SCENE_PROMPT = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'reverse_prompt', 'scene_prompt')"
)
_PROVIDER_CAPABILITY_LEGACY = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'reverse_prompt')"
)
_USAGE_CAPABILITY_WITH_SCENE_PROMPT = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt', "
    "'reverse_prompt_video', 'scene_prompt')"
)
_USAGE_CAPABILITY_LEGACY = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt', "
    "'reverse_prompt_video')"
)
_PROVIDER_ID = "scene-prompt-apimart-luna"


def upgrade() -> None:
    with op.batch_alter_table("provider_configs") as batch_op:
        batch_op.drop_constraint("ck_provider_configs_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_provider_configs_capability",
            _PROVIDER_CAPABILITY_WITH_SCENE_PROMPT,
        )
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("ck_usage_records_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_usage_records_capability",
            _USAGE_CAPABILITY_WITH_SCENE_PROMPT,
        )

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
                "id": _PROVIDER_ID,
                "tenant_id": None,
                "capability": "scene_prompt",
                "provider": "apimart-luna",
                "config": {},
                "is_active": True,
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM usage_records WHERE capability = :capability").bindparams(
            capability="scene_prompt"
        )
    )
    op.execute(
        sa.text("DELETE FROM provider_configs WHERE capability = :capability").bindparams(
            capability="scene_prompt"
        )
    )
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("ck_usage_records_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_usage_records_capability",
            _USAGE_CAPABILITY_LEGACY,
        )
    with op.batch_alter_table("provider_configs") as batch_op:
        batch_op.drop_constraint("ck_provider_configs_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_provider_configs_capability",
            _PROVIDER_CAPABILITY_LEGACY,
        )
