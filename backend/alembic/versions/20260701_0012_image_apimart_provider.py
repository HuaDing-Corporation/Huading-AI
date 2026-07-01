"""Switch the default image provider to APIMart."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260701_0012"
down_revision = "20260629_0011"
branch_labels = None
depends_on = None

_IMAGE_PROVIDER_ID = "00000000-0000-0000-0000-000000000413"


def _platform_image_provider_exists() -> bool:
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM provider_configs "
            "WHERE tenant_id IS NULL AND capability = 'image'"
        )
    ).first()
    return row is not None


def _insert_platform_image_provider(provider: str) -> None:
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
                "id": _IMAGE_PROVIDER_ID,
                "tenant_id": None,
                "capability": "image",
                "provider": provider,
                "config": {},
                "is_active": True,
            }
        ],
    )


def upgrade() -> None:
    if _platform_image_provider_exists():
        op.execute(
            "UPDATE provider_configs SET provider='apimart', config='{}' "
            "WHERE tenant_id IS NULL AND capability='image'"
        )
        return
    _insert_platform_image_provider("apimart")


def downgrade() -> None:
    if _platform_image_provider_exists():
        op.execute(
            "UPDATE provider_configs SET provider='openai', config='{}' "
            "WHERE tenant_id IS NULL AND capability='image' AND provider='apimart'"
        )
        return
    _insert_platform_image_provider("openai")
