from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Asset,
    BgmLibraryTrack,
    BrandAsset,
    EcomReplicateOutput,
    ReversePromptJob,
    VideoTask,
)
from app.db.session import Base, SessionLocal
from app.services.storage.base import StorageKeyError
from app.services.storage.keys import (
    validate_catalog_storage_key,
    validate_tenant_storage_key,
)


@dataclass(frozen=True)
class StorageKeyFinding:
    source: str
    row_id: str
    field: str
    tenant_id: str
    storage_key: str
    reason: str


StorageKeyScope = Literal["tenant", "catalog", "tenant_or_catalog"]


@dataclass(frozen=True)
class StorageKeyScanPolicy:
    model: type[Any]
    scope: StorageKeyScope


_STORAGE_KEY_SCAN_POLICIES = (
    StorageKeyScanPolicy(VideoTask, "tenant"),
    StorageKeyScanPolicy(BgmLibraryTrack, "catalog"),
    StorageKeyScanPolicy(Asset, "tenant_or_catalog"),
    StorageKeyScanPolicy(BrandAsset, "tenant"),
    StorageKeyScanPolicy(EcomReplicateOutput, "tenant"),
    StorageKeyScanPolicy(ReversePromptJob, "tenant"),
)


def _is_persisted_storage_key_column(column_name: str) -> bool:
    return (
        column_name == "storage_key"
        or column_name == "thumbnail_key"
        or column_name.endswith("_storage_key")
    )


def persisted_storage_key_columns() -> set[tuple[str, str]]:
    return {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if _is_persisted_storage_key_column(column.name)
    }


def _model_storage_key_fields(model: type[Any]) -> tuple[str, ...]:
    return tuple(
        column.name
        for column in model.__table__.columns
        if _is_persisted_storage_key_column(column.name)
    )


def configured_storage_key_columns() -> set[tuple[str, str]]:
    return {
        (policy.model.__tablename__, field)
        for policy in _STORAGE_KEY_SCAN_POLICIES
        for field in _model_storage_key_fields(policy.model)
    }


def _ensure_storage_key_scan_coverage() -> None:
    persisted = persisted_storage_key_columns()
    configured = configured_storage_key_columns()
    if persisted == configured:
        return
    missing = sorted(persisted - configured)
    unexpected = sorted(configured - persisted)
    raise RuntimeError(
        "Storage key scanner policy is incomplete: "
        f"missing={missing!r}, unexpected={unexpected!r}."
    )


def _reason(tenant_id: str, storage_key: str) -> str | None:
    try:
        validate_tenant_storage_key(tenant_id, storage_key)
    except StorageKeyError:
        if not storage_key.startswith(f"tenants/{tenant_id}/"):
            return "tenant_prefix_mismatch"
        return "noncanonical_path"
    return None


def _catalog_reason(storage_key: str) -> str | None:
    try:
        validate_catalog_storage_key(storage_key)
    except StorageKeyError:
        if not storage_key.startswith(("platform/", "library/bgm/")):
            return "catalog_prefix_mismatch"
        return "noncanonical_path"
    return None


def _record_finding(
    findings: list[StorageKeyFinding],
    *,
    source: str,
    row_id: Any,
    field: str,
    tenant_id: Any,
    storage_key: Any,
) -> None:
    if storage_key is None:
        return
    tenant_value = str(tenant_id or "")
    key_value = str(storage_key)
    reason = _reason(tenant_value, key_value)
    if reason is None:
        return
    findings.append(
        StorageKeyFinding(
            source=source,
            row_id=str(row_id),
            field=field,
            tenant_id=tenant_value,
            storage_key=key_value,
            reason=reason,
        )
    )


def _record_catalog_finding(
    findings: list[StorageKeyFinding],
    *,
    source: str,
    row_id: Any,
    field: str,
    storage_key: Any,
) -> None:
    if storage_key is None:
        return
    key_value = str(storage_key)
    reason = _catalog_reason(key_value)
    if reason is None:
        return
    findings.append(
        StorageKeyFinding(
            source=source,
            row_id=str(row_id),
            field=field,
            tenant_id="",
            storage_key=key_value,
            reason=reason,
        )
    )


def scan_storage_keys(db: Session) -> list[StorageKeyFinding]:
    _ensure_storage_key_scan_coverage()
    findings: list[StorageKeyFinding] = []
    for policy in _STORAGE_KEY_SCAN_POLICIES:
        model = policy.model
        primary_keys = tuple(model.__mapper__.primary_key)
        if len(primary_keys) != 1:
            raise RuntimeError(f"Storage key scan requires one primary key: {model!r}.")
        primary_key = getattr(model, primary_keys[0].key)
        key_fields = _model_storage_key_fields(model)
        needs_tenant = policy.scope != "catalog"
        columns = [primary_key]
        if needs_tenant:
            columns.append(model.tenant_id)
        columns.extend(getattr(model, field) for field in key_fields)

        for row in db.execute(
            select(*columns).execution_options(yield_per=500)
        ):
            values = tuple(row)
            row_id = values[0]
            tenant_id = values[1] if needs_tenant else None
            key_offset = 2 if needs_tenant else 1
            for field, storage_key in zip(key_fields, values[key_offset:], strict=True):
                use_catalog = policy.scope == "catalog" or (
                    policy.scope == "tenant_or_catalog" and tenant_id is None
                )
                if use_catalog:
                    _record_catalog_finding(
                        findings,
                        source=model.__tablename__,
                        row_id=row_id,
                        field=field,
                        storage_key=storage_key,
                    )
                else:
                    _record_finding(
                        findings,
                        source=model.__tablename__,
                        row_id=row_id,
                        field=field,
                        tenant_id=tenant_id,
                        storage_key=storage_key,
                    )
    return findings


def _payload(findings: list[StorageKeyFinding]) -> dict[str, object]:
    reason_counts: dict[str, int] = {}
    for finding in findings:
        reason_counts[finding.reason] = reason_counts.get(finding.reason, 0) + 1
    return {
        "mode": "dry-run-read-only",
        "finding_count": len(findings),
        "reason_counts": reason_counts,
        "findings": [asdict(finding) for finding in findings],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only scan for noncanonical or cross-tenant storage keys."
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with SessionLocal() as db:
        payload = _payload(scan_storage_keys(db))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Storage key scan (dry-run, read-only)")
        print(f"Findings: {payload['finding_count']}")
        for finding in payload["findings"]:
            print(json.dumps(finding, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
