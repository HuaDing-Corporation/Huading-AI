"""Align batch jobs with the batch production contract."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260702_0014"
down_revision = "20260702_0013"
branch_labels = None
depends_on = None

_VIDEO_TASK_STATUS_CHECK = "status IN ('queued', 'running', 'done', 'failed', 'cancelled')"
_LEGACY_VIDEO_TASK_STATUS_CHECK = "status IN ('queued', 'running', 'done', 'failed')"
_BATCH_KIND_CHECK = "kind IN ('ecom_table', 'prompt_set')"
_BATCH_STATUS_CHECK = (
    "status IN ('running', 'completed', 'partial_failed', 'failed', 'cancelled')"
)
_LEGACY_BATCH_STATUS_CHECK = "status IN ('queued', 'running', 'done', 'failed')"


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _check_exists(table_name: str, check_name: str) -> bool:
    checks = sa.inspect(op.get_bind()).get_check_constraints(table_name)
    return any(check["name"] == check_name for check in checks)


def _replace_check(table_name: str, check_name: str, condition: str) -> None:
    check_exists = _check_exists(table_name, check_name)
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            if check_exists:
                batch.drop_constraint(check_name, type_="check")
            batch.create_check_constraint(check_name, condition)
        return
    if check_exists:
        op.drop_constraint(check_name, table_name, type_="check")
    op.create_check_constraint(check_name, table_name, condition)


def _drop_check(table_name: str, check_name: str) -> None:
    if not _check_exists(table_name, check_name):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            batch.drop_constraint(check_name, type_="check")
        return
    op.drop_constraint(check_name, table_name, type_="check")


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _drop_columns_if_present(table_name: str, names: list[str]) -> None:
    existing = _columns(table_name)
    selected = [name for name in names if name in existing]
    if not selected:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            for name in selected:
                batch.drop_column(name)
        return
    for name in selected:
        op.drop_column(table_name, name)


def upgrade() -> None:
    _add_column_if_missing("batch_jobs", sa.Column("user_id", sa.String(length=36)))
    _add_column_if_missing("batch_jobs", sa.Column("kind", sa.String(length=32)))
    _add_column_if_missing("batch_jobs", sa.Column("total", sa.Integer()))
    _add_column_if_missing("batch_jobs", sa.Column("succeeded", sa.Integer()))
    _add_column_if_missing("batch_jobs", sa.Column("failed", sa.Integer()))
    _add_column_if_missing("batch_jobs", sa.Column("common_params", sa.JSON()))
    _add_column_if_missing("batch_jobs", sa.Column("updated_at", sa.DateTime(timezone=True)))
    _drop_check("batch_jobs", "ck_batch_jobs_status")
    op.execute(
        """
        UPDATE batch_jobs
        SET
            user_id = COALESCE(user_id, created_by),
            kind = COALESCE(
                kind,
                CASE WHEN source_type = 'csv' THEN 'ecom_table' ELSE 'prompt_set' END
            ),
            status = CASE
                WHEN status = 'done' THEN 'completed'
                WHEN status = 'queued' THEN 'running'
                ELSE status
            END,
            total = COALESCE(total, total_count, 0),
            succeeded = COALESCE(succeeded, done_count, 0),
            failed = COALESCE(failed, 0),
            common_params = COALESCE(common_params, '{}'),
            updated_at = COALESCE(updated_at, created_at)
        """
    )
    _drop_check("batch_jobs", "ck_batch_jobs_source_type")
    _replace_check("batch_jobs", "ck_batch_jobs_status", _BATCH_STATUS_CHECK)
    _replace_check("batch_jobs", "ck_batch_jobs_kind", _BATCH_KIND_CHECK)
    _replace_check("video_tasks", "ck_video_tasks_status", _VIDEO_TASK_STATUS_CHECK)
    _drop_columns_if_present(
        "batch_jobs",
        ["created_by", "source_type", "total_count", "done_count"],
    )


def downgrade() -> None:
    _add_column_if_missing("batch_jobs", sa.Column("created_by", sa.String(length=36)))
    _add_column_if_missing(
        "batch_jobs",
        sa.Column("source_type", sa.String(length=16), server_default="manual"),
    )
    _add_column_if_missing("batch_jobs", sa.Column("total_count", sa.Integer()))
    _add_column_if_missing("batch_jobs", sa.Column("done_count", sa.Integer()))
    _drop_check("batch_jobs", "ck_batch_jobs_status")
    op.execute(
        """
        UPDATE batch_jobs
        SET
            created_by = COALESCE(created_by, user_id),
            source_type = COALESCE(
                source_type,
                CASE WHEN kind = 'ecom_table' THEN 'csv' ELSE 'manual' END
            ),
            status = CASE
                WHEN status = 'completed' THEN 'done'
                WHEN status IN ('partial_failed', 'cancelled') THEN 'failed'
                ELSE status
            END,
            total_count = COALESCE(total_count, total, 0),
            done_count = COALESCE(done_count, succeeded, 0)
        """
    )
    _drop_check("batch_jobs", "ck_batch_jobs_kind")
    _replace_check("batch_jobs", "ck_batch_jobs_status", _LEGACY_BATCH_STATUS_CHECK)
    _drop_check("video_tasks", "ck_video_tasks_status")
    op.execute("UPDATE video_tasks SET status = 'failed' WHERE status = 'cancelled'")
    _replace_check("video_tasks", "ck_video_tasks_status", _LEGACY_VIDEO_TASK_STATUS_CHECK)
    _drop_columns_if_present(
        "batch_jobs",
        ["user_id", "kind", "total", "succeeded", "failed", "common_params", "updated_at"],
    )
