"""Switch the default video provider to APIMart."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260702_0013"
down_revision = "20260701_0012"
branch_labels = None
depends_on = None

_VIDEO_PROVIDER_ID = "00000000-0000-0000-0000-000000000414"


def _platform_video_provider_exists() -> bool:
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM provider_configs "
            "WHERE tenant_id IS NULL AND capability = 'video'"
        )
    ).first()
    return row is not None


def _insert_platform_video_provider(provider: str) -> None:
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
                "id": _VIDEO_PROVIDER_ID,
                "tenant_id": None,
                "capability": "video",
                "provider": provider,
                "config": {},
                "is_active": True,
            }
        ],
    )


def upgrade() -> None:
    if _platform_video_provider_exists():
        op.execute(
            "UPDATE provider_configs SET provider='apimart', config='{}' "
            "WHERE tenant_id IS NULL AND capability='video'"
        )
        return
    _insert_platform_video_provider("apimart")


def downgrade() -> None:
    if _platform_video_provider_exists():
        op.execute(
            "UPDATE provider_configs SET provider='seedance-mini', config='{}' "
            "WHERE tenant_id IS NULL AND capability='video' AND provider='apimart'"
        )
        return
    _insert_platform_video_provider("seedance-mini")
