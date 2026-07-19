"""Add the non-streaming AIBRAIN wallet, chat, and billing schema."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260719_0029"
down_revision = "20260719_0028"
branch_labels = None
depends_on = None

_PROVIDER_CAPABILITY_WITH_CHAT = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'reverse_prompt', 'scene_prompt', 'chat')"
)
_PROVIDER_CAPABILITY_LEGACY = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'reverse_prompt', 'scene_prompt')"
)
_USAGE_CAPABILITY_WITH_CHAT = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt', "
    "'reverse_prompt_video', 'scene_prompt', 'chat')"
)
_USAGE_CAPABILITY_LEGACY = (
    "capability IN ('llm', 'tts', 'avatar', 'video', 'image', 'asr', "
    "'publish', 'voice_clone', 'video_gen', 'reverse_prompt', "
    "'reverse_prompt_video', 'scene_prompt')"
)
_PROVIDER_ID = "chat-apimart-gpt56"
_JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "reasoning_wallets",
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "available_credits",
            sa.Numeric(18, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "reserved_credits",
            sa.Numeric(18, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_topup_credits",
            sa.Numeric(18, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_spent_credits",
            sa.Numeric(18, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "available_credits >= 0",
            name="ck_reasoning_wallets_available_nonnegative",
        ),
        sa.CheckConstraint(
            "reserved_credits >= 0",
            name="ck_reasoning_wallets_reserved_nonnegative",
        ),
        sa.CheckConstraint(
            "total_topup_credits >= 0 AND total_spent_credits >= 0",
            name="ck_reasoning_wallets_totals_nonnegative",
        ),
    )

    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_chat_conversations_tenant_id",
        "chat_conversations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_chat_conversations_tenant_updated",
        "chat_conversations",
        ["tenant_id", "updated_at"],
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "attachments",
            _JSON_TYPE,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("tier", sa.String(length=16), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("output_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("reserved_credits", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("charged_credits", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("provider_cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
        sa.CheckConstraint(
            "tier IS NULL OR tier IN ('low', 'mid', 'high')",
            name="ck_chat_messages_tier",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_chat_messages_status",
        ),
    )
    op.create_index(
        "ix_chat_messages_tenant_id",
        "chat_messages",
        ["tenant_id"],
    )
    op.create_index(
        "ix_chat_messages_conversation_created",
        "chat_messages",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_chat_messages_tenant_created",
        "chat_messages",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "reasoning_ledger_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("reasoning_wallets.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entry_type", sa.String(length=16), nullable=False),
        sa.Column("amount_credits", sa.Numeric(18, 6), nullable=False),
        sa.Column("available_delta", sa.Numeric(18, 6), nullable=False),
        sa.Column("reserved_delta", sa.Numeric(18, 6), nullable=False),
        sa.Column("available_after", sa.Numeric(18, 6), nullable=False),
        sa.Column("reserved_after", sa.Numeric(18, 6), nullable=False),
        sa.Column(
            "subscription_id",
            sa.String(length=36),
            sa.ForeignKey("subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "chat_message_id",
            sa.String(length=36),
            sa.ForeignKey("chat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("operation_key", sa.String(length=100), nullable=True, unique=True),
        sa.Column("details", _JSON_TYPE, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "entry_type IN ('topup', 'reserve', 'settle', 'release')",
            name="ck_reasoning_ledger_entries_type",
        ),
    )
    op.create_index(
        "ix_reasoning_ledger_tenant_created",
        "reasoning_ledger_entries",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_reasoning_ledger_message",
        "reasoning_ledger_entries",
        ["chat_message_id"],
    )

    op.execute(
        sa.text(
            "INSERT INTO reasoning_wallets (tenant_id) "
            "SELECT id FROM tenants "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM reasoning_wallets rw WHERE rw.tenant_id = tenants.id"
            ")"
        )
    )

    with op.batch_alter_table("provider_configs") as batch_op:
        batch_op.drop_constraint("ck_provider_configs_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_provider_configs_capability",
            _PROVIDER_CAPABILITY_WITH_CHAT,
        )
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("ck_usage_records_capability", type_="check")
        batch_op.create_check_constraint(
            "ck_usage_records_capability",
            _USAGE_CAPABILITY_WITH_CHAT,
        )
        batch_op.add_column(sa.Column("chat_message_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("provider_cost_usd", sa.Numeric(18, 8), nullable=True))
        batch_op.alter_column(
            "credits",
            existing_type=sa.Numeric(12, 2),
            type_=sa.Numeric(18, 6),
            existing_nullable=False,
        )
        batch_op.create_foreign_key(
            "fk_usage_records_chat_message_id",
            "chat_messages",
            ["chat_message_id"],
            ["id"],
            ondelete="SET NULL",
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
                "capability": "chat",
                "provider": "apimart-gpt56",
                "config": {},
                "is_active": True,
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM usage_records WHERE capability = :capability").bindparams(
            capability="chat"
        )
    )
    op.execute(
        sa.text("DELETE FROM provider_configs WHERE capability = :capability").bindparams(
            capability="chat"
        )
    )

    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("fk_usage_records_chat_message_id", type_="foreignkey")
        batch_op.drop_column("provider_cost_usd")
        batch_op.drop_column("chat_message_id")
        batch_op.alter_column(
            "credits",
            existing_type=sa.Numeric(18, 6),
            type_=sa.Numeric(12, 2),
            existing_nullable=False,
        )
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

    op.drop_index("ix_reasoning_ledger_message", table_name="reasoning_ledger_entries")
    op.drop_index("ix_reasoning_ledger_tenant_created", table_name="reasoning_ledger_entries")
    op.drop_table("reasoning_ledger_entries")
    op.drop_index("ix_chat_messages_tenant_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_conversation_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_tenant_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_conversations_tenant_updated", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_tenant_id", table_name="chat_conversations")
    op.drop_table("chat_conversations")
    op.drop_table("reasoning_wallets")
