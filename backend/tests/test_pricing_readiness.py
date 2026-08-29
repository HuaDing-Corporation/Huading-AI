from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, text

from app.db.models import BrandVoice, CreditRate, ProviderConfig


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
