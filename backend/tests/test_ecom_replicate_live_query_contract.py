from __future__ import annotations

import ast
from pathlib import Path

_LIVE_QUERY_MARKERS = (
    "live_ecom_replicate_job_condition",
    "select_live_ecom_replicate_jobs",
)


def _references_ecom_job(nodes: list[ast.AST]) -> bool:
    return any(
        isinstance(item, ast.Name) and item.id == "EcomReplicateJob"
        for node in nodes
        for item in ast.walk(node)
    )


def _enclosing_statement(
    node: ast.AST,
    *,
    parents: dict[ast.AST, ast.AST],
) -> ast.stmt:
    current = node
    while not isinstance(current, ast.stmt):
        current = parents[current]
    return current


# Audit boundary: this guard recognizes literal model references in
# select(EcomReplicateJob), update(EcomReplicateJob),
# db.get(EcomReplicateJob, id), and db.query(EcomReplicateJob). It cannot follow
# variable-held models (model = EcomReplicateJob; select(model)), dynamic
# mappings, or import aliases. The only current dynamic-mapping consumer,
# services/admin_console.py:604, explicitly uses the live helper, so there is no
# known bypass. Variable data-flow analysis remains backlog and is out of scope
# for this package.
def _unguarded_ecom_job_reads(source: str, *, source_name: str) -> list[str]:
    tree = ast.parse(source, filename=source_name)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    violations: list[str] = []
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        direct_query = (
            isinstance(call.func, ast.Name)
            and call.func.id in {"select", "update"}
            and _references_ecom_job(list(call.args))
        )
        orm_query = (
            isinstance(call.func, ast.Attribute)
            and call.func.attr in {"get", "query"}
            and bool(call.args)
            and _references_ecom_job([call.args[0]])
        )
        if not direct_query and not orm_query:
            continue

        statement = _enclosing_statement(call, parents=parents)
        statement_source = ast.get_source_segment(source, statement) or ""
        if any(marker in statement_source for marker in _LIVE_QUERY_MARKERS):
            continue
        violations.append(f"{source_name}:{call.lineno}")
    return violations


def test_live_ecom_query_audit_rejects_an_unguarded_fake_endpoint() -> None:
    fake_endpoint = """
def get_fake_ecom_job(db, job_id):
    return db.scalar(
        select(EcomReplicateJob).where(EcomReplicateJob.id == job_id)
    )
"""

    assert _unguarded_ecom_job_reads(
        fake_endpoint,
        source_name="fake_ecom_endpoint.py",
    ) == ["fake_ecom_endpoint.py:4"]


def test_live_ecom_query_audit_rejects_an_unguarded_db_get() -> None:
    fake_endpoint = """
def get_fake_ecom_job(db, job_id):
    return db.get(EcomReplicateJob, job_id)
"""

    assert _unguarded_ecom_job_reads(
        fake_endpoint,
        source_name="fake_ecom_db_get.py",
    ) == ["fake_ecom_db_get.py:3"]


def test_live_ecom_query_audit_rejects_an_unguarded_db_query() -> None:
    fake_endpoint = """
def list_fake_ecom_jobs(db):
    return db.query(EcomReplicateJob)
"""

    assert _unguarded_ecom_job_reads(
        fake_endpoint,
        source_name="fake_ecom_db_query.py",
    ) == ["fake_ecom_db_query.py:3"]


def test_all_ecom_replicate_job_reads_use_the_live_query_contract() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    violations = [
        violation
        for path in sorted(app_root.rglob("*.py"))
        for violation in _unguarded_ecom_job_reads(
            path.read_text(encoding="utf-8"),
            source_name=path.relative_to(app_root).as_posix(),
        )
    ]

    assert not violations, "\n".join(violations)
