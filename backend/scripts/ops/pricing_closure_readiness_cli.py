"""Dependency-free, redacted operational boundary for pricing readiness."""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path

_MODES = (
    "bootstrap-empty-preflight",
    "preflight",
    "audit",
    "register-official",
)
_ARGUMENT_ERROR = {"error": "PRICING_READINESS_ARGUMENT_ERROR"}
_TARGET_MIGRATION_REVISION = "20260829_0038"
_SUPPORTED_PREFLIGHT_REVISIONS = {
    "20260724_0031",
    "20260804_0032",
    "20260805_0033",
    "20260805_0034",
    "20260806_0035",
    "20260829_0036",
    "20260829_0037",
    _TARGET_MIGRATION_REVISION,
}
_REPORT_ERROR = "PRICING_READINESS_FAILED"
_REGISTER_ERRORS = {
    "SCHEMA_NOT_READY",
    "OFFICIAL_REGISTRATION_ALREADY_COMPLETE",
    "OFFICIAL_REGISTRATION_ABORTED",
    "OFFICIAL_REGISTRATION_FAILED",
}
_PROVIDER_ID_BLOCKER_CODES = {
    "UNKNOWN_HISTORIC_DOUBAO_ID",
    "OFFICIAL_DOUBAO_CONFIG_INVALID",
    "OFFICIAL_DOUBAO_REGISTRY_MISMATCH",
    "OFFICIAL_DOUBAO_CUSTOMER_REGISTRY_CONFLICT",
    "RETIRED_OFFICIAL_DOUBAO_ID",
}
_BLOCKER_CODES = {
    "APPROVED_PLATFORM_RATE_MISMATCH",
    "BILLING_DUPLICATE_ACTIVE_SUBSCRIPTION",
    "BILLING_OPERATION_INVARIANT_FAILURE",
    "BILLING_REFUND_GRANT_INVARIANT_FAILURE",
    "BILLING_WALLET_INVARIANT_FAILURE",
    "DEFAULT_RATE_FALLBACK_MISMATCH",
    "DOUBAO_REGISTRY_INVARIANT_FAILURE",
    "DUPLICATE_ACTIVE_CREDIT_RATE",
    "EMPTY_BOOTSTRAP_DATABASE_NOT_EMPTY",
    "HISTORIC_DOUBAO_CONFIG_INVALID",
    "HISTORIC_DOUBAO_ID_NOT_NORMALIZED",
    "INVALID_CREDIT_RATE",
    "LEGACY_SLOT_WRITE_SURFACE",
    "MIGRATION_0032_RATE_MISMATCH",
    "MIGRATION_0032_TENANT_RATE_OVERRIDE",
    "MIGRATION_0035_RATE_MISMATCH",
    "MIGRATION_0035_TENANT_RATE_OVERRIDE",
    "MIGRATION_0037_ACTIVE_ZERO_RATE",
    "MIGRATION_0037_DUPLICATE_ACTIVE_RATE",
    "MIGRATION_0037_INVALID_BILLING_AMOUNT",
    "MIGRATION_0037_INVALID_RATE_AMOUNT",
    "MIGRATION_0037_INVALID_USAGE_AMOUNT",
    "MIGRATION_0037_PLATFORM_RATE_DRIFT",
    "MIGRATION_0037_SEED_ID_CONFLICT",
    "OFFICIAL_DOUBAO_CONFIG_INVALID",
    "OFFICIAL_DOUBAO_CONFIG_MISSING",
    "OFFICIAL_DOUBAO_CUSTOMER_REGISTRY_CONFLICT",
    "OFFICIAL_DOUBAO_REGISTRY_MISMATCH",
    "PREFLIGHT_DUPLICATE_ACTIVE_SUBSCRIPTION",
    "PREFLIGHT_RESERVED_USAGE_WALLET_MISSING",
    "PREFLIGHT_RESERVED_USAGE_WITHOUT_SUBSCRIPTION",
    "PREFLIGHT_SCHEMA_MISSING",
    "PREFLIGHT_WALLET_DOMAIN_FAILURE",
    "PREFLIGHT_WALLET_RESERVED_MISMATCH",
    "PRICING_POLICY_DEFAULT_MISMATCH",
    "PRODUCTION_ENVIRONMENT_REQUIRED",
    "RETIRED_OFFICIAL_DOUBAO_ID",
    "SCHEMA_NOT_READY",
    "UNKNOWN_HISTORIC_DOUBAO_ID",
    "UNSUPPORTED_SCHEMA_REVISION",
}
_PROVIDER_INVENTORY_COUNT_KEYS = {
    "official_configured_ids",
    "legacy_configured_ids",
    "provider_config_ids",
    "brand_voice_ids",
    "active_official_registry_ids",
    "retired_official_registry_ids",
    "active_customer_registry_ids",
    "retired_customer_registry_ids",
    "registry_blocker_ids",
}


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _help() -> None:
    sys.stdout.write(
        "Usage: pricing_closure_readiness_cli.py MODE\n\n"
        "Modes: bootstrap-empty-preflight, preflight, audit, register-official\n"
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate readiness output field")
        payload[key] = value
    return payload


def _reject_nonfinite_json(_value: str) -> object:
    raise ValueError("non-finite readiness output value")


def _captured_json_object(output: str) -> dict[str, object]:
    payload = json.loads(
        output,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite_json,
    )
    if not isinstance(payload, dict):
        raise ValueError("readiness output is not a JSON object")
    return payload


def _require_exact_keys(payload: dict[str, object], expected: set[str]) -> None:
    if set(payload) != expected:
        raise ValueError("readiness output fields are invalid")


def _strict_string(value: object) -> str:
    if type(value) is not str or not value:
        raise TypeError("readiness output string is invalid")
    return value


def _strict_optional_revision(value: object) -> str | None:
    if value is None:
        return None
    revision = _strict_string(value)
    if len(revision) > 64:
        raise ValueError("readiness revision is invalid")
    return revision


def _strict_nonnegative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise TypeError("readiness output integer is invalid")
    return value


def _strict_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("readiness output list is invalid")
    return [_strict_string(item) for item in value]


def _strict_timestamp(value: object) -> str:
    timestamp = _strict_string(value)
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("readiness timestamp must include a timezone")
    return timestamp


def _validated_blocker(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("readiness blocker is not an object")
    code = _strict_string(value.get("code"))
    if code not in _BLOCKER_CODES:
        raise ValueError("readiness blocker code is invalid")
    expected = {"code", "record_ids", "detail"}
    if code in _PROVIDER_ID_BLOCKER_CODES:
        expected.add("record_count")
    _require_exact_keys(value, expected)
    record_ids = _strict_string_list(value["record_ids"])
    blocker: dict[str, object] = {
        "code": code,
        "record_ids": record_ids,
        "detail": _strict_string(value["detail"]),
    }
    if code in _PROVIDER_ID_BLOCKER_CODES:
        if record_ids:
            raise ValueError("provider identifier blocker was not redacted")
        blocker["record_count"] = _strict_nonnegative_int(value["record_count"])
    return blocker


def _validated_blockers(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError("readiness blockers are not a list")
    return [_validated_blocker(item) for item in value]


def _validate_report_state(
    payload: dict[str, object],
    *,
    exit_code: int,
) -> tuple[bool, list[dict[str, object]]]:
    ready = payload["ready"]
    if type(ready) is not bool or payload["production_mode"] is not True:
        raise TypeError("readiness report state is invalid")
    blockers = _validated_blockers(payload["blockers"])
    expected_exit = 0 if ready and not blockers else 2 if not ready and blockers else None
    if exit_code != expected_exit:
        raise ValueError("readiness report does not match its exit code")
    return ready, blockers


def _validated_preflight_report(
    mode: str,
    exit_code: int,
    payload: dict[str, object],
) -> dict[str, object]:
    expected = {
        "ready",
        "production_mode",
        "migration_revision",
        "target_migration_revision",
        "generated_at",
        "blockers",
    }
    _require_exact_keys(payload, expected)
    ready, blockers = _validate_report_state(payload, exit_code=exit_code)
    migration_revision = _strict_optional_revision(payload["migration_revision"])
    if mode == "bootstrap-empty-preflight" and migration_revision is not None:
        raise ValueError("bootstrap readiness revision is invalid")
    if mode == "preflight" and migration_revision not in _SUPPORTED_PREFLIGHT_REVISIONS:
        raise ValueError("preflight readiness revision is invalid")
    if payload["target_migration_revision"] != _TARGET_MIGRATION_REVISION:
        raise ValueError("readiness target revision is invalid")
    return {
        "ready": ready,
        "production_mode": True,
        "migration_revision": migration_revision,
        "target_migration_revision": _TARGET_MIGRATION_REVISION,
        "generated_at": _strict_timestamp(payload["generated_at"]),
        "blockers": blockers,
    }


def _validated_rate(value: object, *, scope: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("readiness rate is not an object")
    _require_exact_keys(
        value,
        {"id", "scope", "tenant_id", "capability", "unit", "credits_per_unit", "active"},
    )
    if value["scope"] != scope or type(value["active"]) is not bool:
        raise ValueError("readiness rate scope is invalid")
    tenant_id = value["tenant_id"]
    if scope == "platform":
        if tenant_id is not None:
            raise ValueError("platform rate tenant is invalid")
    else:
        tenant_id = _strict_string(tenant_id)
    return {
        "id": _strict_string(value["id"]),
        "scope": scope,
        "tenant_id": tenant_id,
        "capability": _strict_string(value["capability"]),
        "unit": _strict_string(value["unit"]),
        "credits_per_unit": _strict_string(value["credits_per_unit"]),
        "active": value["active"],
    }


def _validated_rates(value: object, *, scope: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError("readiness rates are not a list")
    return [_validated_rate(item, scope=scope) for item in value]


def _validated_inventory_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise TypeError("readiness inventory counts are not an object")
    _require_exact_keys(value, _PROVIDER_INVENTORY_COUNT_KEYS)
    return {key: _strict_nonnegative_int(value[key]) for key in sorted(value)}


def _validated_surfaces(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise TypeError("readiness surfaces are not a list")
    surfaces: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("readiness surface is not an object")
        _require_exact_keys(item, {"surface", "state"})
        state = item["state"]
        if state not in {"active", "retired"}:
            raise ValueError("readiness surface state is invalid")
        surfaces.append({"surface": _strict_string(item["surface"]), "state": state})
    return surfaces


def _validated_audit_report(
    exit_code: int,
    payload: dict[str, object],
) -> dict[str, object]:
    expected = {
        "ready",
        "production_mode",
        "migration_revision",
        "generated_at",
        "platform_rates",
        "tenant_rates",
        "platform_rate_ids",
        "tenant_rate_ids",
        "inventory_unknown_count",
        "provider_inventory_counts",
        "legacy_slot_write_surfaces",
        "blockers",
    }
    _require_exact_keys(payload, expected)
    ready, blockers = _validate_report_state(payload, exit_code=exit_code)
    revision = _strict_optional_revision(payload["migration_revision"])
    if exit_code == 0 and revision != _TARGET_MIGRATION_REVISION:
        raise ValueError("successful audit revision is invalid")
    platform_rates = _validated_rates(payload["platform_rates"], scope="platform")
    tenant_rates = _validated_rates(payload["tenant_rates"], scope="tenant")
    platform_rate_ids = _strict_string_list(payload["platform_rate_ids"])
    tenant_rate_ids = _strict_string_list(payload["tenant_rate_ids"])
    if platform_rate_ids != [rate["id"] for rate in platform_rates]:
        raise ValueError("platform rate identifiers are inconsistent")
    if tenant_rate_ids != [rate["id"] for rate in tenant_rates]:
        raise ValueError("tenant rate identifiers are inconsistent")
    unknown_count = _strict_nonnegative_int(payload["inventory_unknown_count"])
    active_surfaces = {
        item["surface"]
        for item in _validated_surfaces(payload["legacy_slot_write_surfaces"])
        if item["state"] == "active"
    }
    surface_blockers = {
        record_id
        for blocker in blockers
        if blocker["code"] == "LEGACY_SLOT_WRITE_SURFACE"
        for record_id in blocker["record_ids"]
    }
    unknown_blockers = [
        blocker for blocker in blockers if blocker["code"] == "UNKNOWN_HISTORIC_DOUBAO_ID"
    ]
    if unknown_count:
        if ready or len(unknown_blockers) != 1:
            raise ValueError("unknown inventory evidence is inconsistent")
        if unknown_blockers[0].get("record_count") != unknown_count:
            raise ValueError("unknown inventory count is inconsistent")
    elif unknown_blockers:
        raise ValueError("unknown inventory blocker is inconsistent")
    if active_surfaces:
        if ready or surface_blockers != active_surfaces:
            raise ValueError("legacy surface evidence is inconsistent")
    elif surface_blockers:
        raise ValueError("legacy surface blocker is inconsistent")
    return {
        "ready": ready,
        "production_mode": True,
        "migration_revision": revision,
        "generated_at": _strict_timestamp(payload["generated_at"]),
        "platform_rates": platform_rates,
        "tenant_rates": tenant_rates,
        "platform_rate_ids": platform_rate_ids,
        "tenant_rate_ids": tenant_rate_ids,
        "inventory_unknown_count": unknown_count,
        "provider_inventory_counts": _validated_inventory_counts(
            payload["provider_inventory_counts"]
        ),
        "legacy_slot_write_surfaces": _validated_surfaces(payload["legacy_slot_write_surfaces"]),
        "blockers": blockers,
    }


def _validated_report_error(exit_code: int, payload: dict[str, object]) -> dict[str, object]:
    _require_exact_keys(payload, {"error"})
    if exit_code != 2 or payload["error"] != _REPORT_ERROR:
        raise ValueError("readiness error payload is invalid")
    return {"error": _REPORT_ERROR}


def _validated_register_payload(
    exit_code: int,
    payload: dict[str, object],
) -> dict[str, object]:
    if "error" not in payload:
        _require_exact_keys(payload, {"registered_official_count"})
        count = _strict_nonnegative_int(payload["registered_official_count"])
        if exit_code != 0 or not 1 <= count <= 64:
            raise ValueError("official registration result is invalid")
        return {"registered_official_count": count}
    error = _strict_string(payload["error"])
    if exit_code != 2 or error not in _REGISTER_ERRORS:
        raise ValueError("official registration error is invalid")
    if error == "SCHEMA_NOT_READY":
        _require_exact_keys(
            payload,
            {"error", "migration_revision", "target_migration_revision"},
        )
        revision = _strict_optional_revision(payload["migration_revision"])
        if (
            payload["target_migration_revision"] != _TARGET_MIGRATION_REVISION
            or revision == _TARGET_MIGRATION_REVISION
        ):
            raise ValueError("schema readiness revisions are invalid")
        return {
            "error": error,
            "migration_revision": revision,
            "target_migration_revision": _TARGET_MIGRATION_REVISION,
        }
    _require_exact_keys(payload, {"error"})
    return {"error": error}


def _validated_payload(
    mode: str,
    exit_code: object,
    payload: dict[str, object],
) -> tuple[int, dict[str, object]]:
    if type(exit_code) is not int or exit_code not in {0, 2}:
        raise TypeError("readiness exit code is invalid")
    if mode == "register-official":
        return exit_code, _validated_register_payload(exit_code, payload)
    if "error" in payload:
        return exit_code, _validated_report_error(exit_code, payload)
    if mode == "audit":
        return exit_code, _validated_audit_report(exit_code, payload)
    return exit_code, _validated_preflight_report(mode, exit_code, payload)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--help"], ["-h"]):
        _help()
        return 0
    if len(args) != 1 or args[0] not in _MODES:
        _emit(_ARGUMENT_ERROR)
        return 64

    mode = args[0]
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()
    try:
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            backend_root = str(Path(__file__).resolve().parents[2])
            if backend_root not in sys.path:
                sys.path.insert(0, backend_root)
            from scripts.ops import pricing_closure_readiness

            exit_code = pricing_closure_readiness.main([mode])
        if stderr_buffer.getvalue():
            raise ValueError("readiness command wrote to stderr")
        payload = _captured_json_object(stdout_buffer.getvalue())
        exit_code, payload = _validated_payload(mode, exit_code, payload)
    except BaseException:
        payload = {
            "error": (
                "OFFICIAL_REGISTRATION_FAILED"
                if mode == "register-official"
                else "PRICING_READINESS_FAILED"
            )
        }
        exit_code = 2

    _emit(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
