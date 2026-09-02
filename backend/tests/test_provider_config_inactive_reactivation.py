from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def postgres_provider_config_session_factory(cloned_model_metadata_factory):
    postgres_url = os.getenv("TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("TEST_POSTGRES_URL is required for the real PostgreSQL test.")

    from app.db.models import ProviderConfig, Tenant

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
        test_metadata = cloned_model_metadata_factory()
        test_metadata.create_all(
            scoped_engine,
            tables=[
                test_metadata.tables[Tenant.__table__.key],
                test_metadata.tables[ProviderConfig.__table__.key],
            ],
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
