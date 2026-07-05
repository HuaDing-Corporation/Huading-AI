"""Scale Basic plan quota for repriced credits."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260706_0019"
down_revision = "20260706_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE plans SET quota_credits = 10000000 WHERE code = 'basic'"))
    op.execute(
        sa.text("UPDATE subscriptions SET quota_credits_total = 10000000 WHERE status = 'active'")
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE plans SET quota_credits = 1000 WHERE code = 'basic'"))
    op.execute(
        sa.text("UPDATE subscriptions SET quota_credits_total = 1000 WHERE status = 'active'")
    )
