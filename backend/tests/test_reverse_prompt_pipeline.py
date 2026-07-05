from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Asset, ProviderConfig, ReversePromptJob, Subscription, Tenant, UsageRecord
from app.main import app
from app.providers.reverse_prompt.apimart_gemini import APIMartGeminiReversePromptProvider


def test_reverse_prompt_seed_provider_id_fits_provider_config_column():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260706_0017_reverse_prompt_jobs.py"
    )
    spec = importlib.util.spec_from_file_location("reverse_prompt_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert len(migration._PROVIDER_ID) <= 36


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Response(self.payloads.pop(0))


def _chat_payload(
    content: str,
    *,
    usage: dict | None = None,
    credits: object | None = None,
) -> dict:
    payload = {
        "id": "chatcmpl-test",
        "choices": [{"message": {"content": content}}],
        "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    }
    if credits is not None:
        payload["credits"] = credits
    return payload


JSON_CONTENT = """{
  "target_format": "seedance_2_0",
  "prompt_zh": "Premium perfume bottle on marble with soft morning light.",
  "prompt_en": "Glass perfume bottle on white marble, soft morning light.",
  "negative_prompt": "blurry, distorted logo",
  "style_tags": ["commercial", "soft light"],
  "camera": "close-up product shot",
  "lighting": "soft morning light",
  "composition": "centered product composition",
  "subject": "glass perfume bottle",
  "scene": "white marble tabletop",
  "motion_hint": "slow push-in camera move",
  "selling_points": ["crystal clear bottle", "premium texture"],
  "text_in_media": ["PARFUM"],
  "disclaimer": "Text may be approximate; verify brand and label details before reuse.",
  "confidence": 0.86
}"""


def test_apimart_gemini_provider_uses_token_pricing_when_chat_response_has_no_credits(monkeypatch):
    monkeypatch.setattr(
        "app.services.apimart_costs.settings.engine_apimart_credit_usd",
        Decimal("0.10"),
    )
    monkeypatch.setattr(
        "app.services.apimart_costs.settings.engine_usd_cny_rate",
        Decimal("7.20"),
    )
    provider = APIMartGeminiReversePromptProvider(
        api_key="api-test-key",
        base_url="https://api.apimart.ai/v1",
        model="gemini-3.1-pro-preview",
        session=_Session([_chat_payload(JSON_CONTENT)]),
    )

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert result["prompt_zh"].startswith("Premium perfume")
    assert result["target_format"] == "seedance_2_0"
    assert result["prompt_tokens"] == 1000
    assert result["completion_tokens"] == 500
    assert result["credits"] == Decimal("0.064")
    assert result["cost_cents"] == 5


def test_apimart_gemini_provider_prefers_authoritative_credits_when_present(monkeypatch):
    monkeypatch.setattr(
        "app.services.apimart_costs.settings.engine_apimart_credit_usd",
        Decimal("0.10"),
    )
    monkeypatch.setattr(
        "app.services.apimart_costs.settings.engine_usd_cny_rate",
        Decimal("7.20"),
    )
    provider = APIMartGeminiReversePromptProvider(
        api_key="api-test-key",
        session=_Session([_chat_payload(JSON_CONTENT, credits="2.5")]),
    )

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert result["credits"] == Decimal("2.5")
    assert result["cost_cents"] == 180


def test_apimart_gemini_provider_accepts_apimart_data_wrapper():
    provider = APIMartGeminiReversePromptProvider(
        api_key="api-test-key",
        session=_Session([{"code": 200, "data": _chat_payload(JSON_CONTENT)}]),
    )

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert result["prompt_tokens"] == 1000
    assert result["completion_tokens"] == 500
    assert result["prompt_en"].startswith("Glass perfume")


def test_apimart_gemini_provider_rejects_apimart_error_envelope():
    provider = APIMartGeminiReversePromptProvider(
        api_key="api-test-key",
        session=_Session([{"code": 401, "message": "invalid token"}]),
    )

    try:
        provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})
    except Exception as exc:
        assert exc.__class__.__name__ == "APIMartGeminiReversePromptError"
        assert "invalid token" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("APIMart error envelope must fail")


def test_apimart_gemini_provider_retries_once_when_model_returns_invalid_json():
    session = _Session(
        [
            _chat_payload("not-json"),
            _chat_payload(JSON_CONTENT),
        ]
    )
    provider = APIMartGeminiReversePromptProvider(api_key="api-test-key", session=session)

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert result["prompt_en"].startswith("Glass perfume")
    assert len(session.calls) == 2
    retry_text_parts = session.calls[1]["json"]["messages"][-1]["content"]
    assert any(
        "Previous response was not valid JSON" in part.get("text", "")
        for part in retry_text_parts
    )


def _seed_reverse_prompt_provider(db, tenant_id: str | None = None) -> None:
    db.add(
        ProviderConfig(
            tenant_id=tenant_id,
            capability="reverse_prompt",
            provider="apimart-gemini",
            config={"api_key": "api-test-key"},
            is_active=True,
        )
    )


def _seed_image_asset(db, tenant_id: str) -> Asset:
    asset = Asset(
        tenant_id=tenant_id,
        type="avatar_image",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/source.png",
        mime_type="image/png",
        size_bytes=1234,
        status="ready",
    )
    db.add(asset)
    db.flush()
    return asset


def test_reverse_prompt_sync_creates_job_records_usage_and_returns_fill_targets(
    auth_db,
    auth_context,
    monkeypatch,
):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_image_asset(session, auth_context["tenant_id"])
    asset_id = asset.id
    asset_storage_key = asset.storage_key
    session.commit()
    session.close()

    class _Storage:
        def presign_get_url(
            self,
            key: str,
            *,
            expires_in: int,
            download_filename: str | None = None,
        ) -> str:
            assert key == asset_storage_key
            return "https://assets.test/presigned-source.png"

    fake_provider = SimpleNamespace(
        reverse_image=lambda payload: {
            "target_format": "seedance_2_0",
            "prompt_zh": "Premium perfume bottle on marble.",
            "prompt_en": (
                "Premium perfume bottle commercial product shot, "
                "soft light, marble surface."
            ),
            "negative_prompt": "blurry, low quality",
            "style_tags": ["commercial", "premium"],
            "camera": "close-up",
            "lighting": "soft light",
            "composition": "centered",
            "subject": "perfume bottle",
            "scene": "marble surface",
            "motion_hint": "slow push-in",
            "selling_points": ["premium texture"],
            "text_in_media": ["PARFUM"],
            "disclaimer": "Verify visible text before reuse.",
            "confidence": 0.91,
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "credits": Decimal("0.064"),
            "cost_cents": 5,
            "provider": "apimart",
            "model": "gemini-3.1-pro-preview",
            "raw_model_json": {"prompt_zh": "Premium perfume bottle on marble."},
        }
    )
    monkeypatch.setattr("app.api.v1.routes.reverse_prompt.get_object_storage", lambda: _Storage())
    monkeypatch.setattr(
        "app.services.reverse_prompt.resolve",
        lambda *args, **kwargs: fake_provider,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["status"] == "succeeded"
    assert body["result"]["selling_points"] == ["premium texture"]
    assert body["result"]["text_in_media"] == ["PARFUM"]
    assert body["result"]["disclaimer"] == "Verify visible text before reuse."
    assert body["result"]["fill_targets"]["video_gen"]["prompt"] == body["result"]["prompt_en"]
    assert body["result"]["fill_targets"]["seedance_i2v"]["scene_prompt"]
    assert body["result"]["fill_targets"]["avatar_talk"]["topic"]
    assert body["result"]["fill_targets"]["ecom_model"]["extra_prompt"]

    db = auth_db()
    job = db.get(ReversePromptJob, body["id"])
    subscription = db.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    assert job is not None
    assert job.tenant_id == auth_context["tenant_id"]
    usage = db.scalar(
        select(UsageRecord).where(
            UsageRecord.tenant_id == auth_context["tenant_id"],
            UsageRecord.capability == "reverse_prompt",
        )
    )
    assert usage is not None
    assert usage.video_task_id is None
    assert usage.subscription_id == subscription.id
    assert usage.capability == "reverse_prompt"
    assert usage.provider == "apimart"
    assert usage.model == "gemini-3.1-pro-preview"
    assert usage.unit == "token"
    assert usage.quantity == Decimal("1500.000")
    assert usage.credits == Decimal("1.00")
    assert usage.cost_cents == 5
    assert subscription.quota_credits_used == 1
    db.close()


def test_reverse_prompt_rejects_when_quota_is_exhausted(auth_db, auth_context, monkeypatch):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_image_asset(session, auth_context["tenant_id"])
    asset_id = asset.id
    subscription = session.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    subscription.quota_credits_used = subscription.quota_credits_total
    session.commit()
    session.close()

    fake_provider = SimpleNamespace(
        reverse_image=lambda payload: (_ for _ in ()).throw(
            AssertionError("insufficient quota must not call provider")
        )
    )
    monkeypatch.setattr(
        "app.services.reverse_prompt.resolve",
        lambda *args, **kwargs: fake_provider,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_QUOTA_EXCEEDED"


def test_reverse_prompt_regenerate_failure_marks_job_failed(auth_db, auth_context, monkeypatch):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_image_asset(session, auth_context["tenant_id"])
    job = ReversePromptJob(
        tenant_id=auth_context["tenant_id"],
        created_by_user_id=auth_context["user_id"],
        source_kind="image",
        source_asset_id=asset.id,
        source_storage_key=asset.storage_key,
        target_format="seedance_2_0",
        status="succeeded",
        result_json={"target_format": "seedance_2_0"},
    )
    session.add(job)
    session.commit()
    job_id = job.id
    session.close()

    class _Storage:
        def presign_get_url(
            self,
            key: str,
            *,
            expires_in: int,
            download_filename: str | None = None,
        ) -> str:
            return "https://assets.test/presigned-source.png"

    fake_provider = SimpleNamespace(
        reverse_image=lambda payload: (_ for _ in ()).throw(RuntimeError("provider down"))
    )
    monkeypatch.setattr("app.api.v1.routes.reverse_prompt.get_object_storage", lambda: _Storage())
    monkeypatch.setattr(
        "app.services.reverse_prompt.resolve",
        lambda *args, **kwargs: fake_provider,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/reverse-prompt/jobs/{job_id}/regenerate",
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "REVERSE_PROMPT_FAILED"
    db = auth_db()
    failed_job = db.get(ReversePromptJob, job_id)
    assert failed_job.status == "failed"
    assert failed_job.error_code == "REVERSE_PROMPT_FAILED"
    db.close()


def test_reverse_prompt_rejects_cross_tenant_asset(auth_db, auth_context):
    db = auth_db()
    _seed_reverse_prompt_provider(db)
    db.add(Tenant(id="tenant-other", slug="tenant-other", name="Tenant Other"))
    db.flush()
    other = _seed_image_asset(db, "tenant-other")
    other_id = other.id
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt",
        json={"source_asset_id": other_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REVERSE_PROMPT_SOURCE_NOT_FOUND"
