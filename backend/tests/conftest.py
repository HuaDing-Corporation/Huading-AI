import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-test-secret-test-secret-32")

from app.api.deps import get_db_session
from app.core.config import settings
from app.db.models import Base
from app.main import app


@pytest.fixture(autouse=True)
def jwt_test_secret(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-test-secret-test-secret-32")


@pytest.fixture
def auth_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
def auth_context(auth_db):
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
