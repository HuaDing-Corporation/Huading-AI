from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def postgres_provider_config_session_factory():
    postgres_url = os.getenv("TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("TEST_POSTGRES_URL is required for the real PostgreSQL test.")

    from app.db.models import Base, ProviderConfig, Tenant

    engine = create_engine(
        postgres_url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c lock_timeout=10s -c statement_timeout=15s",
        },
    )
    schema = f"provider_config_inactive_{uuid4().hex}"
    schema_created = False
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        scoped_engine = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(
            scoped_engine,
            tables=[Tenant.__table__, ProviderConfig.__table__],
        )
        Session = sessionmaker(bind=scoped_engine, autoflush=False, autocommit=False)
        yield Session
    finally:
        try:
            if schema_created:
                with engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            engine.dispose()


def test_postgres_brand_voice_cold_start_reactivates_inactive_platform_config(
    monkeypatch,
    postgres_provider_config_session_factory,
) -> None:
    from app.api.v1.routes import brand_voices
    from app.db.models import ProviderConfig, Tenant

    monkeypatch.setattr(
        brand_voices,
        "settings",
        SimpleNamespace(
            engine_doubao_voice_clone_speaker_ids=["S_env_fallback_001"]
        ),
    )
    Session = postgres_provider_config_session_factory

    with Session() as db:
        db.add_all(
            [
                Tenant(
                    id="inactive-config-tenant",
                    slug="inactive-config-tenant",
                    name="Inactive Config Tenant",
                ),
                ProviderConfig(
                    id="inactive-platform-config",
                    tenant_id=None,
                    capability="voice_clone",
                    provider="doubao-voice-clone",
                    config={
                        "speaker_ids": ["S_retained_pool_001"],
                        "api_key": "retained-db-credential",
                    },
                    is_active=False,
                ),
            ]
        )
        db.commit()

        speaker_id = brand_voices._allocate_voice_clone_speaker_id(
            db,
            tenant_id="inactive-config-tenant",
            brand_voice_id="brand-voice-reactivation",
        )
        active_config_id = db.scalar(
            select(ProviderConfig.id).where(
                ProviderConfig.tenant_id.is_(None),
                ProviderConfig.capability == "voice_clone",
                ProviderConfig.provider == "doubao-voice-clone",
                ProviderConfig.is_active.is_(True),
            )
        )
        db.commit()

        configs = list(
            db.scalars(
                select(ProviderConfig).where(
                    ProviderConfig.tenant_id.is_(None),
                    ProviderConfig.capability == "voice_clone",
                    ProviderConfig.provider == "doubao-voice-clone",
                )
            )
        )

    assert speaker_id == "S_retained_pool_001"
    assert active_config_id == "inactive-platform-config"
    assert len(configs) == 1
    assert configs[0].id == "inactive-platform-config"
    assert configs[0].is_active is True
    assert configs[0].config == {
        "speaker_ids": ["S_retained_pool_001"],
        "api_key": "retained-db-credential",
        "used_speaker_ids": {
            "S_retained_pool_001": "brand-voice-reactivation",
        },
    }


def test_postgres_platform_provider_config_still_rejects_second_active_row(
    postgres_provider_config_session_factory,
) -> None:
    from app.db.models import ProviderConfig

    Session = postgres_provider_config_session_factory
    with Session() as db:
        db.add(
            ProviderConfig(
                id="first-active-platform-config",
                tenant_id=None,
                capability="voice_clone",
                provider="doubao-voice-clone",
                config={},
                is_active=True,
            )
        )
        db.commit()
        db.add(
            ProviderConfig(
                id="second-active-platform-config",
                tenant_id=None,
                capability="voice_clone",
                provider="doubao-voice-clone",
                config={},
                is_active=True,
            )
        )

        with pytest.raises(IntegrityError) as exc_info:
            db.commit()
        db.rollback()

    assert getattr(exc_info.value.orig, "sqlstate", None) == "23505"
    assert (
        getattr(getattr(exc_info.value.orig, "diag", None), "constraint_name", None)
        == "uq_provider_configs_platform_capability_provider"
    )
