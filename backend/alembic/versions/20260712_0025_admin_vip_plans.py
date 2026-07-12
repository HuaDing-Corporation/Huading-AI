"""Seed free and Huading plans for registration and analytics access."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_0025"
down_revision: str | None = "20260710_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_plans = sa.table(
    "plans",
    sa.column("id", sa.String),
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("price_cents", sa.Integer),
    sa.column("period", sa.String),
    sa.column("quota_credits", sa.Integer),
    sa.column("max_concurrent", sa.SmallInteger),
    sa.column("seat_limit", sa.SmallInteger),
    sa.column("features", sa.JSON),
    sa.column("is_active", sa.Boolean),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
_PLAN_ROWS: tuple[Mapping[str, object], ...] = (
    {
        "code": "free",
        "name": "免费版",
        "price_cents": 0,
        "period": "monthly",
        "quota_credits": 0,
        "is_active": True,
    },
    {
        "code": "huading",
        "name": "Huading Plan",
        "price_cents": 0,
        "period": "monthly",
        "quota_credits": 0,
        "is_active": True,
    },
)


def _upsert_plan(bind: sa.Connection, row: Mapping[str, object]) -> None:
    code = str(row["code"])
    plan_id = bind.scalar(sa.select(_plans.c.id).where(_plans.c.code == code))
    if plan_id is not None:
        bind.execute(sa.update(_plans).where(_plans.c.code == code).values(**row))
        return
    bind.execute(
        sa.insert(_plans).values(
            id=str(uuid4()),
            max_concurrent=1,
            seat_limit=1,
            features={},
            created_at=datetime.now(UTC),
            **row,
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    for row in _PLAN_ROWS:
        _upsert_plan(bind, row)


def downgrade() -> None:
    # Upgrade may update plans that existed before this migration. Their ownership
    # cannot be distinguished safely, so downgrade preserves catalog data.
    pass
