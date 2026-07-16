import ast
from pathlib import Path

import pytest

from app.db.models import Asset, VideoTask
from app.services.storage.base import StorageKeyError
from app.services.storage.keys import (
    is_tenant_storage_key,
    validate_catalog_storage_key,
    validate_tenant_storage_key,
)
from scripts.ops.scan_storage_keys import scan_storage_keys

_APP_ROOT = Path(__file__).resolve().parents[1] / "app"
_FORBIDDEN_STORAGE_METHODS = {"delete_object", "presign_get_url"}
_RAW_STORAGE_CALL_ALLOWLIST = {
    Path("services/storage/keys.py"),
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


def _raw_storage_call_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in _FORBIDDEN_STORAGE_METHODS
        ):
            violations.append(f"{path.relative_to(_APP_ROOT)}:{node.lineno}:{node.func.attr}")
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "getattr"):
            continue
        if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
            continue
        method_name = node.args[1].value
        if method_name in _FORBIDDEN_STORAGE_METHODS:
            violations.append(f"{path.relative_to(_APP_ROOT)}:{node.lineno}:getattr:{method_name}")
    return violations


def test_storage_delete_and_presign_only_occur_in_validated_boundary() -> None:
    violations: list[str] = []
    for path in _APP_ROOT.rglob("*.py"):
        relative = path.relative_to(_APP_ROOT)
        if relative in _RAW_STORAGE_CALL_ALLOWLIST:
            continue
        violations.extend(_raw_storage_call_violations(path))
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
