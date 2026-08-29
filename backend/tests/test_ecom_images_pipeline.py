from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_object_storage
from app.db.models import (
    Asset,
    BatchJob,
    BillingOperation,
    ProviderConfig,
    Subscription,
    Tenant,
    UsageRecord,
    VideoTask,
)
from app.main import app
from app.workers import image_gen


class _FakeStorage:
    def __init__(self) -> None:
        self.bucket = "ecom-test-bucket"
        self.saved: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.saved[key] = (content, content_type)
        return f"memory://{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.saved[key][0]

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        suffix = "&download=1" if download_filename else ""
        return f"https://storage.test/{key}?ttl={expires_in}{suffix}"


@pytest.fixture(autouse=True)
def _seed_image_provider(auth_db) -> None:
    with auth_db() as db:
        db.add(
            ProviderConfig(
                tenant_id=None,
                capability="image",
                provider="apimart",
                config={"api_key": "test-apimart-key"},
                is_active=True,
            )
        )
        db.commit()


def _set_image_provider(db, provider: str) -> None:
    config = db.scalar(
        select(ProviderConfig).where(
            ProviderConfig.tenant_id.is_(None),
            ProviderConfig.capability == "image",
        )
    )
    assert config is not None
    config.provider = provider
    config.config = {"api_key": f"test-{provider}-key"}


def _seed_source_asset(
    db,
    *,
    tenant_id: str,
    asset_id: str,
    mime_type: str = "image/png",
    asset_type: str = "product_image",
) -> dict[str, str]:
    storage_key = f"tenants/{tenant_id}/uploads/{asset_id}.png"
    asset = Asset(
        id=asset_id,
        tenant_id=tenant_id,
        type=asset_type,
        source="upload",
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=12,
        status="ready",
    )
    db.add(asset)
    db.commit()
    return {"id": asset_id, "storage_key": storage_key}


def _stub_image_task(monkeypatch):
    enqueued: list[dict] = []

    def fake_apply_async(*, args, task_id, queue=None):
        enqueued.append({"args": args, "task_id": task_id, "queue": queue})
        return type("Result", (), {"status": "PENDING"})()

    monkeypatch.setattr(image_gen.generate_image_task, "apply_async", fake_apply_async)
    return enqueued


@pytest.fixture(autouse=True)
def _sign_ecom_image_submissions(monkeypatch) -> None:
    """Exercise image creation through the same signed quote contract as clients."""
    original_post = TestClient.post

    def signed_post(self, url, *, json=None, headers=None, **kwargs):
        path = str(url)
        if (
            path in {"/api/v1/ecom-images/cutout", "/api/v1/ecom-images/cutout/batch"}
            and headers is not None
            and "X-Huading-Quote" not in headers
        ):
            estimate = original_post(
                self,
                "/api/v1/ecom-images/cutout/estimate",
                json=json,
                headers=headers,
            )
            if estimate.status_code != 200:
                return estimate
            headers = {
                **headers,
                "Idempotency-Key": str(uuid4()),
                "X-Huading-Quote": estimate.json()["data"]["quote_token"],
            }
        elif (
            path in {"/api/v1/ecom-images/model", "/api/v1/ecom-images/model/batch"}
            and headers is not None
            and "X-Huading-Quote" not in headers
        ):
            estimate = original_post(
                self,
                "/api/v1/ecom-images/model/estimate",
                json=json,
                headers=headers,
            )
            if estimate.status_code != 200:
                return estimate
            headers = {
                **headers,
                "Idempotency-Key": str(uuid4()),
                "X-Huading-Quote": estimate.json()["data"]["quote_token"],
            }
        return original_post(self, url, json=json, headers=headers, **kwargs)

    monkeypatch.setattr(TestClient, "post", signed_post)


def test_ecom_poster_templates_returns_static_presets(auth_context) -> None:
    resp = TestClient(app).get(
        "/api/v1/ecom-images/poster-templates",
        headers=auth_context["headers"],
    )

    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "ECOM_POSTER_DISABLED"


def test_ecom_cutout_insufficient_balance_rolls_back_everything(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        source = _seed_source_asset(
            db, tenant_id=auth_context["tenant_id"], asset_id="insufficient-cutout-source"
        )
        subscription = db.scalars(select(Subscription)).one()
        subscription.quota_credits_total = 0
        subscription.quota_credits_used = 0
        subscription.quota_credits_reserved = 0
        db.commit()
    enqueued = _stub_image_task(monkeypatch)

    response = TestClient(app).post(
        "/api/v1/ecom-images/cutout",
        json={"source_asset_id": source["id"]},
        headers=auth_context["headers"],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_QUOTA_EXCEEDED"
    assert enqueued == []
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(VideoTask)) == 0
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_terminal_cutout_replay_returns_the_stored_success_without_new_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.db.models import TaskAsset
    from app.services.ecom_billing import try_finalize_ecom_operation

    with auth_db() as db:
        source = _seed_source_asset(
            db, tenant_id=auth_context["tenant_id"], asset_id="terminal-replay-success-source"
        )
    payload = {"source_asset_id": source["id"]}
    client = TestClient(app)
    estimate = client.post(
        "/api/v1/ecom-images/cutout/estimate", json=payload, headers=auth_context["headers"]
    )
    assert estimate.status_code == 200
    headers = {
        **auth_context["headers"],
        "Idempotency-Key": str(uuid4()),
        "X-Huading-Quote": estimate.json()["data"]["quote_token"],
    }
    enqueued = _stub_image_task(monkeypatch)

    submitted = client.post("/api/v1/ecom-images/cutout", json=payload, headers=headers)

    assert submitted.status_code == 202
    task_id = submitted.json()["data"]["task_id"]
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task is not None
        operation_id = str(task.params["billing_operation_id"])
        asset = Asset(
            tenant_id=auth_context["tenant_id"],
            type="generated_image",
            source="generated",
            storage_key=f"tenants/{auth_context['tenant_id']}/photos/{task.id}/output.png",
            mime_type="image/png",
            status="ready",
        )
        db.add(asset)
        db.flush()
        asset_id = asset.id
        db.add(TaskAsset(video_task_id=task.id, asset_id=asset.id, role="output_image"))
        task.status = "done"
        task.progress = 100
        db.commit()
    with auth_db() as db:
        assert try_finalize_ecom_operation(db, billing_operation_id=operation_id) is not None
        db.commit()
    with auth_db() as db:
        counts_before = (
            db.scalar(select(func.count()).select_from(VideoTask)),
            db.scalar(select(func.count()).select_from(BillingOperation)),
            db.scalar(select(func.count()).select_from(UsageRecord)),
        )

    replay = client.post("/api/v1/ecom-images/cutout", json=payload, headers=headers)

    assert replay.status_code == 202
    data = replay.json()["data"]
    assert data["state"] == "completed"
    assert data["completion_kind"] == "succeeded"
    assert data["billing"]["held_credits"] == 0
    assert data["result_type"] == "ecom_image_batch"
    assert data["result"]["items"] == [
        {
            "item_index": 0,
            "task_id": task_id,
            "source_asset_id": source["id"],
            "status": "done",
            "asset_id": asset_id,
        }
    ]
    assert len(enqueued) == 1
    assert enqueued[0]["task_id"] == task_id
    with auth_db() as db:
        counts_after = (
            db.scalar(select(func.count()).select_from(VideoTask)),
            db.scalar(select(func.count()).select_from(BillingOperation)),
            db.scalar(select(func.count()).select_from(UsageRecord)),
        )
    assert counts_after == counts_before


def test_terminal_cutout_replay_returns_the_stored_failure_without_new_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services.ecom_billing import try_finalize_ecom_operation

    with auth_db() as db:
        source = _seed_source_asset(
            db, tenant_id=auth_context["tenant_id"], asset_id="terminal-replay-failure-source"
        )
    payload = {"source_asset_id": source["id"]}
    client = TestClient(app)
    estimate = client.post(
        "/api/v1/ecom-images/cutout/estimate", json=payload, headers=auth_context["headers"]
    )
    assert estimate.status_code == 200
    headers = {
        **auth_context["headers"],
        "Idempotency-Key": str(uuid4()),
        "X-Huading-Quote": estimate.json()["data"]["quote_token"],
    }
    enqueued = _stub_image_task(monkeypatch)

    submitted = client.post("/api/v1/ecom-images/cutout", json=payload, headers=headers)

    assert submitted.status_code == 202
    task_id = submitted.json()["data"]["task_id"]
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task is not None
        operation_id = str(task.params["billing_operation_id"])
        task.status = "failed"
        task.progress = 1
        db.commit()
    with auth_db() as db:
        assert try_finalize_ecom_operation(db, billing_operation_id=operation_id) is not None
        db.commit()
    with auth_db() as db:
        counts_before = (
            db.scalar(select(func.count()).select_from(VideoTask)),
            db.scalar(select(func.count()).select_from(BillingOperation)),
            db.scalar(select(func.count()).select_from(UsageRecord)),
        )

    replay = client.post("/api/v1/ecom-images/cutout", json=payload, headers=headers)

    assert replay.status_code == 202
    data = replay.json()["data"]
    assert data["state"] == "completed"
    assert data["completion_kind"] == "failed"
    assert data["billing"]["held_credits"] == 0
    assert data["failure"] == {
        "code": "ECOM_IMAGE_BATCH_FAILED",
        "original_http_status": 502,
        "detail": None,
    }
    assert len(enqueued) == 1
    assert enqueued[0]["task_id"] == task_id
    with auth_db() as db:
        counts_after = (
            db.scalar(select(func.count()).select_from(VideoTask)),
            db.scalar(select(func.count()).select_from(BillingOperation)),
            db.scalar(select(func.count()).select_from(UsageRecord)),
        )
    assert counts_after == counts_before


def test_ecom_poster_single_creates_photo_task_clamps_text_without_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    long_title = "限时大促新品热卖全场爆款买一送一今日专享超值优惠"
    long_subtitle = "官方正品保障极速发货售后无忧门店同款直播间专享福利"
    with auth_db() as db:
        source = _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="product-poster-source",
        )
        subscription_id = db.scalars(select(Subscription.id)).one()

    enqueued = _stub_image_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/ecom-images/poster",
            json={
                "source_asset_id": source["id"],
                "template_id": "promo_bold",
                "title": long_title,
                "subtitle": long_subtitle,
                "apply_visible_label": True,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "ECOM_POSTER_DISABLED"
    assert enqueued == []

    with auth_db() as db:
        task_count = db.scalar(select(func.count()).select_from(VideoTask))
        usage_count = db.scalar(select(func.count()).select_from(UsageRecord))
        subscription = db.get(Subscription, subscription_id)

    assert task_count == 0
    assert usage_count == 0
    assert subscription.quota_credits_reserved == 0


def test_ecom_poster_batch_clamps_to_20_and_fans_out_without_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        sources = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"poster-batch-product-{index:02d}",
            )
            for index in range(25)
        ]

    enqueued = _stub_image_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/ecom-images/poster/batch",
            json={
                "items": [
                    {
                        "source_asset_id": source["id"],
                        "template_id": "minimal" if index % 2 == 0 else "festival",
                        "title": f"主推单品{index}",
                        "subtitle": f"今日专享福利{index}",
                    }
                    for index, source in enumerate(sources)
                ]
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "ECOM_POSTER_DISABLED"
    assert enqueued == []

    with auth_db() as db:
        task_count = db.scalar(
            select(func.count())
            .select_from(VideoTask)
            .where(VideoTask.mode == "photo", VideoTask.params["kind"].as_string() == "ecom_poster")
        )
        batch_rows = db.scalar(select(func.count()).select_from(BatchJob))
        usage_count = db.scalar(select(func.count()).select_from(UsageRecord))
        subscription = db.scalars(select(Subscription)).one()

    assert task_count == 0
    assert batch_rows == 0
    assert usage_count == 0
    assert subscription.quota_credits_reserved == 0


def test_ecom_poster_rejects_unknown_template_id_without_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        source = _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="product-poster-template-source",
        )

    enqueued = _stub_image_task(monkeypatch)
    resp = TestClient(app).post(
        "/api/v1/ecom-images/poster",
        json={
            "source_asset_id": source["id"],
            "template_id": "unknown-template",
            "title": "上新",
            "subtitle": "今日专享",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "ECOM_POSTER_DISABLED"
    assert enqueued == []

    with auth_db() as db:
        task_count = db.scalar(select(func.count()).select_from(VideoTask))
        usage_count = db.scalar(select(func.count()).select_from(UsageRecord))
        subscription = db.scalars(select(Subscription)).one()

    assert task_count == 0
    assert usage_count == 0
    assert subscription.quota_credits_reserved == 0


def test_ecom_poster_rejects_cross_tenant_source_asset(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        other_tenant = Tenant(id="other-poster-tenant", slug="other-poster", name="Other Poster")
        db.add(other_tenant)
        db.flush()
        _seed_source_asset(db, tenant_id=other_tenant.id, asset_id="foreign-poster-product")

    enqueued = _stub_image_task(monkeypatch)
    resp = TestClient(app).post(
        "/api/v1/ecom-images/poster",
        json={
            "source_asset_id": "foreign-poster-product",
            "template_id": "minimal",
            "title": "上新",
            "subtitle": "今日专享",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "ECOM_POSTER_DISABLED"
    assert enqueued == []


def test_ecom_model_styles_returns_static_presets(auth_context) -> None:
    resp = TestClient(app).get(
        "/api/v1/ecom-images/model-styles",
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    styles = resp.json()["data"]["styles"]
    assert styles == [
        {"id": "studio_white", "name": "棚拍白底"},
        {"id": "lifestyle", "name": "生活场景"},
        {"id": "street", "name": "街拍"},
        {"id": "office_commute", "name": "通勤职场"},
        {"id": "resort_travel", "name": "度假旅拍"},
        {"id": "high_fashion", "name": "高级时尚大片"},
    ]


def test_ecom_model_single_preserves_long_prompt_and_reserves_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    long_extra = ("clean catalog pose with exact garment details; " * 120)[:5000]
    assert len(long_extra) == 5000
    with auth_db() as db:
        source = _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="product-model-source",
        )
        subscription_id = db.scalars(select(Subscription.id)).one()

    enqueued = _stub_image_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/ecom-images/model",
            json={
                "source_asset_id": source["id"],
                "gender": "any",
                "style_id": "studio_white",
                "extra_prompt": long_extra,
                "aspect_ratio": "21:9",
                "apply_visible_label": True,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["task_id"]
    assert data["status"] == "queued"
    assert len(enqueued) == 1
    payload = enqueued[0]["args"][0]
    assert payload["kind"] == "ecom_model"
    assert payload["gender"] == "any"
    assert payload["style_id"] == "studio_white"
    assert payload["source_asset_id"] == source["id"]
    assert payload["source_storage_key"] == source["storage_key"]
    assert payload["image_provider"] == "apimart"
    assert payload["image_resolution"] == "1k"
    assert payload["video_task_id"] == data["task_id"]
    assert payload["extra_prompt"] == long_extra
    assert payload["aspect_ratio"] == "21:9"
    assert payload["requested_aspect_ratio"] == "21:9"
    assert payload["apply_visible_label"] is True
    assert "female" not in payload["topic"].lower()
    assert "male" not in payload["topic"].lower()
    assert "preserve the exact shape" in payload["topic"].lower()

    with auth_db() as db:
        task = db.get(VideoTask, data["task_id"])
        usage = db.scalars(
            select(UsageRecord).where(UsageRecord.video_task_id == data["task_id"])
        ).one()
        subscription = db.get(Subscription, subscription_id)

    assert task.mode == "photo"
    assert task.video_mode == "photo"
    assert task.aspect_ratio == "21:9"
    assert task.params["kind"] == "ecom_model"
    assert task.params["gender"] == "any"
    assert task.params["style_id"] == "studio_white"
    assert task.params["extra_prompt"] == long_extra
    assert task.params["aspect_ratio"] == "21:9"
    assert task.params["requested_aspect_ratio"] == "21:9"
    assert task.params["source_storage_key"] == source["storage_key"]
    assert task.params["image_provider"] == "apimart"
    assert task.params["image_resolution"] == "1k"
    assert task.params["apply_visible_label"] is True
    assert usage.status == "reserved"
    assert usage.credits == 80
    assert subscription.quota_credits_reserved == 80


@pytest.mark.parametrize("field_name", ["extra_prompt", "custom_style"])
def test_ecom_model_rejects_prompt_fields_over_20000_characters_without_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
    field_name: str,
) -> None:
    enqueued = _stub_image_task(monkeypatch)
    response = TestClient(app).post(
        "/api/v1/ecom-images/model",
        json={
            "source_asset_id": "unused-product",
            "gender": "any",
            field_name: "x" * 20_001,
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert enqueued == []
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(VideoTask)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        subscription = db.scalars(select(Subscription)).one()
        assert subscription.quota_credits_reserved == 0


def test_ecom_model_product_asset_list_takes_precedence_over_legacy_scalar(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        legacy = _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="model-product-legacy",
        )
        products = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"model-product-{index}",
            )
            for index in range(2)
        ]

    enqueued = _stub_image_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).post(
            "/api/v1/ecom-images/model",
            json={
                "source_asset_id": legacy["id"],
                "product_asset_ids": [product["id"] for product in products],
                "gender": "any",
                "style_id": "studio_white",
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 202
    assert len(enqueued) == 1
    worker_payload = enqueued[0]["args"][0]
    assert worker_payload["product_asset_ids"] == [product["id"] for product in products]
    assert worker_payload["source_asset_id"] == products[0]["id"]
    assert worker_payload["source_storage_key"] == products[0]["storage_key"]
    assert worker_payload["source_storage_keys"] == [product["storage_key"] for product in products]


def test_ecom_model_schema_accepts_product_list_or_legacy_scalar() -> None:
    from app.schemas.ecom_images import EcomModelRequest

    product_list = EcomModelRequest(
        product_asset_ids=["product-from-list"],
        model_asset_ids=[],
        gender="any",
        style_id="studio_white",
    )
    legacy_scalar = EcomModelRequest(
        source_asset_id="product-from-scalar",
        gender="any",
        style_id="studio_white",
    )

    assert product_list.product_asset_ids == ["product-from-list"]
    assert product_list.model_asset_ids == []
    assert product_list.source_asset_id is None
    assert legacy_scalar.source_asset_id == "product-from-scalar"
    assert legacy_scalar.product_asset_ids is None


def test_ecom_model_accepts_six_product_and_model_images_in_product_first_order(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        products = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"ordered-product-{index}",
            )
            for index in range(2)
        ]
        models = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"ordered-model-{index}",
                asset_type="avatar_image",
            )
            for index in range(4)
        ]

    enqueued = _stub_image_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).post(
            "/api/v1/ecom-images/model",
            json={
                "product_asset_ids": [product["id"] for product in products],
                "model_asset_ids": [model["id"] for model in models],
                "gender": "female",
                "style_id": "lifestyle",
                "aspect_ratio": "auto",
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 202
    worker_payload = enqueued[0]["args"][0]
    assert worker_payload["product_asset_ids"] == [product["id"] for product in products]
    assert worker_payload["model_asset_ids"] == [model["id"] for model in models]
    assert worker_payload["source_asset_id"] == products[0]["id"]
    assert worker_payload["source_storage_keys"] == [
        *[product["storage_key"] for product in products],
        *[model["storage_key"] for model in models],
    ]
    assert worker_payload["image_provider"] == "apimart"
    assert worker_payload["image_resolution"] == "1k"
    assert (
        "product reference images come first, followed by model reference images"
        in worker_payload["topic"]
    )
    assert (
        "model references only for the model's identity and appearance" in worker_payload["topic"]
    )


def test_ecom_model_rejects_two_references_for_openai_before_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        _set_image_provider(db, "openai")
        products = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"openai-multi-product-{index}",
            )
            for index in range(2)
        ]
        subscription_id = db.scalars(select(Subscription.id)).one()

    enqueued = _stub_image_task(monkeypatch)
    response = TestClient(app).post(
        "/api/v1/ecom-images/model",
        json={
            "product_asset_ids": [product["id"] for product in products],
            "gender": "any",
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ("IMAGE_PROVIDER_REFERENCE_IMAGES_UNSUPPORTED")
    assert response.json()["error"]["message"] == "当前图片服务最多支持 1 张参考图。"
    assert enqueued == []
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(VideoTask)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == 0


def test_ecom_model_batch_preflights_every_reference_group_before_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        _set_image_provider(db, "openai")
        products = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"openai-batch-product-{index}",
            )
            for index in range(3)
        ]
        subscription_id = db.scalars(select(Subscription.id)).one()

    enqueued = _stub_image_task(monkeypatch)
    response = TestClient(app).post(
        "/api/v1/ecom-images/model/batch",
        json={
            "items": [
                {
                    "product_asset_ids": [products[0]["id"]],
                    "gender": "any",
                },
                {
                    "product_asset_ids": [products[1]["id"], products[2]["id"]],
                    "gender": "any",
                },
            ]
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ("IMAGE_PROVIDER_REFERENCE_IMAGES_UNSUPPORTED")
    assert enqueued == []
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(VideoTask)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == 0


def test_ecom_model_rejects_more_than_six_images_without_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        products = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"over-limit-product-{index}",
            )
            for index in range(4)
        ]
        models = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"over-limit-model-{index}",
                asset_type="avatar_image",
            )
            for index in range(3)
        ]
        subscription_id = db.scalars(select(Subscription.id)).one()

    enqueued = _stub_image_task(monkeypatch)
    response = TestClient(app).post(
        "/api/v1/ecom-images/model",
        json={
            "product_asset_ids": [product["id"] for product in products],
            "model_asset_ids": [model["id"] for model in models],
            "gender": "any",
            "style_id": "studio_white",
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["message"] == "商品图与模特图合计最多 6 张"
    assert enqueued == []
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(VideoTask)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == 0


@pytest.mark.parametrize(
    ("product_images_mode", "mode_prompt"),
    [
        (
            None,
            "The product reference images show different products. "
            "Put, wear, or coordinate every product on one model in the same image. "
            "Do not merge products together and do not omit any product.",
        ),
        (
            "multi_angle",
            "The product reference images show different angles of the same product. "
            "Reconstruct exactly one coherent product from all angles. "
            "Do not duplicate the product.",
        ),
    ],
)
def test_ecom_model_product_image_modes_produce_distinct_final_prompts(
    monkeypatch,
    auth_context,
    auth_db,
    product_images_mode: str | None,
    mode_prompt: str,
) -> None:
    with auth_db() as db:
        products = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"prompt-{product_images_mode or 'default'}-{index}",
            )
            for index in range(2)
        ]

    enqueued = _stub_image_task(monkeypatch)
    request_payload = {
        "product_asset_ids": [product["id"] for product in products],
        "gender": "female",
        "style_id": "studio_white",
    }
    if product_images_mode is not None:
        request_payload["product_images_mode"] = product_images_mode
    response = TestClient(app).post(
        "/api/v1/ecom-images/model",
        json=request_payload,
        headers=auth_context["headers"],
    )

    assert response.status_code == 202
    assert enqueued[0]["args"][0]["topic"] == (
        "Create an e-commerce fashion image using the supplied reference images. "
        "All supplied reference images are product references. "
        f"{mode_prompt} "
        "Use a female fashion model. "
        "Apply this visual style: a clean studio white product catalog scene with controlled "
        "lighting. Preserve the exact shape, logos, colors, materials, proportions, and visible "
        "details of every product. Do not alter product designs, brand marks, text, or colorways."
    )


def test_ecom_model_batch_clamps_to_20_and_fans_out_independent_photo_tasks(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        sources = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"model-batch-product-{index:02d}",
            )
            for index in range(20)
        ]

    enqueued = _stub_image_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/ecom-images/model/batch",
            json={
                "items": [
                    {
                        "source_asset_id": source["id"],
                        "gender": "female" if index % 2 == 0 else "male",
                        "style_id": "lifestyle" if index % 2 == 0 else "street",
                        "extra_prompt": f"variant {index}",
                        "aspect_ratio": "4:3" if index % 2 == 0 else "2:3",
                    }
                    for index, source in enumerate(sources)
                ]
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["batch_id"]
    assert len(data["tasks"]) == 20
    assert len(enqueued) == 20
    assert [item["source_asset_id"] for item in data["tasks"]] == [
        source["id"] for source in sources[:20]
    ]
    assert all(item["status"] == "queued" for item in data["tasks"])
    assert {call["args"][0]["batch_id"] for call in enqueued} == {data["batch_id"]}
    assert {call["args"][0]["kind"] for call in enqueued} == {"ecom_model"}
    assert {call["args"][0]["image_provider"] for call in enqueued} == {"apimart"}
    assert {call["args"][0]["image_resolution"] for call in enqueued} == {"1k"}
    assert {call["args"][0]["style_id"] for call in enqueued} == {"lifestyle", "street"}
    assert {call["args"][0]["aspect_ratio"] for call in enqueued} == {"4:3", "2:3"}

    with auth_db() as db:
        task_count = db.scalar(
            select(func.count())
            .select_from(VideoTask)
            .where(VideoTask.mode == "photo", VideoTask.params["kind"].as_string() == "ecom_model")
        )
        batch_rows = db.scalar(select(func.count()).select_from(BatchJob))
        reserved_count = db.scalar(
            select(func.count()).select_from(UsageRecord).where(UsageRecord.status == "reserved")
        )
        subscription = db.scalars(select(Subscription)).one()

    assert task_count == 20
    assert batch_rows == 0
    assert reserved_count == 20
    assert subscription.quota_credits_reserved == 1600


def test_ecom_model_rejects_unknown_style_id(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        source = _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="product-model-style-source",
        )

    enqueued = _stub_image_task(monkeypatch)
    resp = TestClient(app).post(
        "/api/v1/ecom-images/model",
        json={
            "source_asset_id": source["id"],
            "gender": "female",
            "style_id": "unknown-style",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "ECOM_MODEL_STYLE_INVALID"
    assert enqueued == []

    with auth_db() as db:
        task_count = db.scalar(select(func.count()).select_from(VideoTask))
        reserved_count = db.scalar(select(func.count()).select_from(UsageRecord))
        subscription = db.scalars(select(Subscription)).one()

    assert task_count == 0
    assert reserved_count == 0
    assert subscription.quota_credits_reserved == 0


def test_ecom_model_rejects_preset_and_custom_style_together_without_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    enqueued = _stub_image_task(monkeypatch)
    response = TestClient(app).post(
        "/api/v1/ecom-images/model",
        json={
            "source_asset_id": "unused-product",
            "gender": "female",
            "style_id": "street",
            "custom_style": "soft window light with a minimalist gallery mood",
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["message"] == "预设风格与自定义风格不能同时选择"
    assert enqueued == []
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(VideoTask)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        subscription = db.scalars(select(Subscription)).one()
        assert subscription.quota_credits_reserved == 0


def test_ecom_model_accepts_custom_style_without_preset(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    custom_style = "soft window light with a minimalist gallery mood"
    with auth_db() as db:
        source = _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="custom-style-product",
        )

    enqueued = _stub_image_task(monkeypatch)
    response = TestClient(app).post(
        "/api/v1/ecom-images/model",
        json={
            "source_asset_id": source["id"],
            "gender": "any",
            "custom_style": custom_style,
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 202
    worker_payload = enqueued[0]["args"][0]
    assert worker_payload["custom_style"] == custom_style
    assert f"Apply this custom visual style: {custom_style}." in worker_payload["topic"]
    assert "studio white" not in worker_payload["topic"]


def test_ecom_model_accepts_no_style_without_adding_style_sentence(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        source = _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="no-style-product",
        )

    enqueued = _stub_image_task(monkeypatch)
    response = TestClient(app).post(
        "/api/v1/ecom-images/model",
        json={"source_asset_id": source["id"], "gender": "any"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 202
    worker_payload = enqueued[0]["args"][0]
    assert worker_payload["style_id"] is None
    assert "custom_style" not in worker_payload
    assert "Apply this visual style:" not in worker_payload["topic"]
    assert "Apply this custom visual style:" not in worker_payload["topic"]


def test_ecom_model_rejects_cross_tenant_source_asset(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        other_tenant = Tenant(id="other-model-tenant", slug="other-model", name="Other Model")
        db.add(other_tenant)
        db.flush()
        _seed_source_asset(db, tenant_id=other_tenant.id, asset_id="foreign-model-product")

    enqueued = _stub_image_task(monkeypatch)
    resp = TestClient(app).post(
        "/api/v1/ecom-images/model",
        json={
            "source_asset_id": "foreign-model-product",
            "gender": "male",
            "style_id": "street",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ECOM_SOURCE_ASSET_NOT_FOUND"
    assert enqueued == []


def test_ecom_cutout_rejects_undeclared_image_provider_before_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.api.v1.routes import ecom_images as route
    from app.providers.base import ResolvedProvider

    class _UndeclaredImageProvider:
        async def generate_image(self, _payload):
            raise AssertionError("provider must not be invoked")

    with auth_db() as db:
        source = _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="undeclared-provider-cutout-source",
        )
        subscription = db.scalars(select(Subscription)).one()
        subscription_id = subscription.id
        reserved_before = subscription.quota_credits_reserved
        task_count_before = db.scalar(select(func.count()).select_from(VideoTask))
        usage_count_before = db.scalar(select(func.count()).select_from(UsageRecord))

    enqueued = _stub_image_task(monkeypatch)
    monkeypatch.setattr(
        route,
        "resolve_with_name",
        lambda _db, *, tenant_id, capability: ResolvedProvider(
            name="undeclared",
            provider=_UndeclaredImageProvider(),
        ),
        raising=False,
    )

    response = TestClient(app).post(
        "/api/v1/ecom-images/cutout",
        json={
            "source_asset_id": source["id"],
            "background": "white",
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IMAGE_PROVIDER_CAPABILITIES_UNDECLARED"
    assert response.json()["error"]["message"] == ("当前图片服务能力配置不完整，暂时无法生成图片。")
    assert enqueued == []
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == reserved_before
        assert db.scalar(select(func.count()).select_from(VideoTask)) == task_count_before
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == usage_count_before


def test_ecom_model_rejects_cross_tenant_model_reference_without_side_effects(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        product = _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="local-product-with-foreign-model",
        )
        other_tenant = Tenant(
            id="other-model-reference-tenant",
            slug="other-model-reference",
            name="Other Model Reference",
        )
        db.add(other_tenant)
        db.flush()
        foreign_model = _seed_source_asset(
            db,
            tenant_id=other_tenant.id,
            asset_id="foreign-model-reference",
            asset_type="avatar_image",
        )
        subscription_id = db.scalars(select(Subscription.id)).one()

    enqueued = _stub_image_task(monkeypatch)
    response = TestClient(app).post(
        "/api/v1/ecom-images/model",
        json={
            "product_asset_ids": [product["id"]],
            "model_asset_ids": [foreign_model["id"]],
            "gender": "any",
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ECOM_SOURCE_ASSET_NOT_FOUND"
    assert enqueued == []
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(VideoTask)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_reserved == 0


def test_ecom_cutout_single_creates_photo_task_and_reserves_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        source = _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="product-cutout-source",
        )
        subscription_id = db.scalars(select(Subscription.id)).one()

    enqueued = _stub_image_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/ecom-images/cutout",
            json={
                "source_asset_id": source["id"],
                "background": "transparent",
                "aspect_ratio": "auto",
                "apply_visible_label": True,
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["task_id"]
    assert data["status"] == "queued"
    assert len(enqueued) == 1
    assert enqueued[0]["task_id"] == data["task_id"]
    assert enqueued[0]["queue"] == "image"
    payload = enqueued[0]["args"][0]
    assert payload["kind"] == "ecom_cutout"
    assert payload["background"] == "transparent"
    assert payload["source_asset_id"] == source["id"]
    assert payload["source_storage_key"] == source["storage_key"]
    assert payload["image_provider"] == "apimart"
    assert payload["image_resolution"] == "1k"
    assert payload["video_task_id"] == data["task_id"]
    assert payload["aspect_ratio"] == "auto"
    assert payload["requested_aspect_ratio"] == "auto"
    assert payload["apply_visible_label"] is True

    with auth_db() as db:
        task = db.get(VideoTask, data["task_id"])
        usage = db.scalars(
            select(UsageRecord).where(UsageRecord.video_task_id == data["task_id"])
        ).one()
        subscription = db.get(Subscription, subscription_id)

    assert task.mode == "photo"
    assert task.video_mode == "photo"
    assert task.aspect_ratio == "auto"
    assert task.params["kind"] == "ecom_cutout"
    assert task.params["background"] == "transparent"
    assert task.params["source_asset_id"] == source["id"]
    assert task.params["source_storage_key"] == source["storage_key"]
    assert task.params["image_provider"] == "apimart"
    assert task.params["image_resolution"] == "1k"
    assert task.params["aspect_ratio"] == "auto"
    assert task.params["requested_aspect_ratio"] == "auto"
    assert task.params["apply_visible_label"] is True
    assert usage.status == "reserved"
    assert usage.credits == 80
    assert subscription.quota_credits_reserved == 80


def test_ecom_cutout_batch_clamps_to_20_and_fans_out_independent_photo_tasks(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        sources = [
            _seed_source_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"batch-product-{index:02d}",
            )
            for index in range(20)
        ]

    enqueued = _stub_image_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/ecom-images/cutout/batch",
            json={
                "items": [
                    {
                        "source_asset_id": source["id"],
                        "background": "white",
                        "aspect_ratio": "16:9" if index % 2 == 0 else "3:4",
                    }
                    for index, source in enumerate(sources)
                ]
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["batch_id"]
    assert len(data["tasks"]) == 20
    assert len(enqueued) == 20
    assert [item["source_asset_id"] for item in data["tasks"]] == [
        source["id"] for source in sources[:20]
    ]
    assert all(item["status"] == "queued" for item in data["tasks"])
    assert {call["args"][0]["batch_id"] for call in enqueued} == {data["batch_id"]}
    assert {call["args"][0]["kind"] for call in enqueued} == {"ecom_cutout"}
    assert {call["args"][0]["image_provider"] for call in enqueued} == {"apimart"}
    assert {call["args"][0]["image_resolution"] for call in enqueued} == {"1k"}
    assert {call["args"][0]["aspect_ratio"] for call in enqueued} == {"16:9", "3:4"}

    with auth_db() as db:
        task_count = db.scalar(
            select(func.count()).select_from(VideoTask).where(VideoTask.mode == "photo")
        )
        batch_rows = db.scalar(select(func.count()).select_from(BatchJob))
        reserved_count = db.scalar(
            select(func.count()).select_from(UsageRecord).where(UsageRecord.status == "reserved")
        )
        subscription = db.scalars(select(Subscription)).one()

    assert task_count == 20
    assert batch_rows == 0
    assert reserved_count == 20
    assert subscription.quota_credits_reserved == 1600


def test_ecom_image_ratio_defaults_square_and_rejects_unknown(auth_context) -> None:
    from pydantic import ValidationError

    from app.schemas.ecom_images import EcomCutoutRequest, EcomModelRequest

    cutout = EcomCutoutRequest(source_asset_id="product-cutout-source")
    model = EcomModelRequest(
        source_asset_id="product-model-source",
        gender="any",
        style_id="studio_white",
    )

    assert cutout.aspect_ratio == "1:1"
    assert model.aspect_ratio == "1:1"
    with pytest.raises(ValidationError):
        EcomCutoutRequest(
            source_asset_id="product-cutout-source",
            aspect_ratio="5:4",
        )


def test_ecom_cutout_rejects_cross_tenant_source_asset(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        other_tenant = Tenant(id="other-tenant", slug="other", name="Other")
        db.add(other_tenant)
        db.flush()
        _seed_source_asset(db, tenant_id=other_tenant.id, asset_id="foreign-product")

    enqueued = _stub_image_task(monkeypatch)
    resp = TestClient(app).post(
        "/api/v1/ecom-images/cutout",
        json={"source_asset_id": "foreign-product", "background": "white"},
        headers=auth_context["headers"],
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ECOM_SOURCE_ASSET_NOT_FOUND"
    assert enqueued == []


def test_ecom_cutout_rejects_non_image_source_asset(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        _seed_source_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="not-image-source",
            mime_type="video/mp4",
            asset_type="video",
        )

    enqueued = _stub_image_task(monkeypatch)
    resp = TestClient(app).post(
        "/api/v1/ecom-images/cutout",
        json={"source_asset_id": "not-image-source", "background": "white"},
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "ECOM_SOURCE_ASSET_INVALID"
    assert enqueued == []
