from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_object_storage
from app.db.models import BillingOperation, UsageRecord
from app.main import app


class _Storage:
    bucket = "test"

    def object_exists(self, _key: str) -> bool:
        return True

    def presign_get_url(self, key: str, **_kwargs) -> str:
        return f"https://storage.test/{key}"


def test_scene_failure_releases_and_keeps_provider_cost(monkeypatch, auth_context, auth_db) -> None:
    """Removing the allocation-attached cost capture must fail this test."""
    from app.api.v1.routes import videos as videos_route
    from app.providers.base import ProviderInvocationError

    client = TestClient(app)
    payload = {"topic": "春季上新", "product_image_keys": ["uploads/product.png"]}
    quote_response = client.post(
        "/api/v1/videos/scene-prompt/estimate", json=payload, headers=auth_context["headers"]
    )
    assert quote_response.status_code == 200, quote_response.text
    quote = quote_response.json()["data"]

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
    """The estimate cannot resolve a provider or create any billing state."""
    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(
        videos_route,
        "resolve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("estimate called provider")),
    )
    payload = {"topic": "春季上新", "product_image_keys": ["uploads/product.png"]}
    client = TestClient(app)
    quote = client.post(
        "/api/v1/videos/scene-prompt/estimate", json=payload, headers=auth_context["headers"]
    )
    assert quote.status_code == 200, quote.text
    assert quote.json()["data"]["operation"] == "scene_prompt"
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0

    missing_quote = client.post(
        "/api/v1/videos/scene-prompt",
        json=payload,
        headers=auth_context["headers"],
    )
    assert missing_quote.status_code == 422
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 0
