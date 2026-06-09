from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.db.models import Base, Organization, Tenant, User
from app.main import app


def _install_test_db() -> tuple[sessionmaker[Session], object]:
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
    return SessionTesting, engine


def _cleanup_test_db(engine: object) -> None:
    app.dependency_overrides.pop(get_db_session, None)
    Base.metadata.drop_all(engine)


def _register(client: TestClient, slug: str = "acme") -> dict:
    resp = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": slug,
            "tenant_name": "Acme Studio",
            "email": "Owner@Example.com",
            "password": "secret-pass",
            "full_name": "Owner",
        },
    )
    assert resp.status_code == 201
    return resp.json()["data"]


def test_register_tenant_creates_admin_user_org_and_token() -> None:
    SessionTesting, engine = _install_test_db()
    try:
        client = TestClient(app)
        data = _register(client)
        assert data["tenant"]["slug"] == "acme"
        assert data["user"]["role"] == "admin"
        assert data["token"]["access_token"]

        with SessionTesting() as db:
            tenant = db.scalar(select(Tenant).where(Tenant.slug == "acme"))
            assert tenant is not None
            user = db.scalar(select(User).where(User.tenant_id == tenant.id))
            org = db.scalar(select(Organization).where(Organization.tenant_id == tenant.id))
            assert user is not None
            assert org is not None
            assert user.email == "owner@example.com"
            assert org.name == "Acme Studio"
    finally:
        _cleanup_test_db(engine)


def test_login_then_me_uses_tenant_context() -> None:
    _SessionTesting, engine = _install_test_db()
    try:
        client = TestClient(app)
        registered = _register(client)
        login = client.post(
            "/api/v1/auth/login",
            json={"tenant_slug": "acme", "email": "owner@example.com", "password": "secret-pass"},
        )
        assert login.status_code == 200
        token = login.json()["data"]["access_token"]

        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["data"]["tenant"]["id"] == registered["tenant"]["id"]
        assert "tenant:admin" in me.json()["data"]["permissions"]
    finally:
        _cleanup_test_db(engine)


def test_tenant_header_mismatch_is_forbidden() -> None:
    _SessionTesting, engine = _install_test_db()
    try:
        client = TestClient(app)
        registered = _register(client)
        token = registered["token"]["access_token"]
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "other-tenant"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "TENANT_MISMATCH"
    finally:
        _cleanup_test_db(engine)


def test_rbac_rejects_creator_for_admin_permission() -> None:
    SessionTesting, engine = _install_test_db()
    try:
        client = TestClient(app)
        registered = _register(client)
        tenant_id = registered["tenant"]["id"]
        with SessionTesting() as db:
            admin = db.scalar(select(User).where(User.tenant_id == tenant_id))
            admin.role = "creator"
            db.commit()

        token = registered["token"]["access_token"]
        resp = client.get("/api/v1/auth/admin-check", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"
    finally:
        _cleanup_test_db(engine)


def test_auth_request_rejects_unknown_fields() -> None:
    _SessionTesting, engine = _install_test_db()
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/auth/login",
            json={
                "email": "owner@example.com",
                "password": "secret-pass",
                "tenant_slug": "acme",
                "role": "admin",
            },
        )
        assert resp.status_code == 422
        assert resp.headers.get("X-Request-ID")
    finally:
        _cleanup_test_db(engine)


def test_login_requires_tenant_slug() -> None:
    _SessionTesting, engine = _install_test_db()
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "secret-pass"},
        )
        assert resp.status_code == 422
    finally:
        _cleanup_test_db(engine)
