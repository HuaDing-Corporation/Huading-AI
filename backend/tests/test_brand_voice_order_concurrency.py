from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path

import pytest

from app.services import brand_voice_orders


def test_manual_resolve_uses_the_canonical_global_lock_stages() -> None:
    source = inspect.getsource(brand_voice_orders._resolve_in_transaction)
    stages = [
        "lock_tenant_for_subscription_lifecycle",
        "_operation_for_update",
        "refund_subscriptions_for_update",
        "_usage_for_update",
        "decide_credit_refund",
        "_lock_provider_stage",
        "select(BrandVoiceOrder)",
    ]
    offsets = [source.index(stage) for stage in stages]
    assert offsets == sorted(offsets)


def test_provider_readiness_is_rechecked_inside_the_locked_stage_for_both_actions() -> None:
    stage_source = inspect.getsource(brand_voice_orders._lock_provider_stage)
    stage_offsets = [
        stage_source.index("lock_provider_voice_ids"),
        stage_source.index("select(BrandVoiceProviderId)"),
        stage_source.index("select(ProviderConfig)"),
        stage_source.index("assert_doubao_registry_ready"),
    ]
    assert stage_offsets == sorted(stage_offsets)

    resolve_source = inspect.getsource(brand_voice_orders._resolve_in_transaction)
    assert resolve_source.index("_lock_provider_stage") < resolve_source.index(
        'if action == "fulfill":'
    )


def test_fulfill_locks_business_rows_in_canonical_order() -> None:
    source = inspect.getsource(brand_voice_orders._resolve_in_transaction)
    offsets = [
        source.index("select(BrandVoiceOrder)"),
        source.index("voice = BrandVoice("),
        source.index("select(Asset)"),
    ]
    assert offsets == sorted(offsets)


def test_production_code_has_no_unprotected_user_deactivation_writer() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in {"is_active", "status"}
                    and isinstance(target.value, ast.Name)
                    and target.value.id in {"user", "locked_user", "current_user"}
                ):
                    offenders.append(f"{path.relative_to(app_root)}:{node.lineno}")
    assert offenders == []


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="isolated TEST_POSTGRES_URL is required for row-lock race coverage",
)
def test_postgresql_manual_order_races_run_only_against_isolated_database() -> None:
    url = os.environ["TEST_POSTGRES_URL"]
    assert "localhost" in url or "127.0.0.1" in url
    assert "huading_pricing_test_" in url
