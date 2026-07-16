import ast
from pathlib import Path

import pytest

import scripts.ops.scan_storage_keys as storage_key_scanner
from app.db.models import Asset, BgmLibraryTrack, VideoTask
from app.services.storage.base import StorageKeyError
from app.services.storage.keys import (
    get_tenant_storage_bytes,
    is_tenant_storage_key,
    validate_catalog_storage_key,
    validate_tenant_storage_key,
)
from app.services.storage.local import LocalObjectStorage
from scripts.ops.scan_storage_keys import scan_storage_keys

_APP_ROOT = Path(__file__).resolve().parents[1] / "app"
_STORAGE_BASE_PATH = _APP_ROOT / "services/storage/base.py"
_RAW_STORAGE_CALL_ALLOWLIST = {
    Path("services/storage/base.py"),
    Path("services/storage/keys.py"),
    Path("services/storage/local.py"),
    Path("services/storage/s3.py"),
}


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "tenants/{tenant}/../victim/output.png",
        "tenants/{tenant}/./images/output.png",
        "tenants/{tenant}//images/output.png",
        "tenants/{tenant}/images\\..\\victim.png",
        "/tenants/{tenant}/images/output.png",
        "tenants/{tenant}/%2e%2e/victim/output.png",
        "tenants/{tenant}/%252e%252e%252fvictim/output.png",
        "tenants/{tenant}/images/output.png\x00suffix",
        "tenants/another-tenant/images/output.png",
    ],
    ids=[
        "parent-segment",
        "current-segment",
        "repeated-separator",
        "backslash",
        "absolute",
        "encoded-parent",
        "double-encoded-parent",
        "nul",
        "wrong-tenant",
    ],
)
def test_tenant_storage_key_validator_rejects_noncanonical_paths(unsafe_key: str) -> None:
    tenant_id = "tenant-boundary"
    with pytest.raises(StorageKeyError):
        validate_tenant_storage_key(tenant_id, unsafe_key.format(tenant=tenant_id))


def test_tenant_storage_key_validator_accepts_canonical_owned_key() -> None:
    tenant_id = "tenant-boundary"
    key = f"tenants/{tenant_id}/images/output.png"
    assert validate_tenant_storage_key(tenant_id, key) == key
    assert is_tenant_storage_key(tenant_id, key) is True


def test_catalog_storage_key_validator_never_accepts_tenant_keys() -> None:
    assert validate_catalog_storage_key("platform/bgm/track.mp3") == (
        "platform/bgm/track.mp3"
    )
    assert validate_catalog_storage_key("library/bgm/legacy.wav") == (
        "library/bgm/legacy.wav"
    )
    with pytest.raises(StorageKeyError):
        validate_catalog_storage_key("tenants/tenant-boundary/images/output.png")


def test_tenant_get_bytes_boundary_blocks_real_local_storage_traversal(
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(str(tmp_path))
    tenant_id = "tenant-reader"
    victim_key = "tenants/tenant-victim/private/victim.png"
    traversal_key = f"tenants/{tenant_id}/../tenant-victim/private/victim.png"
    victim_bytes = b"cross-tenant-private-content"
    storage.put_bytes(victim_key, victim_bytes, content_type="image/png")

    with pytest.raises(StorageKeyError):
        get_tenant_storage_bytes(
            storage,
            tenant_id=tenant_id,
            storage_key=traversal_key,
        )

    assert storage.get_bytes(victim_key) == victim_bytes


def _object_storage_public_methods() -> set[str]:
    tree = ast.parse(
        _STORAGE_BASE_PATH.read_text(encoding="utf-8"),
        filename=str(_STORAGE_BASE_PATH),
    )
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ObjectStorage":
            methods = {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith("_")
            }
            if methods:
                return methods
    raise AssertionError("ObjectStorage public methods could not be discovered.")


def _raw_storage_call_violations(path: Path, method_names: set[str]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in method_names:
            violations.append(f"{path.relative_to(_APP_ROOT)}:{node.lineno}:{node.attr}")
            continue
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "getattr"):
            if not (
                isinstance(node.func, ast.Name)
                and node.func.id == "methodcaller"
            ):
                continue
            method_index = 0
        else:
            method_index = 1
        if len(node.args) <= method_index:
            continue
        method_arg = node.args[method_index]
        if isinstance(method_arg, ast.Constant) and method_arg.value in method_names:
            violations.append(
                f"{path.relative_to(_APP_ROOT)}:{node.lineno}:"
                f"{node.func.id}:{method_arg.value}"
            )
    return violations


def test_object_storage_methods_only_occur_in_validated_boundary() -> None:
    method_names = _object_storage_public_methods()
    violations: list[str] = []
    for path in _APP_ROOT.rglob("*.py"):
        relative = path.relative_to(_APP_ROOT)
        if relative in _RAW_STORAGE_CALL_ALLOWLIST:
            continue
        violations.extend(_raw_storage_call_violations(path, method_names))
    assert violations == []


def test_storage_key_scanner_reports_dirty_rows_without_modifying_them(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    traversal_key = f"tenants/{tenant_id}/../victim/output.png"
    mismatched_key = "tenants/another-tenant/images/output.png"
    safe_asset_id = "storage-scan-safe"
    dirty_asset_id = "storage-scan-prefix-mismatch"
    dirty_task_id = "storage-scan-noncanonical"
    with auth_db() as db:
        db.add_all(
            [
                Asset(
                    id=safe_asset_id,
                    tenant_id=tenant_id,
                    type="generated_image",
                    source="generated",
                    storage_key=f"tenants/{tenant_id}/images/safe.png",
                    status="ready",
                ),
                Asset(
                    id=dirty_asset_id,
                    tenant_id=tenant_id,
                    type="generated_image",
                    source="generated",
                    storage_key=mismatched_key,
                    status="ready",
                ),
                VideoTask(
                    id=dirty_task_id,
                    tenant_id=tenant_id,
                    mode="photo",
                    video_mode="photo",
                    status="done",
                    storage_key=traversal_key,
                ),
            ]
        )
        db.commit()

        findings = scan_storage_keys(db)

        assert {
            (finding.source, finding.row_id, finding.field, finding.reason)
            for finding in findings
        } == {
            (
                "video_tasks",
                dirty_task_id,
                "storage_key",
                "noncanonical_path",
            ),
            (
                "assets",
                dirty_asset_id,
                "storage_key",
                "tenant_prefix_mismatch",
            ),
        }
        assert db.get(Asset, safe_asset_id) is not None
        assert db.get(Asset, dirty_asset_id) is not None
        assert db.get(VideoTask, dirty_task_id) is not None


def test_storage_key_scanner_reports_platform_asset_and_bgm_catalog_keys(
    auth_db,
) -> None:
    platform_asset_id = "storage-scan-platform-asset"
    track_id = "storage-scan-bgm-track"
    with auth_db() as db:
        db.add_all(
            [
                Asset(
                    id=platform_asset_id,
                    tenant_id=None,
                    type="avatar_image",
                    source="preset",
                    storage_key="platform/../tenants/victim/private.png",
                    status="ready",
                ),
                BgmLibraryTrack(
                    track_id=track_id,
                    name="Storage scan track",
                    duration_sec=10,
                    storage_key="platform/bgm//dirty.mp3",
                    preview_storage_key="tenants/victim/private/preview.mp3",
                    license="Test license",
                ),
            ]
        )
        db.commit()

        findings = scan_storage_keys(db)

        assert {
            (finding.source, finding.row_id, finding.field, finding.reason)
            for finding in findings
        } == {
            (
                "assets",
                platform_asset_id,
                "storage_key",
                "noncanonical_path",
            ),
            (
                "bgm_library_tracks",
                track_id,
                "storage_key",
                "noncanonical_path",
            ),
            (
                "bgm_library_tracks",
                track_id,
                "preview_storage_key",
                "catalog_prefix_mismatch",
            ),
        }
        assert db.get(Asset, platform_asset_id) is not None
        assert db.get(BgmLibraryTrack, track_id) is not None


def test_storage_key_scanner_policy_covers_every_persisted_key_column() -> None:
    persisted_columns = storage_key_scanner.persisted_storage_key_columns()

    assert persisted_columns == {
        ("assets", "storage_key"),
        ("bgm_library_tracks", "preview_storage_key"),
        ("bgm_library_tracks", "storage_key"),
        ("brand_assets", "storage_key"),
        ("ecom_replicate_outputs", "storage_key"),
        ("reverse_prompt_jobs", "source_storage_key"),
        ("video_tasks", "storage_key"),
        ("video_tasks", "thumbnail_key"),
    }
    assert storage_key_scanner.configured_storage_key_columns() == persisted_columns
