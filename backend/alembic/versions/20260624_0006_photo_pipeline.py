"""Add photo pipeline asset roles and OpenAI image provider."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260624_0006"
down_revision = "20260621_0005"
branch_labels = None
depends_on = None

_PROVIDER_ID = "00000000-0000-0000-0000-000000000406"


def upgrade() -> None:
    op.drop_constraint("ck_assets_type", "assets", type_="check")
    op.create_check_constraint(
        "ck_assets_type",
        "assets",
        "type IN ('avatar_image', 'audio', 'subtitle', 'video', 'bgm', 'cover', "
        "'product_image', 'generated_image')",
    )
    op.drop_constraint("ck_task_assets_role", "task_assets", type_="check")
    op.create_check_constraint(
        "ck_task_assets_role",
        "task_assets",
        "role IN ('input_avatar', 'output_audio', 'output_subtitle', 'output_video', "
        "'output_image')",
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
                "capability": "image",
                "provider": "openai",
                "config": {},
                "is_active": True,
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM provider_configs WHERE id = :provider_id").bindparams(
            provider_id=_PROVIDER_ID
        )
    )
    op.drop_constraint("ck_task_assets_role", "task_assets", type_="check")
    op.create_check_constraint(
        "ck_task_assets_role",
        "task_assets",
        "role IN ('input_avatar', 'output_audio', 'output_subtitle', 'output_video')",
    )
    op.drop_constraint("ck_assets_type", "assets", type_="check")
    op.create_check_constraint(
        "ck_assets_type",
        "assets",
        "type IN ('avatar_image', 'audio', 'subtitle', 'video', 'bgm', 'cover', "
        "'product_image')",
    )
