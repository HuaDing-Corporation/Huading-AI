from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import BillingOperation, CreditRate, UsageRecord
from app.main import app


def _quote(client: TestClient, headers: dict[str, str], payload: dict) -> dict:
    response = client.post("/api/v1/scripts/estimate", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_script_reservation_commits_before_supplier_call(
    monkeypatch, auth_context, auth_db
) -> None:
    """Breaking reservation commit before invoking DeepSeek must fail this test."""
    from app.api.v1.routes import scripts as scripts_route

    class _DeepSeek:
        async def generate_text(self, _payload: dict) -> dict:
            with auth_db() as db:
                operation = db.scalar(
                    select(BillingOperation)
                    .where(BillingOperation.operation == "script_generate")
                    .order_by(BillingOperation.created_at.desc())
                )
                assert operation is not None
                assert operation.status == "in_progress"
                assert operation.requested_credits == Decimal("1")
            return {
                "text": "新品现在下单，立享优惠。",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                },
            }

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "key")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://llm.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "deepseek-v4-flash")
    monkeypatch.setattr(scripts_route, "resolve", lambda *_args, **_kwargs: _DeepSeek())
    client = TestClient(app)
    quote = _quote(client, auth_context["headers"], {"topic": "新品介绍"})

    response = client.post(
        "/api/v1/scripts/generate",
        json={"topic": "新品介绍"},
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote["quote_token"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["script"] == "新品现在下单，立享优惠。"
    assert body["billing"]["status"] == "settled"
    with auth_db() as db:
        usages = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.billing_operation_id == body["billing"]["operation_id"]
                )
            )
        )
    assert len(usages) == 1
    assert usages[0].unit == "call"
    assert usages[0].quantity == Decimal("1")
    assert usages[0].provider_usage == {
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
    }


def test_script_quote_is_read_only_and_rejects_changed_request(
    monkeypatch, auth_context, auth_db
) -> None:
    """A quote must neither reserve credits nor authorize a different payload."""
    from app.api.v1.routes import scripts as scripts_route

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "key")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://llm.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "deepseek-v4-flash")
    monkeypatch.setattr(scripts_route, "resolve", lambda *_args, **_kwargs: object())
    client = TestClient(app)
    quote = _quote(client, auth_context["headers"], {"topic": "新品介绍"})
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0

    response = client.post(
        "/api/v1/scripts/generate",
        json={"topic": "换了一个主题"},
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote["quote_token"],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PRICE_CHANGED"
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 0


def test_script_replay_returns_stored_clean_result_after_rate_changes(
    monkeypatch, auth_context, auth_db
) -> None:
    """Deleting result persistence or replay-before-quote verification must fail this test."""
    from app.api.v1.routes import scripts as scripts_route

    calls = 0

    class _DeepSeek:
        async def generate_text(self, _payload: dict) -> dict:
            nonlocal calls
            calls += 1
            return {"text": "**干净的口播文案。**"}

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "key")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://llm.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "deepseek-v4-flash")
    monkeypatch.setattr(scripts_route, "resolve", lambda *_args, **_kwargs: _DeepSeek())
    client = TestClient(app)
    payload = {"topic": "新品介绍"}
    quote = _quote(client, auth_context["headers"], payload)
    headers = {
        **auth_context["headers"],
        "Idempotency-Key": str(uuid4()),
        "X-Huading-Quote": quote["quote_token"],
    }
    first = client.post("/api/v1/scripts/generate", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    with auth_db() as db:
        db.add(
            CreditRate(
                tenant_id=auth_context["tenant_id"],
                capability="script_generate",
                unit="call",
                credits_per_unit=Decimal("9"),
                is_active=True,
            )
        )
        db.commit()
    replay = client.post("/api/v1/scripts/generate", json=payload, headers=headers)

    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"]
    assert calls == 1
    with auth_db() as db:
        stored = db.scalar(select(BillingOperation.result_payload))
    assert stored == {"script": "干净的口播文案。"}


def test_script_oversized_result_releases_reservation_with_billing(
    monkeypatch, auth_context, auth_db
) -> None:
    """Removing terminal-result failure handling must leave this reservation stuck."""
    from app.api.v1.routes import scripts as scripts_route

    class _DeepSeek:
        async def generate_text(self, _payload: dict) -> dict:
            return {"text": "x" * 70_000}

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "key")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://llm.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "deepseek-v4-flash")
    monkeypatch.setattr(scripts_route, "resolve", lambda *_args, **_kwargs: _DeepSeek())
    client = TestClient(app)
    payload = {"topic": "新品介绍"}
    quote = _quote(client, auth_context["headers"], payload)
    response = client.post(
        "/api/v1/scripts/generate",
        json=payload,
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote["quote_token"],
        },
    )

    assert response.status_code == 500
    billing = response.json()["error"]["detail"]["billing"]
    assert billing["status"] == "released"
    with auth_db() as db:
        operation = db.get(BillingOperation, billing["operation_id"])
        assert operation is not None
        assert operation.completion_kind == "failed"


def test_script_provider_resolution_race_releases_reservation(
    monkeypatch, auth_context, auth_db
) -> None:
    """A provider disappearing after reservation returns the normal released billing error."""
    from app.api.v1.routes import scripts as scripts_route
    from app.providers.base import ProviderResolutionError

    class _DeepSeek:
        async def generate_text(self, _payload: dict) -> dict:
            raise AssertionError("the provider disappeared before invocation")

    resolutions = 0

    def _resolve(*_args, **_kwargs):
        nonlocal resolutions
        resolutions += 1
        if resolutions == 3:
            raise ProviderResolutionError("provider changed after reservation")
        return _DeepSeek()

    monkeypatch.setattr(scripts_route.settings, "engine_llm_api_key", "key")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_base_url", "https://llm.test")
    monkeypatch.setattr(scripts_route.settings, "engine_llm_model", "deepseek-v4-flash")
    monkeypatch.setattr(scripts_route, "resolve", _resolve)
    client = TestClient(app)
    payload = {"topic": "新品介绍"}
    quote = _quote(client, auth_context["headers"], payload)
    response = client.post(
        "/api/v1/scripts/generate",
        json=payload,
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote["quote_token"],
        },
    )

    assert response.status_code == 503, response.text
    billing = response.json()["error"]["detail"]["billing"]
    assert billing["status"] == "released"
    assert resolutions == 3
    with auth_db() as db:
        operation = db.get(BillingOperation, billing["operation_id"])
        assert operation is not None
        assert operation.completion_kind == "failed"
