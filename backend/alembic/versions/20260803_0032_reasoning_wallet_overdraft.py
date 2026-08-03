"""Allow temporary overdrafts in AIBRAIN reasoning wallets."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260803_0032"
down_revision = "20260724_0031"
branch_labels = None
depends_on = None

_AVAILABLE_NONNEGATIVE_CONSTRAINT = (
    "ck_reasoning_wallets_available_nonnegative"
)


def upgrade() -> None:
    with op.batch_alter_table("reasoning_wallets") as batch_op:
        batch_op.drop_constraint(
            _AVAILABLE_NONNEGATIVE_CONSTRAINT,
            type_="check",
        )


def downgrade() -> None:
    negative_wallet_count = int(
        op.get_bind().scalar(
            sa.text(
                "SELECT COUNT(*) FROM reasoning_wallets "
                "WHERE available_credits < 0"
            )
        )
        or 0
    )
    if negative_wallet_count:
        wallet_verb = (
            "wallet has" if negative_wallet_count == 1 else "wallets have"
        )
        raise RuntimeError(
            "Cannot downgrade 20260803_0032: "
            f"{negative_wallet_count} reasoning {wallet_verb} a negative "
            "available_credits balance. Top up or settle every outstanding "
            "balance before retrying. Balances were not modified."
        )

    with op.batch_alter_table("reasoning_wallets") as batch_op:
        batch_op.create_check_constraint(
            _AVAILABLE_NONNEGATIVE_CONSTRAINT,
            "available_credits >= 0",
        )
