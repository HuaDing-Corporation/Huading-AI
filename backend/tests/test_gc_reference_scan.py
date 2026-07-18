from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import pytest
from sqlalchemy import Column, MetaData, String, Table

from app.db.models import Base, GcCandidate, VideoTask
from app.services.gc.reference_scan import (
    APPROVED_PROTECTIVE_SCHEMA_FINGERPRINT,
    OrphanScanCoverageError,
    ReferenceClassification,
    StorageKeyScope,
    classify_storage_key,
    discover_reference_surfaces,
    find_referenced_keys,
    reference_schema_fingerprint,
)


def test_reference_discovery_classifies_legacy_business_and_gc_control_surfaces() -> None:
    surfaces = discover_reference_surfaces(Base.metadata)
    counts = Counter(surface.classification for surface in surfaces)

    assert counts == {
        ReferenceClassification.PROTECTIVE: 44,
        ReferenceClassification.GC_CONTROL: 5,
    }
    assert all(
        surface.classification is ReferenceClassification.GC_CONTROL
        for surface in surfaces
        if surface.table in {"gc_candidates", "gc_reclamation_jobs", "gc_audit_log"}
    )
    fingerprint = reference_schema_fingerprint(surfaces)
    assert len(fingerprint) == 64
    assert fingerprint == reference_schema_fingerprint(tuple(reversed(surfaces)))


def test_unclassified_locator_surface_fails_closed() -> None:
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    Table(
        "future_media",
        metadata,
        Column("id", String, primary_key=True),
        Column("download_url", String),
    )

    with pytest.raises(OrphanScanCoverageError, match="Unclassified"):
        discover_reference_surfaces(
            metadata,
            approved_protective_fingerprint=APPROVED_PROTECTIVE_SCHEMA_FINGERPRINT,
        )


def test_reference_scan_returns_per_key_evidence_and_ignores_gc_control_rows(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    referenced_key = f"tenants/{tenant_id}/uploads/referenced.png"
    candidate_key = f"tenants/{tenant_id}/uploads/candidate.png"
    now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)

    with auth_db() as db:
        db.add(
            VideoTask(
                id="gc-reference-video",
                tenant_id=tenant_id,
                mode="photo",
                video_mode="photo",
                params={"source_storage_key": referenced_key},
            )
        )
        db.add(
            GcCandidate(
                id="gc-control-candidate",
                tenant_id=tenant_id,
                status="observed",
                bucket="media",
                key=candidate_key,
                key_hash="a" * 64,
                first_seen_at=now,
                last_clean_scan_at=now,
                clean_scan_count=1,
                scan_id="gc-reference-scan",
                schema_fingerprint="b" * 64,
                evidence={},
                object_version_id="version-one",
                object_etag="etag-one",
                object_size=10,
                object_last_modified=now,
            )
        )
        db.commit()

        result = find_referenced_keys(
            db,
            object_keys={referenced_key, candidate_key},
            surfaces=discover_reference_surfaces(Base.metadata),
        )

    assert result.referenced_keys == frozenset({referenced_key})
    assert result.evidence_for(referenced_key)["matched_surfaces"] == [
        "json:video_tasks.params"
    ]
    assert result.evidence_for(candidate_key)["matched_surfaces"] == []
    assert "string:gc_candidates.key" not in result.checked_protective_surfaces


def test_reference_scan_fails_closed_for_cross_tenant_gc_control_key(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)

    with auth_db() as db:
        db.add(
            GcCandidate(
                id="cross-tenant-gc-control",
                tenant_id=tenant_id,
                status="observed",
                bucket="media",
                key="tenants/other-tenant/uploads/wrong.png",
                key_hash="a" * 64,
                first_seen_at=now,
                last_clean_scan_at=now,
                clean_scan_count=1,
                scan_id="cross-tenant-control-scan",
                schema_fingerprint="b" * 64,
                evidence={},
                object_version_id="version-one",
                object_etag="etag-one",
                object_size=10,
                object_last_modified=now,
            )
        )
        db.commit()

        with pytest.raises(OrphanScanCoverageError, match="GC control key validation"):
            find_referenced_keys(
                db,
                object_keys={f"tenants/{tenant_id}/uploads/candidate.png"},
                surfaces=discover_reference_surfaces(Base.metadata),
            )


@pytest.mark.parametrize(
    ("key", "expected_scope", "expected_reason"),
    [
        ("tenants/tenant-a/uploads/ok.png", StorageKeyScope.TENANT, None),
        ("platform/avatars/catalog.png", StorageKeyScope.CATALOG, "catalog_excluded"),
        ("library/bgm/track.mp3", StorageKeyScope.CATALOG, "catalog_excluded"),
        ("tenants/missing/uploads/nope.png", StorageKeyScope.INVALID, "tenant_unknown"),
        ("tenants/tenant-a/../tenant-b/nope.png", StorageKeyScope.INVALID, "invalid_key"),
        ("tenants\\tenant-a\\uploads\\nope.png", StorageKeyScope.INVALID, "invalid_key"),
        ("unknown/prefix/nope.png", StorageKeyScope.INVALID, "invalid_key"),
    ],
)
def test_storage_key_classification_is_tenant_only_and_fail_closed(
    key: str,
    expected_scope: StorageKeyScope,
    expected_reason: str | None,
) -> None:
    result = classify_storage_key(key, known_tenant_ids={"tenant-a"})

    assert result.scope is expected_scope
    assert result.skip_reason == expected_reason
    assert result.tenant_id == ("tenant-a" if expected_scope is StorageKeyScope.TENANT else None)


def test_storage_key_classification_rejects_cross_tenant_expectation() -> None:
    result = classify_storage_key(
        "tenants/tenant-b/uploads/cross.png",
        known_tenant_ids={"tenant-a", "tenant-b"},
        expected_tenant_id="tenant-a",
    )

    assert result.scope is StorageKeyScope.INVALID
    assert result.skip_reason == "invalid_key"
