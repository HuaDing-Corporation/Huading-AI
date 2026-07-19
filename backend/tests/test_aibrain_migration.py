import importlib.util
from pathlib import Path


class _BatchOp:
    def __init__(self, owner: "_MigrationOp", table_name: str) -> None:
        self.owner = owner
        self.table_name = table_name

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def drop_constraint(self, name: str, *, type_: str) -> None:
        self.owner.dropped_constraints.append((self.table_name, name, type_))

    def create_check_constraint(self, name: str, condition: str) -> None:
        self.owner.created_checks.append((self.table_name, name, condition))

    def add_column(self, column) -> None:
        self.owner.added_columns.append((self.table_name, column.name))

    def drop_column(self, name: str) -> None:
        self.owner.dropped_columns.append((self.table_name, name))

    def alter_column(self, name: str, **_kwargs) -> None:
        self.owner.altered_columns.append((self.table_name, name))

    def create_foreign_key(self, name: str, *_args, **_kwargs) -> None:
        self.owner.created_foreign_keys.append((self.table_name, name))

    def drop_constraint_named(self, name: str, *, type_: str) -> None:
        self.drop_constraint(name, type_=type_)


class _MigrationOp:
    def __init__(self) -> None:
        self.created_tables: list[str] = []
        self.dropped_tables: list[str] = []
        self.created_indexes: list[tuple[str, str]] = []
        self.dropped_indexes: list[tuple[str, str | None]] = []
        self.created_checks: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str, str]] = []
        self.added_columns: list[tuple[str, str]] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self.created_foreign_keys: list[tuple[str, str]] = []
        self.altered_columns: list[tuple[str, str]] = []
        self.inserted: list[tuple[str, list[dict]]] = []
        self.executed: list[object] = []

    def batch_alter_table(self, table_name: str):
        return _BatchOp(self, table_name)

    def create_table(self, table_name: str, *_args, **_kwargs) -> None:
        self.created_tables.append(table_name)

    def drop_table(self, table_name: str) -> None:
        self.dropped_tables.append(table_name)

    def create_index(self, name: str, table_name: str, *_args, **_kwargs) -> None:
        self.created_indexes.append((table_name, name))

    def drop_index(self, name: str, *, table_name: str | None = None) -> None:
        self.dropped_indexes.append((name, table_name))

    def bulk_insert(self, table, rows: list[dict]) -> None:
        self.inserted.append((table.name, rows))

    def execute(self, statement) -> None:
        self.executed.append(statement)


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260719_0029_aibrain_non_streaming.py"
    )
    spec = importlib.util.spec_from_file_location("aibrain_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_aibrain_migration_creates_wallet_chat_and_billing_schema() -> None:
    migration = _load_migration()
    fake_op = _MigrationOp()
    migration.op = fake_op

    migration.upgrade()

    assert migration.revision == "20260719_0029"
    assert migration.down_revision == "20260719_0028"
    assert set(fake_op.created_tables) == {
        "reasoning_wallets",
        "chat_conversations",
        "chat_messages",
        "reasoning_ledger_entries",
    }
    assert {
        table_name
        for table_name, _name, condition in fake_op.created_checks
        if "'chat'" in condition
    } == {"provider_configs", "usage_records"}
    assert ("usage_records", "provider_cost_usd") in fake_op.added_columns
    assert ("usage_records", "chat_message_id") in fake_op.added_columns
    assert ("usage_records", "credits") in fake_op.altered_columns
    assert fake_op.inserted == [
        (
            "provider_configs",
            [
                {
                    "id": "chat-apimart-gpt56",
                    "tenant_id": None,
                    "capability": "chat",
                    "provider": "apimart-gpt56",
                    "config": {},
                    "is_active": True,
                }
            ],
        )
    ]


def test_aibrain_migration_downgrade_removes_its_schema() -> None:
    migration = _load_migration()
    fake_op = _MigrationOp()
    migration.op = fake_op

    migration.downgrade()

    assert fake_op.dropped_tables == [
        "reasoning_ledger_entries",
        "chat_messages",
        "chat_conversations",
        "reasoning_wallets",
    ]
    assert ("usage_records", "provider_cost_usd") in fake_op.dropped_columns
    assert ("usage_records", "chat_message_id") in fake_op.dropped_columns
