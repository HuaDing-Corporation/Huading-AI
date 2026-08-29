from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.db.models import BillingOperation, BrandVoice, CreditRate, ProviderConfig, Subscription


def _session_scope(session):
    @contextmanager
    def scope():
        yield session

    return scope


def test_readiness_fails_on_unknown_historic_doubao_id(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    db_session.add(
        ProviderConfig(
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={"used_speaker_ids": {"unknown-id": "historic"}},
            is_active=False,
        )
    )
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=True)

    assert report.ready is False
    assert report.blockers[0].code == "UNKNOWN_HISTORIC_DOUBAO_ID"
    assert report.blockers[0].record_ids == ("unknown-id",)


def test_readiness_audit_is_read_only_and_redacts_provider_config(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    config = ProviderConfig(
        capability="voice_clone",
        provider="doubao-voice-clone",
        config={"api_key": "must-never-be-disclosed", "speaker_ids": ["legacy-id"]},
        is_active=False,
    )
    voice = BrandVoice(
        tenant_id="tenant-a",
        owner_user_id="user-a",
        name="Cosy",
        provider="cosyvoice-voice-clone",
        speaker_id="cosy-not-doubao",
        status="ready",
        consent_confirmed=True,
    )
    db_session.add_all([config, voice])
    db_session.commit()
    before = (
        len(db_session.scalars(select(ProviderConfig)).all()),
        len(db_session.scalars(select(BrandVoice)).all()),
    )

    report = pricing_closure_readiness(db_session, production_mode=False)
    payload = report.as_dict()

    after = (
        len(db_session.scalars(select(ProviderConfig)).all()),
        len(db_session.scalars(select(BrandVoice)).all()),
    )
    assert after == before
    assert "must-never-be-disclosed" not in str(payload)
    assert "cosy-not-doubao" not in {
        item.provider_voice_id for item in report.inventory_unknown_ids
    }


def test_readiness_reports_invalid_and_duplicate_active_rates_with_record_ids(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    # Historical databases can contain duplicates that modern partial indexes prevent.
    db_session.execute(text("DROP INDEX uq_credit_rates_platform_active_capability_unit"))
    db_session.add_all(
        [
            CreditRate(
                id="zero-rate",
                capability="image",
                unit="image",
                credits_per_unit=Decimal("0"),
                is_active=True,
            ),
            CreditRate(
                id="duplicate-rate-a",
                capability="avatar",
                unit="second",
                credits_per_unit=Decimal("1"),
                is_active=True,
            ),
            CreditRate(
                id="duplicate-rate-b",
                capability="avatar",
                unit="second",
                credits_per_unit=Decimal("2"),
                is_active=True,
            ),
        ]
    )
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=True)

    assert {(item.code, item.record_ids) for item in report.blockers} >= {
        ("INVALID_CREDIT_RATE", ("zero-rate",)),
        ("DUPLICATE_ACTIVE_CREDIT_RATE", ("duplicate-rate-a", "duplicate-rate-b")),
    }


def test_readiness_reports_official_config_registry_mismatch_and_retired_slot_surface(
    db_session,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["official-only"],
    )

    report = pricing_closure_readiness(db_session, production_mode=True)

    assert ("OFFICIAL_DOUBAO_REGISTRY_MISMATCH", ("official-only",)) in {
        (item.code, item.record_ids) for item in report.blockers
    }
    assert [
        (item.surface, item.state) for item in report.legacy_slot_write_surfaces
    ] == [("admin_api:POST /tenants/{tenant_id}/voice-slots", "retired")]


def test_readiness_fails_closed_when_policy_default_or_fallback_drifts(
    db_session,
    monkeypatch,
) -> None:
    from app.services import pricing
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    monkeypatch.setitem(
        pricing.PRICING_POLICIES,
        "script_generate",
        replace(pricing.PRICING_POLICIES["script_generate"], default_unit_credits=Decimal("2")),
    )
    monkeypatch.setitem(pricing.DEFAULT_RATE_CREDITS, ("avatar", "second"), Decimal("999"))

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert ("PRICING_POLICY_DEFAULT_MISMATCH", ("script_generate",)) in {
        (item.code, item.record_ids) for item in report.blockers
    }
    assert ("DEFAULT_RATE_FALLBACK_MISMATCH", ("avatar/second",)) in {
        (item.code, item.record_ids) for item in report.blockers
    }


def test_readiness_lists_safe_platform_and_tenant_rate_rows(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    db_session.add_all(
        [
            CreditRate(
                id="platform-rate-row",
                capability="image",
                unit="image",
                credits_per_unit=Decimal("1"),
                is_active=True,
            ),
            CreditRate(
                id="tenant-rate-row",
                tenant_id="tenant-a",
                capability="scene_prompt",
                unit="call",
                credits_per_unit=Decimal("2"),
                is_active=True,
            ),
        ]
    )
    db_session.commit()

    payload = pricing_closure_readiness(db_session, production_mode=False).as_dict()

    assert payload["platform_rates"] == [
        {
            "id": "platform-rate-row",
            "scope": "platform",
            "tenant_id": None,
            "capability": "image",
            "unit": "image",
            "credits_per_unit": "1.0000",
            "active": True,
        }
    ]
    assert payload["tenant_rates"] == [
        {
            "id": "tenant-rate-row",
            "scope": "tenant",
            "tenant_id": "tenant-a",
            "capability": "scene_prompt",
            "unit": "call",
            "credits_per_unit": "2.0000",
            "active": True,
        }
    ]


def test_readiness_cli_default_audit_emits_json_and_rolls_back(
    db_session, monkeypatch, capsys
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    db_session.add(
        ProviderConfig(
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={"api_key": "cli-audit-secret"},
            is_active=False,
        )
    )
    db_session.commit()
    before = db_session.scalar(select(CreditRate.id))
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))

    exit_code = readiness.main([])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code in {0, 2}
    assert payload["production_mode"] is True
    assert "cli-audit-secret" not in json.dumps(payload)
    assert db_session.scalar(select(CreditRate.id)) == before


def test_readiness_cli_unknown_inventory_aborts_without_mutation(
    db_session,
    monkeypatch,
    capsys,
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    db_session.add(
        ProviderConfig(
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={"used_speaker_ids": {"unknown-cli-id": "historic"}},
            is_active=False,
        )
    )
    db_session.commit()
    before = len(db_session.scalars(select(ProviderConfig)).all())
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))

    exit_code = readiness.main(["register-official"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["unknown_ids"] == ["unknown-cli-id"]
    assert len(db_session.scalars(select(ProviderConfig)).all()) == before


@pytest.mark.parametrize(
    "rate_id, value",
    [("historic-negative-rate", "-1"), ("historic-nan-rate", "NaN")],
)
def test_readiness_blocks_historically_corrupt_rate_values(
    db_session, rate_id: str, value: str
) -> None:
    """SQLite's check bypass models a legacy row written before current constraints."""
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    db_session.execute(text("PRAGMA ignore_check_constraints = ON"))
    db_session.execute(
        text(
            "INSERT INTO credit_rates "
            "(id, capability, unit, credits_per_unit, is_active, effective_at) "
            "VALUES (:id, 'image', 'image', :value, 0, CURRENT_TIMESTAMP)"
        ),
        {"id": rate_id, "value": value},
    )
    db_session.execute(text("PRAGMA ignore_check_constraints = OFF"))
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=True)

    assert ("INVALID_CREDIT_RATE", (rate_id,)) in {
        (blocker.code, blocker.record_ids) for blocker in report.blockers
    }


def test_readiness_reports_persisted_operation_and_wallet_invariants_without_repair(
    db_session,
) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    operation = BillingOperation(
        id="corrupt-operation",
        tenant_id="tenant-a",
        user_id="user-a",
        operation="video_create",
        idempotency_key="corrupt-operation",
        request_hash="a" * 64,
        quote_hash="b" * 64,
        pricing_snapshot={},
        requested_credits=Decimal("0"),
        settled_credits=Decimal("0"),
        released_credits=Decimal("0"),
        status="in_progress",
    )
    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_reserved = 7
    db_session.add(operation)
    db_session.execute(text("PRAGMA ignore_check_constraints = ON"))
    db_session.commit()
    db_session.execute(text("PRAGMA ignore_check_constraints = OFF"))
    before = (
        operation.status,
        operation.pricing_snapshot,
        subscription.quota_credits_reserved,
    )

    report = pricing_closure_readiness(db_session, production_mode=True)

    assert report.ready is False
    assert {
        (blocker.code, blocker.record_ids) for blocker in report.blockers
    } >= {
        ("BILLING_OPERATION_INVARIANT_FAILURE", (operation.id,)),
        ("BILLING_WALLET_INVARIANT_FAILURE", (subscription.id,)),
    }
    db_session.expire_all()
    after_operation = db_session.get(BillingOperation, operation.id)
    after_subscription = db_session.get(Subscription, subscription.id)
    assert (
        after_operation.status,
        after_operation.pricing_snapshot,
        after_subscription.quota_credits_reserved,
    ) == before
