from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import JSON, String, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import MetaData

from app.core.config import settings
from app.db.models import Asset, Base
from app.db.session import SessionLocal
from app.services.storage.base import ObjectStorage, StorageKeyError
from app.services.storage.factory import create_object_storage
from app.services.storage.keys import validate_catalog_storage_key
from scripts.ops.scan_storage_keys import persisted_storage_key_columns

DEFAULT_GRACE_HOURS = 24.0


class OrphanScanCoverageError(RuntimeError):
    """Raised when a complete, read-only orphan classification cannot be proven."""


@dataclass(frozen=True)
class StorageObjectInfo:
    key: str
    size: int
    last_modified: datetime


ReferenceKind = Literal["string", "json", "asset_fk"]


@dataclass(frozen=True)
class ReferenceSurface:
    table: str
    column: str
    kind: ReferenceKind

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.table}.{self.column}"


@dataclass(frozen=True)
class OrphanObjectFinding:
    key: str
    size: int
    last_modified: datetime
    reason: str
    checked_references: tuple[str, ...]


@dataclass(frozen=True)
class OrphanScanReport:
    bucket: str
    grace_hours: float
    scanned_object_count: int
    eligible_object_count: int
    referenced_object_count: int
    recent_object_count: int
    catalog_object_count: int
    reference_surfaces: tuple[ReferenceSurface, ...]
    orphans: tuple[OrphanObjectFinding, ...]


def _looks_like_storage_locator(column_name: str) -> bool:
    name = column_name.lower()
    return name in {"key", "path", "url"} or name.endswith(("_key", "_path", "_url"))


def discover_reference_surfaces(metadata: MetaData) -> tuple[ReferenceSurface, ...]:
    surfaces: list[ReferenceSurface] = []
    unsupported: list[str] = []
    for table in sorted(metadata.tables.values(), key=lambda item: item.name):
        for column in table.columns:
            locator = _looks_like_storage_locator(column.name)
            if locator and not isinstance(column.type, String):
                unsupported.append(f"{table.name}.{column.name}")
            elif locator:
                surfaces.append(ReferenceSurface(table.name, column.name, "string"))

            if isinstance(column.type, JSON):
                surfaces.append(ReferenceSurface(table.name, column.name, "json"))

            if any(
                foreign_key.target_fullname == "assets.id" for foreign_key in column.foreign_keys
            ):
                if not isinstance(column.type, String):
                    unsupported.append(f"{table.name}.{column.name}")
                else:
                    surfaces.append(ReferenceSurface(table.name, column.name, "asset_fk"))

    if unsupported:
        raise OrphanScanCoverageError(
            f"Unsupported reflected storage-reference column types: {sorted(set(unsupported))!r}."
        )

    direct_columns = {
        (surface.table, surface.column) for surface in surfaces if surface.kind == "string"
    }
    if metadata is Base.metadata:
        missing = persisted_storage_key_columns() - direct_columns
        if missing:
            raise OrphanScanCoverageError(
                f"Persisted storage-key discovery is incomplete: missing={sorted(missing)!r}."
            )

    if not surfaces:
        raise OrphanScanCoverageError(
            "No database reference surfaces were discovered; refusing to report clean."
        )
    return tuple(sorted(set(surfaces), key=lambda surface: surface.label))


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
        return
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
                if not isinstance(key, str) or not key or not isinstance(size, int):
                    raise OrphanScanCoverageError(
                        "S3 storage inventory returned incomplete object metadata."
                    )
                yield StorageObjectInfo(
                    key=key,
                    size=size,
                    last_modified=_as_utc(modified, source=key),
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
            )
        )
    return tuple(sorted(objects, key=lambda item: item.key))


def _json_references(
    value: object,
    *,
    object_keys: set[str],
    asset_keys_by_id: Mapping[str, str],
) -> set[str]:
    if isinstance(value, str):
        references = {value} & object_keys
        asset_key = asset_keys_by_id.get(value)
        if asset_key is not None:
            references.add(asset_key)
        return references
    if isinstance(value, Mapping):
        references: set[str] = set()
        for key, item in value.items():
            references.update(
                _json_references(
                    key,
                    object_keys=object_keys,
                    asset_keys_by_id=asset_keys_by_id,
                )
            )
            references.update(
                _json_references(
                    item,
                    object_keys=object_keys,
                    asset_keys_by_id=asset_keys_by_id,
                )
            )
        return references
    if isinstance(value, (list, tuple, set)):
        references: set[str] = set()
        for item in value:
            references.update(
                _json_references(
                    item,
                    object_keys=object_keys,
                    asset_keys_by_id=asset_keys_by_id,
                )
            )
        return references
    return set()


def _asset_keys_by_id(db: Session, object_keys: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        rows = db.execute(select(Asset.id, Asset.storage_key).execution_options(yield_per=500))
        for asset_id, storage_key in rows:
            if isinstance(storage_key, str) and storage_key in object_keys:
                result[str(asset_id)] = storage_key
    except Exception as exc:
        raise OrphanScanCoverageError("Asset reference mapping could not be completed.") from exc
    return result


def _referenced_object_keys(
    db: Session,
    *,
    object_keys: set[str],
    surfaces: Iterable[ReferenceSurface],
) -> set[str]:
    if not object_keys:
        return set()
    asset_keys_by_id = _asset_keys_by_id(db, object_keys)
    by_table: dict[str, list[ReferenceSurface]] = defaultdict(list)
    for surface in surfaces:
        by_table[surface.table].append(surface)

    referenced: set[str] = set()
    for table_name, table_surfaces in sorted(by_table.items()):
        table = Base.metadata.tables[table_name]
        columns = [table.c[surface.column] for surface in table_surfaces]
        try:
            rows = db.execute(select(*columns).execution_options(yield_per=500))
            for row in rows:
                for surface, value in zip(table_surfaces, row, strict=True):
                    if surface.kind == "string":
                        if isinstance(value, str) and value in object_keys:
                            referenced.add(value)
                    elif surface.kind == "asset_fk":
                        storage_key = asset_keys_by_id.get(str(value or ""))
                        if storage_key is not None:
                            referenced.add(storage_key)
                    else:
                        referenced.update(
                            _json_references(
                                value,
                                object_keys=object_keys,
                                asset_keys_by_id=asset_keys_by_id,
                            )
                        )
        except Exception as exc:
            raise OrphanScanCoverageError(
                f"Database reference scan failed for table {table_name!r}."
            ) from exc
    return referenced


def _is_catalog_object(key: str) -> bool:
    try:
        validate_catalog_storage_key(key)
    except StorageKeyError:
        return False
    return True


def scan_orphan_objects(
    db: Session,
    *,
    storage: object,
    grace_period: timedelta = timedelta(hours=DEFAULT_GRACE_HOURS),
    now: datetime | None = None,
) -> OrphanScanReport:
    if grace_period < timedelta(0):
        raise ValueError("grace_period must be non-negative.")
    scan_time = _as_utc(now or datetime.now(UTC), source="scan clock")
    objects = storage_inventory(storage)
    surfaces = discover_reference_surfaces(Base.metadata)
    cutoff = scan_time - grace_period

    catalog_count = 0
    recent_count = 0
    eligible: list[StorageObjectInfo] = []
    for item in objects:
        if _is_catalog_object(item.key):
            catalog_count += 1
        elif item.last_modified > cutoff:
            recent_count += 1
        else:
            eligible.append(item)

    eligible_keys = {item.key for item in eligible}
    referenced = _referenced_object_keys(
        db,
        object_keys=eligible_keys,
        surfaces=surfaces,
    )
    checked_references = tuple(surface.label for surface in surfaces)
    orphans = tuple(
        OrphanObjectFinding(
            key=item.key,
            size=item.size,
            last_modified=item.last_modified,
            reason="no_database_reference",
            checked_references=checked_references,
        )
        for item in eligible
        if item.key not in referenced
    )
    return OrphanScanReport(
        bucket=str(getattr(storage, "bucket", "") or ""),
        grace_hours=grace_period.total_seconds() / 3600,
        scanned_object_count=len(objects),
        eligible_object_count=len(eligible),
        referenced_object_count=len(referenced),
        recent_object_count=recent_count,
        catalog_object_count=catalog_count,
        reference_surfaces=surfaces,
        orphans=orphans,
    )


def _finding_payload(finding: OrphanObjectFinding) -> dict[str, object]:
    return {
        "key": finding.key,
        "size": finding.size,
        "last_modified": finding.last_modified.isoformat(),
        "reason": finding.reason,
        "checked_references": list(finding.checked_references),
    }


def report_payload(report: OrphanScanReport) -> dict[str, object]:
    return {
        "mode": "dry-run-read-only",
        "bucket": report.bucket,
        "grace_hours": report.grace_hours,
        "scanned_object_count": report.scanned_object_count,
        "eligible_object_count": report.eligible_object_count,
        "referenced_object_count": report.referenced_object_count,
        "recent_object_count": report.recent_object_count,
        "catalog_object_count": report.catalog_object_count,
        "orphan_count": len(report.orphans),
        "reference_surfaces": [
            {
                "kind": surface.kind,
                "table": surface.table,
                "column": surface.column,
                "label": surface.label,
            }
            for surface in report.reference_surfaces
        ],
        "orphans": [_finding_payload(finding) for finding in report.orphans],
    }


def _nonnegative_hours(value: str) -> float:
    hours = float(value)
    if hours < 0:
        raise argparse.ArgumentTypeError("grace hours must be non-negative")
    return hours


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only scan for storage objects without database references."
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON report.")
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
    with SessionLocal() as db:
        report = scan_orphan_objects(
            db,
            storage=storage,
            grace_period=timedelta(hours=args.grace_hours),
        )
    payload = report_payload(report)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Orphan object scan (dry-run, read-only)")
        print(f"Scanned objects: {payload['scanned_object_count']}")
        print(f"Orphans: {payload['orphan_count']}")
        for finding in payload["orphans"]:
            print(json.dumps(finding, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
