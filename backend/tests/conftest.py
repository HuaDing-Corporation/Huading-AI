import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-test-secret-test-secret-32")

from app.api.deps import get_db_session
from app.core.config import settings
from app.db.models import Base, Plan, Subscription, Tenant, User
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
def db_session():
    """Small wallet-backed database used by billing service tests."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_billing_sqlite_fk(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    now = datetime.now(UTC)
    tenant = Tenant(id="tenant-a", slug="billing-a", name="Billing A")
    user = User(
        id="user-a",
        tenant_id=tenant.id,
        email="billing-a@example.com",
        password_hash="hash",
        role="creator",
    )
    plan = Plan(
        id="plan-a",
        code="billing-plan-a",
        name="Billing Plan",
        price_cents=0,
        period="monthly",
        quota_credits=100,
    )
    subscription = Subscription(
        id="subscription-a",
        tenant_id=tenant.id,
        plan_id=plan.id,
        status="active",
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=30),
        quota_credits_total=100,
        quota_credits_used=0,
        quota_credits_reserved=0,
    )
    session.add_all([tenant, plan])
    session.flush()
    session.add_all([user, subscription])
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def seed_plan(auth_db):
    """Seed a default 'basic' plan so register-tenant can attach a subscription."""
    session = auth_db()
    plan = Plan(
        code="basic",
        name="Basic",
        price_cents=0,
        period="monthly",
        quota_credits=10_000_000,
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
