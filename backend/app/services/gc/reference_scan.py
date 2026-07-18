from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

from sqlalchemy import JSON, String, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import MetaData

from app.db.models import Asset, Base, GcSkipReason
from app.services.storage.base import StorageKeyError
from app.services.storage.keys import (
    validate_catalog_storage_key,
    validate_tenant_storage_key,
)

# This digest approves the 44 reflected business reference surfaces reviewed in
# GC-DELETE-SPIKE-0001. It is deliberately opaque rather than a second field list:
# schema changes must update the source model and explicitly re-approve this digest.
APPROVED_PROTECTIVE_SCHEMA_FINGERPRINT = (
    "15d4fdc15c47ab05b63022432c2c11bd09b92a1b04a2f1acbf5639b2fefc3d0b"
)


class OrphanScanCoverageError(RuntimeError):
    """Raised when complete, read-only reference coverage cannot be proven."""


class ReferenceClassification(StrEnum):
    PROTECTIVE = "protective"
    GC_CONTROL = "gc_control"
    UNCLASSIFIED = "unclassified"


class StorageKeyScope(StrEnum):
    TENANT = "tenant"
    CATALOG = "catalog"
    INVALID = "invalid"


ReferenceKind = Literal["string", "json", "asset_fk"]


@dataclass(frozen=True)
class ReferenceSurface:
    table: str
    column: str
    kind: ReferenceKind
    classification: ReferenceClassification = ReferenceClassification.UNCLASSIFIED

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.table}.{self.column}"

    @property
    def fingerprint_label(self) -> str:
        return f"{self.classification.value}:{self.label}"


@dataclass(frozen=True)
class ReferenceScanResult:
    referenced_keys: frozenset[str]
    checked_protective_surfaces: tuple[str, ...]
    schema_fingerprint: str
    matches_by_key: Mapping[str, tuple[str, ...]]

    def evidence_for(self, key: str) -> dict[str, object]:
        return {
            "schema_fingerprint": self.schema_fingerprint,
            "checked_surfaces": list(self.checked_protective_surfaces),
            "matched_surfaces": list(self.matches_by_key.get(key, ())),
        }


@dataclass(frozen=True)
class StorageKeyClassification:
    scope: StorageKeyScope
    tenant_id: str | None
    skip_reason: GcSkipReason | None


def _looks_like_storage_locator(column_name: str) -> bool:
    name = column_name.lower()
    return name in {"key", "path", "url"} or name.endswith(("_key", "_path", "_url"))


def classify_storage_key(
    storage_key: str | None,
    *,
    known_tenant_ids: set[str],
    expected_tenant_id: str | None = None,
) -> StorageKeyClassification:
    try:
        validate_catalog_storage_key(storage_key)
    except StorageKeyError:
        pass
    else:
        return StorageKeyClassification(
            StorageKeyScope.CATALOG,
            None,
            GcSkipReason.CATALOG_EXCLUDED,
        )

    value = str(storage_key or "")
    parts = value.split("/")
    if len(parts) < 3 or parts[0] != "tenants" or not parts[1]:
        return StorageKeyClassification(
            StorageKeyScope.INVALID,
            None,
            GcSkipReason.INVALID_KEY,
        )
    tenant_id = parts[1]
    try:
        validate_tenant_storage_key(tenant_id, value)
    except StorageKeyError:
        return StorageKeyClassification(
            StorageKeyScope.INVALID,
            None,
            GcSkipReason.INVALID_KEY,
        )
    if expected_tenant_id is not None and tenant_id != expected_tenant_id:
        return StorageKeyClassification(
            StorageKeyScope.INVALID,
            None,
            GcSkipReason.INVALID_KEY,
        )
    if tenant_id not in known_tenant_ids:
        return StorageKeyClassification(
            StorageKeyScope.INVALID,
            None,
            GcSkipReason.TENANT_UNKNOWN,
        )
    return StorageKeyClassification(StorageKeyScope.TENANT, tenant_id, None)


def _labels_fingerprint(surfaces: list[ReferenceSurface]) -> str:
    payload = json.dumps(
        sorted(surface.label for surface in surfaces),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _explicit_classification(table, column) -> ReferenceClassification | None:
    raw = column.info.get("gc_reference_class") or table.info.get("gc_reference_class")
    if raw is None:
        return None
    try:
        return ReferenceClassification(str(raw))
    except ValueError as exc:
        raise OrphanScanCoverageError(
            f"Invalid GC reference classification for {table.name}.{column.name}: {raw!r}."
        ) from exc


def discover_reference_surfaces(
    metadata: MetaData,
    *,
    approved_protective_fingerprint: str | None = None,
    strict: bool = True,
) -> tuple[ReferenceSurface, ...]:
    surfaces: list[ReferenceSurface] = []
    implicit_business: list[ReferenceSurface] = []
    unsupported: list[str] = []
    for table in sorted(metadata.tables.values(), key=lambda item: item.name):
        for column in table.columns:
            discovered: list[ReferenceSurface] = []
            locator = _looks_like_storage_locator(column.name)
            if locator and not isinstance(column.type, String):
                unsupported.append(f"{table.name}.{column.name}")
            elif locator:
                discovered.append(ReferenceSurface(table.name, column.name, "string"))

            if isinstance(column.type, JSON):
                discovered.append(ReferenceSurface(table.name, column.name, "json"))

            if any(
                foreign_key.target_fullname == "assets.id" for foreign_key in column.foreign_keys
            ):
                if not isinstance(column.type, String):
                    unsupported.append(f"{table.name}.{column.name}")
                else:
                    discovered.append(ReferenceSurface(table.name, column.name, "asset_fk"))

            classification = _explicit_classification(table, column)
            for surface in discovered:
                if classification is None:
                    implicit_business.append(surface)
                    surfaces.append(surface)
                else:
                    surfaces.append(replace(surface, classification=classification))

    if unsupported:
        raise OrphanScanCoverageError(
            f"Unsupported reflected storage-reference column types: {sorted(set(unsupported))!r}."
        )
    if not surfaces:
        raise OrphanScanCoverageError(
            "No database reference surfaces were discovered; refusing to report clean."
        )

    approved = approved_protective_fingerprint
    if approved is None and metadata is Base.metadata:
        approved = APPROVED_PROTECTIVE_SCHEMA_FINGERPRINT
    if approved is not None and _labels_fingerprint(implicit_business) == approved:
        surfaces = [
            replace(surface, classification=ReferenceClassification.PROTECTIVE)
            if surface.classification is ReferenceClassification.UNCLASSIFIED
            else surface
            for surface in surfaces
        ]

    ordered = tuple(sorted(set(surfaces), key=lambda surface: surface.fingerprint_label))
    unclassified = [
        surface.label
        for surface in ordered
        if surface.classification is ReferenceClassification.UNCLASSIFIED
    ]
    if strict and unclassified:
        raise OrphanScanCoverageError(
            "Unclassified storage-reference surfaces changed the approved coverage: "
            f"{unclassified!r}."
        )
    return ordered


def reference_schema_fingerprint(surfaces: tuple[ReferenceSurface, ...]) -> str:
    payload = json.dumps(
        sorted(surface.fingerprint_label for surface in surfaces),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def _group_surfaces(
    surfaces: Iterable[ReferenceSurface],
) -> dict[str, list[ReferenceSurface]]:
    grouped: dict[str, list[ReferenceSurface]] = defaultdict(list)
    for surface in surfaces:
        grouped[surface.table].append(surface)
    return grouped


def _validate_gc_control_surfaces(
    db: Session,
    *,
    object_keys: set[str],
    surfaces: Iterable[ReferenceSurface],
) -> None:
    for table_name, table_surfaces in sorted(_group_surfaces(surfaces).items()):
        table = Base.metadata.tables[table_name]
        tenant_column = table.c.get("tenant_id")
        if tenant_column is None:
            raise OrphanScanCoverageError(
                f"GC control table {table_name!r} has no tenant_id for key validation."
            )
        columns = [tenant_column, *(table.c[surface.column] for surface in table_surfaces)]
        try:
            rows = db.execute(select(*columns).execution_options(yield_per=500))
            for row in rows:
                tenant_id = str(row[0] or "")
                for surface, value in zip(table_surfaces, row[1:], strict=True):
                    keys = (
                        {value}
                        if surface.kind == "string" and isinstance(value, str)
                        else _json_references(
                            value,
                            object_keys=object_keys,
                            asset_keys_by_id={},
                        )
                    )
                    for key in keys:
                        try:
                            validate_tenant_storage_key(tenant_id, key)
                        except StorageKeyError as exc:
                            raise OrphanScanCoverageError(
                                f"GC control key validation failed for {surface.label}."
                            ) from exc
        except OrphanScanCoverageError:
            raise
        except Exception as exc:
            raise OrphanScanCoverageError(
                f"GC control scan failed for table {table_name!r}."
            ) from exc


def find_referenced_keys(
    db: Session,
    *,
    object_keys: set[str],
    surfaces: Iterable[ReferenceSurface],
    control_surfaces: Iterable[ReferenceSurface] | None = None,
) -> ReferenceScanResult:
    supplied_controls = tuple(control_surfaces or ())
    all_surfaces = tuple(
        sorted(
            {*surfaces, *supplied_controls},
            key=lambda surface: surface.fingerprint_label,
        )
    )
    if any(
        surface.classification is ReferenceClassification.UNCLASSIFIED
        for surface in all_surfaces
    ):
        raise OrphanScanCoverageError("Unclassified reference surfaces cannot be scanned.")

    protective = tuple(
        surface
        for surface in all_surfaces
        if surface.classification is ReferenceClassification.PROTECTIVE
    )
    controls = supplied_controls or tuple(
        surface
        for surface in all_surfaces
        if surface.classification is ReferenceClassification.GC_CONTROL
    )
    fingerprint = reference_schema_fingerprint(all_surfaces)
    checked = tuple(sorted(surface.label for surface in protective))
    matches: dict[str, set[str]] = {key: set() for key in object_keys}
    _validate_gc_control_surfaces(
        db,
        object_keys=object_keys,
        surfaces=controls,
    )
    if not object_keys:
        return ReferenceScanResult(frozenset(), checked, fingerprint, {})
    asset_keys_by_id = _asset_keys_by_id(db, object_keys)
    for table_name, table_surfaces in sorted(_group_surfaces(protective).items()):
        table = Base.metadata.tables[table_name]
        columns = [table.c[surface.column] for surface in table_surfaces]
        try:
            rows = db.execute(select(*columns).execution_options(yield_per=500))
            for row in rows:
                for surface, value in zip(table_surfaces, row, strict=True):
                    if surface.kind == "string":
                        referenced = (
                            {value}
                            if isinstance(value, str) and value in object_keys
                            else set()
                        )
                    elif surface.kind == "asset_fk":
                        asset_key = asset_keys_by_id.get(str(value or ""))
                        referenced = {asset_key} if asset_key is not None else set()
                    else:
                        referenced = _json_references(
                            value,
                            object_keys=object_keys,
                            asset_keys_by_id=asset_keys_by_id,
                        )
                    for key in referenced:
                        matches[key].add(surface.label)
        except Exception as exc:
            raise OrphanScanCoverageError(
                f"Database reference scan failed for table {table_name!r}."
            ) from exc

    normalized_matches = {
        key: tuple(sorted(labels)) for key, labels in matches.items() if labels
    }
    return ReferenceScanResult(
        referenced_keys=frozenset(normalized_matches),
        checked_protective_surfaces=checked,
        schema_fingerprint=fingerprint,
        matches_by_key=normalized_matches,
    )
