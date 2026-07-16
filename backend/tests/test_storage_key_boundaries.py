import ast
from pathlib import Path

import pytest

import scripts.ops.scan_storage_keys as storage_key_scanner
from app.core.exceptions import AppError
from app.db.models import Asset, BgmLibraryTrack, VideoTask
from app.services.storage.base import StorageKeyError
from app.services.storage.keys import (
    get_tenant_storage_bytes,
    is_tenant_storage_key,
    presign_owned_storage_key,
    validate_catalog_storage_key,
    validate_tenant_storage_key,
)
from app.services.storage.local import LocalObjectStorage
from scripts.ops.scan_storage_keys import scan_storage_keys

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _BACKEND_ROOT / "app"
_SCRIPTS_ROOT = _BACKEND_ROOT / "scripts"
_STORAGE_SCAN_ROOTS = (_APP_ROOT, _SCRIPTS_ROOT)
_STORAGE_BASE_PATH = _APP_ROOT / "services/storage/base.py"
_RAW_STORAGE_CALL_ALLOWLIST = {
    Path("app/services/storage/base.py"),
    Path("app/services/storage/keys.py"),
    Path("app/services/storage/local.py"),
    Path("app/services/storage/s3.py"),
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


def test_owned_presign_rejects_asset_owned_by_another_tenant(tmp_path: Path) -> None:
    storage = LocalObjectStorage(str(tmp_path))

    with pytest.raises(AppError) as exc_info:
        presign_owned_storage_key(
            storage,
            tenant_id="tenant-requester",
            owner_tenant_id="tenant-owner",
            storage_key="tenants/tenant-owner/images/private.png",
            expires_in=60,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "STORAGE_OBJECT_NOT_FOUND"


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


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases = {
        "getattr": "builtins.getattr",
        "methodcaller": "operator.methodcaller",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local_name = imported.asname or imported.name.split(".", 1)[0]
                aliases[local_name] = imported.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for imported in node.names:
                local_name = imported.asname or imported.name
                aliases[local_name] = f"{node.module}.{imported.name}"
    return aliases


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


def _resolved_call_name(node: ast.expr, aliases: dict[str, str]) -> str | None:
    dotted_name = _dotted_name(node)
    if not dotted_name:
        return None
    head, separator, tail = dotted_name.partition(".")
    resolved_head = aliases.get(head, head)
    return f"{resolved_head}.{tail}" if separator else resolved_head


def _raw_storage_call_violations(
    path: Path,
    method_names: set[str],
    *,
    relative_to: Path = _APP_ROOT,
) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _import_aliases(tree)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in method_names:
            violations.append(f"{path.relative_to(relative_to)}:{node.lineno}:{node.attr}")
            continue
        if not isinstance(node, ast.Call):
            continue
        resolved_name = _resolved_call_name(node.func, aliases)
        method_index = {
            "builtins.getattr": 1,
            "operator.methodcaller": 0,
        }.get(resolved_name or "")
        if method_index is None:
            continue
        if len(node.args) <= method_index:
            continue
        method_arg = node.args[method_index]
        if isinstance(method_arg, ast.Constant) and method_arg.value in method_names:
            violations.append(
                f"{path.relative_to(relative_to)}:{node.lineno}:"
                f"{resolved_name}:{method_arg.value}"
            )
    return violations


@pytest.mark.parametrize(
    "source",
    [
        'import operator\noperator.methodcaller("get_bytes")\n',
        'from operator import methodcaller as mc\nmc("get_bytes")\n',
        'import builtins\nbuiltins.getattr(storage, "get_bytes")\n',
        'from builtins import getattr as lookup\nlookup(storage, "get_bytes")\n',
    ],
    ids=[
        "operator-attribute",
        "methodcaller-import-alias",
        "builtins-attribute",
        "getattr-import-alias",
    ],
)
def test_raw_storage_guard_resolves_common_indirect_calls(
    tmp_path: Path,
    source: str,
) -> None:
    fixture = tmp_path / "indirect_storage_call.py"
    fixture.write_text(source, encoding="utf-8")

    violations = _raw_storage_call_violations(
        fixture,
        {"get_bytes"},
        relative_to=tmp_path,
    )

    assert len(violations) == 1
    assert violations[0].endswith(":get_bytes")


def test_raw_storage_guard_scans_application_and_operational_scripts() -> None:
    assert _STORAGE_SCAN_ROOTS == (_APP_ROOT, _SCRIPTS_ROOT)


def test_object_storage_methods_only_occur_in_validated_boundary() -> None:
    method_names = _object_storage_public_methods()
    violations: list[str] = []
    missing_roots = [root for root in _STORAGE_SCAN_ROOTS if not root.is_dir()]
    assert missing_roots == []
    for root in _STORAGE_SCAN_ROOTS:
        for path in root.rglob("*.py"):
            relative = path.relative_to(_BACKEND_ROOT)
            if relative in _RAW_STORAGE_CALL_ALLOWLIST:
                continue
            violations.extend(
                _raw_storage_call_violations(
                    path,
                    method_names,
                    relative_to=_BACKEND_ROOT,
                )
            )
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
