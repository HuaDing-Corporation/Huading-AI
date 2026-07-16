from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Asset,
    BrandAsset,
    EcomReplicateOutput,
    ReversePromptJob,
    VideoTask,
)
from app.db.session import SessionLocal
from app.services.storage.base import StorageKeyError
from app.services.storage.keys import validate_tenant_storage_key


@dataclass(frozen=True)
class StorageKeyFinding:
    source: str
    row_id: str
    field: str
    tenant_id: str
    storage_key: str
    reason: str


def _reason(tenant_id: str, storage_key: str) -> str | None:
    try:
        validate_tenant_storage_key(tenant_id, storage_key)
    except StorageKeyError:
        if not storage_key.startswith(f"tenants/{tenant_id}/"):
            return "tenant_prefix_mismatch"
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


def scan_storage_keys(db: Session) -> list[StorageKeyFinding]:
    findings: list[StorageKeyFinding] = []
    for row in db.execute(
        select(
            VideoTask.id,
            VideoTask.tenant_id,
            VideoTask.storage_key,
            VideoTask.thumbnail_key,
        ).execution_options(yield_per=500)
    ):
        _record_finding(
            findings,
            source="video_tasks",
            row_id=row.id,
            field="storage_key",
            tenant_id=row.tenant_id,
            storage_key=row.storage_key,
        )
        _record_finding(
            findings,
            source="video_tasks",
            row_id=row.id,
            field="thumbnail_key",
            tenant_id=row.tenant_id,
            storage_key=row.thumbnail_key,
        )

    sources = (
        (
            "assets",
            select(Asset.id, Asset.tenant_id, Asset.storage_key).where(
                Asset.tenant_id.is_not(None)
            ),
        ),
        (
            "brand_assets",
            select(BrandAsset.id, BrandAsset.tenant_id, BrandAsset.storage_key),
        ),
        (
            "ecom_replicate_outputs",
            select(
                EcomReplicateOutput.id,
                EcomReplicateOutput.tenant_id,
                EcomReplicateOutput.storage_key,
            ),
        ),
        (
            "reverse_prompt_jobs",
            select(
                ReversePromptJob.id,
                ReversePromptJob.tenant_id,
                ReversePromptJob.source_storage_key.label("storage_key"),
            ),
        ),
    )
    for source, statement in sources:
        for row in db.execute(statement.execution_options(yield_per=500)):
            _record_finding(
                findings,
                source=source,
                row_id=row.id,
                field=(
                    "source_storage_key"
                    if source == "reverse_prompt_jobs"
                    else "storage_key"
                ),
                tenant_id=row.tenant_id,
                storage_key=row.storage_key,
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
