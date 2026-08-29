from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_object_storage
from app.db.models import BillingOperation, UsageRecord
from app.main import app


class _Storage:
    bucket = "test"

    def __init__(self, *, exists: bool = True) -> None:
        self.exists = exists

    def object_exists(self, _key: str) -> bool:
        return self.exists

    def presign_get_url(self, key: str, **_kwargs) -> str:
        return f"https://storage.test/{key}"


def test_scene_failure_releases_and_keeps_provider_cost(monkeypatch, auth_context, auth_db) -> None:
    """Removing the allocation-attached cost capture must fail this test."""
    from app.api.v1.routes import videos as videos_route
    from app.providers.base import ProviderInvocationError

    class _Provider:
        async def generate_scene_prompt(self, _payload: dict) -> dict:
            raise AssertionError("invoke wrapper should surface the controlled supplier failure")

    class _SupplierFailure(RuntimeError):
        usage_result = {
            "provider": "apimart",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 21,
            "completion_tokens": 13,
            "total_tokens": 34,
            "cost_cents": 9,
        }

    async def _fail(*_args, **_kwargs):
        try:
            raise _SupplierFailure("supplier unavailable")
        except _SupplierFailure as exc:
            raise ProviderInvocationError("scene prompt failed") from exc

    monkeypatch.setattr(videos_route, "resolve", lambda *_args, **_kwargs: _Provider())
    monkeypatch.setattr(videos_route, "invoke", _fail)
    app.dependency_overrides[get_object_storage] = _Storage
    try:
        client = TestClient(app)
        payload = {"topic": "春季上新", "product_image_keys": ["uploads/product.png"]}
        quote_response = client.post(
            "/api/v1/videos/scene-prompt/estimate",
            json=payload,
            headers=auth_context["headers"],
        )
        assert quote_response.status_code == 200, quote_response.text
        quote = quote_response.json()["data"]
        response = client.post(
            "/api/v1/videos/scene-prompt",
            json=payload,
            headers={
                **auth_context["headers"],
                "Idempotency-Key": str(uuid4()),
                "X-Huading-Quote": quote["quote_token"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 502, response.text
    billing = response.json()["error"]["detail"]["billing"]
    assert billing["status"] == "released"
    with auth_db() as db:
        usages = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.billing_operation_id == billing["operation_id"]
                )
            )
        )
    assert len(usages) == 1
    assert usages[0].unit == "call"
    assert usages[0].quantity == Decimal("1")
    assert usages[0].credits == Decimal("30")
    assert usages[0].provider_usage == {
        "input_tokens": 21,
        "output_tokens": 13,
        "total_tokens": 34,
    }
    assert usages[0].cost_cents == 9


def test_scene_quote_is_read_only_and_submit_requires_quote(
    monkeypatch, auth_context, auth_db
) -> None:
    """The estimate runs read-only gates but creates no billing state or supplier call."""
    from app.api.v1.routes import videos as videos_route

    resolved = 0

    class _Provider:
        async def generate_scene_prompt(self, _payload: dict) -> dict:
            raise AssertionError("estimate must not generate a scene prompt")

    def _resolve(*_args, **_kwargs):
        nonlocal resolved
        resolved += 1
        return _Provider()

    monkeypatch.setattr(videos_route, "resolve", _resolve)
    payload = {"topic": "春季上新", "product_image_keys": ["uploads/product.png"]}
    client = TestClient(app)
    app.dependency_overrides[get_object_storage] = _Storage
    try:
        quote = client.post(
            "/api/v1/videos/scene-prompt/estimate", json=payload, headers=auth_context["headers"]
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)
    assert quote.status_code == 200, quote.text
    assert quote.json()["data"]["operation"] == "scene_prompt"
    assert resolved == 1
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0

    app.dependency_overrides[get_object_storage] = _Storage
    try:
        missing_quote = client.post(
            "/api/v1/videos/scene-prompt",
            json=payload,
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)
    assert missing_quote.status_code == 422
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 0


def test_scene_estimate_rejects_missing_product_image(monkeypatch, auth_context) -> None:
    """Removing the read-only scene preflight must make this quote incorrectly succeed."""
    client = TestClient(app)
    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(
        videos_route,
        "resolve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing image must fail before resolving a provider")
        ),
    )
    app.dependency_overrides[get_object_storage] = lambda: _Storage(exists=False)
    try:
        response = client.post(
            "/api/v1/videos/scene-prompt/estimate",
            json={"topic": "春季上新", "product_image_keys": ["uploads/missing.png"]},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PRODUCT_IMAGE_NOT_FOUND"


def test_scene_oversized_result_releases_reservation_with_billing(
    monkeypatch, auth_context, auth_db
) -> None:
    """Terminal result validation must not strand a committed reservation."""
    from app.api.v1.routes import videos as videos_route

    class _Provider:
        async def generate_scene_prompt(self, _payload: dict) -> dict:
            return {"scene_prompt": "x" * 70_000, "negative_prompt": "no blur"}

    monkeypatch.setattr(videos_route, "resolve", lambda *_args, **_kwargs: _Provider())
    app.dependency_overrides[get_object_storage] = _Storage
    try:
        client = TestClient(app)
        payload = {"topic": "春季上新", "product_image_keys": ["uploads/product.png"]}
        quote_response = client.post(
            "/api/v1/videos/scene-prompt/estimate",
            json=payload,
            headers=auth_context["headers"],
        )
        assert quote_response.status_code == 200, quote_response.text
        response = client.post(
            "/api/v1/videos/scene-prompt",
            json=payload,
            headers={
                **auth_context["headers"],
                "Idempotency-Key": str(uuid4()),
                "X-Huading-Quote": quote_response.json()["data"]["quote_token"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 500, response.text
    billing = response.json()["error"]["detail"]["billing"]
    assert billing["status"] == "released"
    with auth_db() as db:
        operation = db.get(BillingOperation, billing["operation_id"])
        assert operation is not None
        assert operation.completion_kind == "failed"
