import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-test-secret-test-secret-32")

from app.api.deps import get_db_session
from app.core.config import settings
from app.db.models import Base, Plan
from app.main import app


@pytest.fixture(autouse=True)
def jwt_test_secret(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-test-secret-test-secret-32")
    monkeypatch.setattr(settings, "engine_bgm_seed_on_startup", False)


@pytest.fixture
def auth_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Enforce foreign keys like Postgres so FK-ordering bugs (e.g. inserting a
    # task_asset before its video_task is persisted) surface in tests too.
    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db() -> Generator[Session, None, None]:
        db = SessionTesting()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    try:
        yield SessionTesting
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        Base.metadata.drop_all(engine)


@pytest.fixture
def seed_plan(auth_db):
    """Seed a default 'basic' plan so register-tenant can attach a subscription."""
    session = auth_db()
    plan = Plan(
        code="basic",
        name="Basic",
        price_cents=0,
        period="monthly",
        quota_credits=1000,
        is_active=True,
    )
    session.add(plan)
    session.commit()
    session.close()
    return plan


@pytest.fixture
def auth_context(auth_db, seed_plan):
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": "acme",
            "tenant_name": "Acme Studio",
            "email": "owner@example.com",
            "password": "secret-pass",
        },
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    token = data["token"]["access_token"]
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "tenant_id": data["tenant"]["id"],
        "user_id": data["user"]["id"],
    }
