from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Base, Tenant
from app.db.session import SessionLocal
from app.services.gc.observation import (
    GcObjectObservation,
    GcObservationResult,
    observe_gc_candidates,
)
from app.services.gc.reference_scan import (
    OrphanScanCoverageError,
    ReferenceSurface,
    StorageKeyScope,
    classify_storage_key,
    discover_reference_surfaces,
    find_referenced_keys,
)
from app.services.storage.base import ObjectStorage
from app.services.storage.factory import create_object_storage
from app.services.storage.keys import head_tenant_storage_identity

DEFAULT_GRACE_HOURS = 24.0


@dataclass(frozen=True)
class StorageObjectInfo:
    key: str
    size: int
    last_modified: datetime
    version_id: str | None = None
    etag: str | None = None


@dataclass(frozen=True)
class OrphanObjectFinding:
    tenant_id: str
    key: str
    key_hash: str
    size: int
    last_modified: datetime
    version_id: str | None
    etag: str | None
    reason: str
    checked_references: tuple[str, ...]
    schema_fingerprint: str
    reference_evidence: dict[str, object]


@dataclass(frozen=True)
class SkippedObjectFinding:
    key_hash: str
    reason: str


@dataclass(frozen=True)
class OrphanScanReport:
    scan_id: str
    scanned_at: datetime
    bucket: str
    schema_fingerprint: str
    grace_hours: float
    scanned_object_count: int
    eligible_object_count: int
    referenced_object_count: int
    recent_object_count: int
    catalog_object_count: int
    reference_surfaces: tuple[ReferenceSurface, ...]
    orphans: tuple[OrphanObjectFinding, ...]
    skipped_objects: tuple[SkippedObjectFinding, ...]
    tenant_objects: tuple[GcObjectObservation, ...]


def _as_utc(value: datetime, *, source: str) -> datetime:
    if not isinstance(value, datetime):
        raise OrphanScanCoverageError(
            f"Storage inventory returned invalid last-modified metadata for {source}."
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _local_inventory(root_value: object) -> Iterator[StorageObjectInfo]:
    root = Path(root_value).resolve()
    if not root.exists():
        raise OrphanScanCoverageError("Local storage inventory root does not exist.")
    if not root.is_dir():
        raise OrphanScanCoverageError("Local storage inventory root is not a directory.")
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise OrphanScanCoverageError(
                    f"Local storage inventory contains a symlink: {path}."
                )
            if not path.is_file():
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise OrphanScanCoverageError(
                    "Local storage inventory escaped its configured root."
                ) from exc
            stat = resolved.stat()
            yield StorageObjectInfo(
                key=path.relative_to(root).as_posix(),
                size=int(stat.st_size),
                last_modified=datetime.fromtimestamp(
                    stat.st_mtime,
                    tz=UTC,
                ),
            )
    except OrphanScanCoverageError:
        raise
    except Exception as exc:
        raise OrphanScanCoverageError("Local storage inventory could not be completed.") from exc


def _s3_inventory(storage: object, client: object) -> Iterator[StorageObjectInfo]:
    get_paginator = getattr(client, "get_paginator", None)
    if not callable(get_paginator):
        raise OrphanScanCoverageError(
            "Configured object storage does not expose a readable inventory."
        )
    bucket = str(getattr(storage, "bucket", "") or "")
    if not bucket:
        raise OrphanScanCoverageError("Configured object storage inventory has no bucket.")
    try:
        paginator = get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            contents = page.get("Contents") or ()
            for item in contents:
                key = item.get("Key")
                size = item.get("Size")
                modified = item.get("LastModified")
                etag = item.get("ETag")
                if not isinstance(key, str) or not key or not isinstance(size, int):
                    raise OrphanScanCoverageError(
                        "S3 storage inventory returned incomplete object metadata."
                    )
                yield StorageObjectInfo(
                    key=key,
                    size=size,
                    last_modified=_as_utc(modified, source=key),
                    etag=str(etag).strip('"') if etag else None,
                )
    except OrphanScanCoverageError:
        raise
    except Exception as exc:
        raise OrphanScanCoverageError("S3 storage inventory could not be completed.") from exc


def storage_inventory(storage: object) -> tuple[StorageObjectInfo, ...]:
    root = getattr(storage, "root", None)
    client = getattr(storage, "client", None)
    if root is not None:
        inventory = _local_inventory(root)
    elif client is not None:
        inventory = _s3_inventory(storage, client)
    else:
        raise OrphanScanCoverageError(
            "Configured object storage does not expose a readable inventory."
        )

    objects: list[StorageObjectInfo] = []
    seen: set[str] = set()
    for item in inventory:
        if item.key in seen:
            raise OrphanScanCoverageError(
                f"Storage inventory returned duplicate object key: {item.key}."
            )
        seen.add(item.key)
        objects.append(
            StorageObjectInfo(
                key=item.key,
                size=item.size,
                last_modified=_as_utc(item.last_modified, source=item.key),
                version_id=item.version_id,
                etag=item.etag,
            )
        )
    return tuple(sorted(objects, key=lambda item: item.key))


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _read_object_identity(
    storage: ObjectStorage,
    item: StorageObjectInfo,
    *,
    tenant_id: str,
) -> StorageObjectInfo:
    try:
        identity = head_tenant_storage_identity(
            storage,
            tenant_id=tenant_id,
            storage_key=item.key,
        )
        if identity is None:
            return item
        if identity.size < 0:
            raise ValueError("negative object size")
        return StorageObjectInfo(
            key=item.key,
            size=int(identity.size),
            last_modified=_as_utc(identity.last_modified, source=item.key),
            version_id=identity.version_id,
            etag=identity.etag,
        )
    except Exception as exc:
        raise OrphanScanCoverageError(
            f"Storage identity read failed for key hash {_key_hash(item.key)}."
        ) from exc


def scan_orphan_objects(
    db: Session,
    *,
    storage: object,
    grace_period: timedelta = timedelta(hours=DEFAULT_GRACE_HOURS),
    now: datetime | None = None,
    scan_id: str | None = None,
) -> OrphanScanReport:
    if grace_period < timedelta(0):
        raise ValueError("grace_period must be non-negative.")
    scan_time = _as_utc(now or datetime.now(UTC), source="scan clock")
    objects = storage_inventory(storage)
    surfaces = discover_reference_surfaces(Base.metadata)
    known_tenant_ids = set(db.scalars(select(Tenant.id)))
    cutoff = scan_time - grace_period

    catalog_count = 0
    recent_count = 0
    eligible: list[StorageObjectInfo] = []
    tenant_ids_by_key: dict[str, str] = {}
    skipped: list[SkippedObjectFinding] = []
    for item in objects:
        classification = classify_storage_key(
            item.key,
            known_tenant_ids=known_tenant_ids,
        )
        if classification.scope is StorageKeyScope.CATALOG:
            catalog_count += 1
            skipped.append(
                SkippedObjectFinding(
                    key_hash=_key_hash(item.key),
                    reason=str(classification.skip_reason),
                )
            )
        elif classification.scope is StorageKeyScope.INVALID:
            skipped.append(
                SkippedObjectFinding(
                    key_hash=_key_hash(item.key),
                    reason=str(classification.skip_reason),
                )
            )
        elif item.last_modified > cutoff:
            recent_count += 1
        else:
            item = _read_object_identity(
                storage,
                item,
                tenant_id=str(classification.tenant_id),
            )
            eligible.append(item)
            tenant_ids_by_key[item.key] = str(classification.tenant_id)

    eligible_keys = {item.key for item in eligible}
    references = find_referenced_keys(
        db,
        object_keys=eligible_keys,
        surfaces=surfaces,
    )
    referenced = references.referenced_keys
    checked_references = references.checked_protective_surfaces
    orphans = tuple(
        OrphanObjectFinding(
            tenant_id=tenant_ids_by_key[item.key],
            key=item.key,
            key_hash=_key_hash(item.key),
            size=item.size,
            last_modified=item.last_modified,
            version_id=item.version_id,
            etag=item.etag,
            reason="no_database_reference",
            checked_references=checked_references,
            schema_fingerprint=references.schema_fingerprint,
            reference_evidence=references.evidence_for(item.key),
        )
        for item in eligible
        if item.key not in referenced
    )
    tenant_objects = tuple(
        GcObjectObservation(
            tenant_id=tenant_ids_by_key[item.key],
            key=item.key,
            size=item.size,
            last_modified=item.last_modified,
            version_id=item.version_id,
            etag=item.etag,
            referenced=item.key in referenced,
            reference_evidence=references.evidence_for(item.key),
        )
        for item in eligible
    )
    return OrphanScanReport(
        scan_id=scan_id or str(uuid4()),
        scanned_at=scan_time,
        bucket=str(getattr(storage, "bucket", "") or ""),
        schema_fingerprint=references.schema_fingerprint,
        grace_hours=grace_period.total_seconds() / 3600,
        scanned_object_count=len(objects),
        eligible_object_count=len(eligible),
        referenced_object_count=len(referenced),
        recent_object_count=recent_count,
        catalog_object_count=catalog_count,
        reference_surfaces=surfaces,
        orphans=orphans,
        skipped_objects=tuple(skipped),
        tenant_objects=tenant_objects,
    )


def record_gc_candidates(db: Session, report: OrphanScanReport) -> GcObservationResult:
    return observe_gc_candidates(
        db,
        bucket=report.bucket,
        scan_id=report.scan_id,
        schema_fingerprint=report.schema_fingerprint,
        scanned_at=report.scanned_at,
        observations=report.tenant_objects,
    )


def _finding_payload(finding: OrphanObjectFinding) -> dict[str, object]:
    return {
        "key_hash": finding.key_hash,
        "size": finding.size,
        "last_modified": finding.last_modified.isoformat(),
        "version_id": finding.version_id,
        "etag": finding.etag,
        "reason": finding.reason,
        "checked_references": list(finding.checked_references),
        "schema_fingerprint": finding.schema_fingerprint,
        "reference_evidence": finding.reference_evidence,
    }


def report_payload(report: OrphanScanReport) -> dict[str, object]:
    return {
        "mode": "dry-run-read-only",
        "scan_id": report.scan_id,
        "scanned_at": report.scanned_at.isoformat(),
        "bucket": report.bucket,
        "schema_fingerprint": report.schema_fingerprint,
        "grace_hours": report.grace_hours,
        "scanned_object_count": report.scanned_object_count,
        "eligible_object_count": report.eligible_object_count,
        "referenced_object_count": report.referenced_object_count,
        "recent_object_count": report.recent_object_count,
        "catalog_object_count": report.catalog_object_count,
        "orphan_count": len(report.orphans),
        "skipped_object_count": len(report.skipped_objects),
        "reference_surfaces": [
            {
                "kind": surface.kind,
                "table": surface.table,
                "column": surface.column,
                "label": surface.label,
                "classification": surface.classification.value,
            }
            for surface in report.reference_surfaces
        ],
        "orphans": [_finding_payload(finding) for finding in report.orphans],
        "skipped_objects": [
            {"key_hash": finding.key_hash, "reason": finding.reason}
            for finding in report.skipped_objects
        ],
    }


def _nonnegative_hours(value: str) -> float:
    hours = float(value)
    if hours < 0:
        raise argparse.ArgumentTypeError("grace hours must be non-negative")
    return hours


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan storage references without deleting objects or product records."
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON report.")
    parser.add_argument(
        "--record-candidates",
        action="store_true",
        help="Persist Phase 1 observations in GC control tables; never deletes media.",
    )
    parser.add_argument(
        "--grace-hours",
        type=_nonnegative_hours,
        default=DEFAULT_GRACE_HOURS,
        help="Ignore non-catalog objects newer than this many hours (default: 24).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    storage: ObjectStorage = create_object_storage(settings)
    observation_result: GcObservationResult | None = None
    with SessionLocal() as db:
        report = scan_orphan_objects(
            db,
            storage=storage,
            grace_period=timedelta(hours=args.grace_hours),
        )
        if args.record_candidates:
            observation_result = record_gc_candidates(db, report)
            db.commit()
    payload = report_payload(report)
    if observation_result is not None:
        payload["mode"] = "gc-candidate-observation"
        payload["candidate_observation"] = {
            "observed_count": observation_result.observed_count,
            "eligible_count": observation_result.eligible_count,
            "skipped_count": observation_result.skipped_count,
        }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Orphan object scan ({payload['mode']})")
        print(f"Scanned objects: {payload['scanned_object_count']}")
        print(f"Orphans: {payload['orphan_count']}")
        for finding in payload["orphans"]:
            print(json.dumps(finding, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
