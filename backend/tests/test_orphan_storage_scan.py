from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from sqlalchemy import Column, Integer, MetaData, String, Table

from app.db.models import Base, VideoTask
from app.services.storage.local import LocalObjectStorage
from scripts.ops.scan_orphan_objects import (
    OrphanScanCoverageError,
    discover_reference_surfaces,
    scan_orphan_objects,
    storage_inventory,
)

_NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
_OLD = _NOW - timedelta(days=2)


def _put_object(
    storage: LocalObjectStorage,
    *,
    key: str,
    content: bytes = b"fixture",
    modified_at: datetime = _OLD,
) -> None:
    storage.put_bytes(key, content, content_type="application/octet-stream")
    path = storage.root.joinpath(*key.split("/"))
    timestamp = modified_at.timestamp()
    os.utime(path, (timestamp, timestamp))


def _s3_storage(paginator: Mock) -> SimpleNamespace:
    client = Mock()
    client.get_paginator.return_value = paginator
    return SimpleNamespace(bucket="media", client=client)


def test_orphan_scan_reports_unreferenced_object_with_evidence(
    auth_db,
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(str(tmp_path))
    key = "tenants/unowned/uploads/orphan.bin"
    _put_object(storage, key=key, content=b"orphan-bytes")

    with auth_db() as db:
        report = scan_orphan_objects(
            db,
            storage=storage,
            grace_period=timedelta(hours=24),
            now=_NOW,
        )

    assert report.scanned_object_count == 1
    assert report.recent_object_count == 0
    assert report.catalog_object_count == 0
    assert len(report.orphans) == 1
    finding = report.orphans[0]
    assert finding.key == key
    assert finding.size == len(b"orphan-bytes")
    assert finding.last_modified == _OLD
    assert finding.reason == "no_database_reference"
    assert "json:video_tasks.params" in finding.checked_references
    assert "string:assets.storage_key" in finding.checked_references
    assert "asset_fk:task_assets.asset_id" in finding.checked_references
    assert storage.object_exists(key) is True


def test_orphan_scan_protects_object_referenced_only_from_json(
    auth_context,
    auth_db,
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(str(tmp_path))
    tenant_id = auth_context["tenant_id"]
    key = f"tenants/{tenant_id}/uploads/json-reference.png"
    _put_object(storage, key=key)

    with auth_db() as db:
        db.add(
            VideoTask(
                id="orphan-json-video",
                tenant_id=tenant_id,
                mode="photo",
                video_mode="photo",
                status="queued",
                params={"nested": {"source_storage_key": key}},
            )
        )
        db.commit()

        report = scan_orphan_objects(
            db,
            storage=storage,
            grace_period=timedelta(hours=24),
            now=_NOW,
        )

    assert report.orphans == ()
    assert report.referenced_object_count == 1


def test_orphan_scan_protects_unmanaged_catalog_object(
    auth_db,
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(str(tmp_path))
    _put_object(storage, key="platform/avatars/catalog.png")

    with auth_db() as db:
        report = scan_orphan_objects(
            db,
            storage=storage,
            grace_period=timedelta(hours=24),
            now=_NOW,
        )

    assert report.orphans == ()
    assert report.catalog_object_count == 1
    assert report.referenced_object_count == 0


def test_orphan_scan_grace_period_protects_recent_uncommitted_object(
    auth_db,
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(str(tmp_path))
    _put_object(
        storage,
        key="tenants/pending/uploads/in-flight.bin",
        modified_at=_NOW - timedelta(minutes=5),
    )

    with auth_db() as db:
        report = scan_orphan_objects(
            db,
            storage=storage,
            grace_period=timedelta(hours=24),
            now=_NOW,
        )

    assert report.orphans == ()
    assert report.recent_object_count == 1


def test_reference_discovery_covers_json_locator_and_asset_fk_surfaces() -> None:
    labels = {surface.label for surface in discover_reference_surfaces(Base.metadata)}

    assert "json:video_tasks.params" in labels
    assert "string:templates.path" in labels
    assert "string:voices.sample_url" in labels
    assert "string:bgm_library_tracks.preview_storage_key" in labels
    assert "asset_fk:brand_voices.source_audio_asset_id" in labels
    assert "asset_fk:brands.watermark_asset_id" in labels
    assert "asset_fk:ecom_replicate_outputs.reference_asset_id" in labels
    assert "asset_fk:ecom_replicate_outputs.product_asset_id" in labels
    assert "asset_fk:ecom_replicate_outputs.asset_id" in labels
    assert "asset_fk:task_assets.asset_id" in labels


def test_reference_discovery_fails_closed_for_unsupported_locator_type() -> None:
    metadata = MetaData()
    Table(
        "unsupported_objects",
        metadata,
        Column("id", String, primary_key=True),
        Column("result_storage_key", Integer),
    )

    with pytest.raises(
        OrphanScanCoverageError,
        match=r"unsupported_objects\.result_storage_key",
    ):
        discover_reference_surfaces(metadata)


def test_orphan_scan_fails_closed_when_storage_inventory_is_unavailable(
    auth_db,
) -> None:
    class UnsupportedStorage:
        bucket = "unsupported"

    with (
        auth_db() as db,
        pytest.raises(
            OrphanScanCoverageError,
            match="inventory",
        ),
    ):
        scan_orphan_objects(
            db,
            storage=UnsupportedStorage(),
            grace_period=timedelta(hours=24),
            now=_NOW,
        )


def test_orphan_scan_fails_closed_when_local_root_is_missing(
    auth_db,
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "not-mounted"
    storage = LocalObjectStorage(str(missing_root))
    assert not missing_root.exists()

    with (
        auth_db() as db,
        pytest.raises(
            OrphanScanCoverageError,
            match="does not exist",
        ),
    ):
        scan_orphan_objects(
            db,
            storage=storage,
            grace_period=timedelta(hours=24),
            now=_NOW,
        )


def test_s3_inventory_uses_every_list_objects_page(auth_db) -> None:
    first_key = "tenants/tenant-a/uploads/first.bin"
    second_key = "tenants/tenant-b/uploads/second.bin"

    class Paginator:
        def paginate(self, **kwargs):
            assert kwargs == {"Bucket": "media"}
            return [
                {
                    "Contents": [
                        {
                            "Key": first_key,
                            "Size": 5,
                            "LastModified": _OLD,
                        }
                    ]
                },
                {
                    "Contents": [
                        {
                            "Key": second_key,
                            "Size": 6,
                            "LastModified": _OLD,
                        }
                    ]
                },
            ]

    class Client:
        def get_paginator(self, operation_name: str):
            assert operation_name == "list_objects_v2"
            return Paginator()

    class S3Storage:
        bucket = "media"
        client = Client()

    with auth_db() as db:
        report = scan_orphan_objects(
            db,
            storage=S3Storage(),
            grace_period=timedelta(hours=24),
            now=_NOW,
        )

    assert report.scanned_object_count == 2
    assert [finding.key for finding in report.orphans] == [
        first_key,
        second_key,
    ]


def test_s3_inventory_accepts_successful_empty_bucket() -> None:
    paginator = Mock()
    paginator.paginate.return_value = [{"KeyCount": 0}]

    assert storage_inventory(_s3_storage(paginator)) == ()
    paginator.paginate.assert_called_once_with(Bucket="media")


@pytest.mark.parametrize(
    "failure",
    [
        ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "missing bucket"}},
            "ListObjectsV2",
        ),
        ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "forbidden"}},
            "ListObjectsV2",
        ),
        EndpointConnectionError(endpoint_url="https://storage.invalid"),
    ],
    ids=["missing-bucket", "access-denied", "endpoint-unreachable"],
)
def test_s3_inventory_fails_closed_when_listing_fails(failure: Exception) -> None:
    paginator = Mock()
    paginator.paginate.side_effect = failure

    with pytest.raises(
        OrphanScanCoverageError,
        match="S3 storage inventory could not be completed",
    ) as exc_info:
        storage_inventory(_s3_storage(paginator))

    assert exc_info.value.__cause__ is failure
