from __future__ import annotations

import asyncio
import inspect
import time
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
import redis
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select

from app.api.deps import get_object_storage, get_progress_store
from app.db.models import (
    Asset,
    EcomReplicateJob,
    EcomReplicateOutput,
    Subscription,
    Tenant,
    UsageRecord,
    VideoTask,
)
from app.main import app
from app.schemas.ecom_images import EcomReplicateRequest
from app.services import ecom_replicate, quota
from app.workers import image_gen


class _FakeStorage:
    def __init__(self) -> None:
        self.bucket = "ecom-replicate-test-bucket"
        self.saved: dict[str, tuple[bytes, str]] = {}
        self.put_calls: list[str] = []

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.put_calls.append(key)
        self.saved[key] = (content, content_type)
        return f"memory://{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.saved.get(key, (_png_bytes(), "image/png"))[0]

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        suffix = "&download=1" if download_filename else ""
        return f"https://storage.test/{key}?ttl={expires_in}{suffix}"


class _FakeProgressStore:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.history: list[tuple[str, dict]] = []

    def update(self, key: str, **fields) -> None:
        snapshot = dict(self.data.get(key, {}))
        snapshot.update({name: value for name, value in fields.items() if value is not None})
        snapshot["task_id"] = key
        self.data[key] = snapshot
        self.history.append((key, dict(snapshot)))

    def read(self, key: str):
        snapshot = self.data.get(key)
        return dict(snapshot) if snapshot else None


@pytest.fixture(autouse=True)
def _replicate_progress_store():
    store = _FakeProgressStore()
    previous = app.dependency_overrides.get(get_progress_store)
    app.dependency_overrides[get_progress_store] = lambda: store
    try:
        yield store
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_progress_store, None)
        else:
            app.dependency_overrides[get_progress_store] = previous


class _FakeReverseProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.product_identity_calls: list[dict] = []
        self.validation_calls: list[dict] = []

    async def reverse_image(self, payload: dict) -> dict:
        self.calls.append(payload)
        return {
            "reference_analysis_json": {
                "template_id": len(self.calls),
                "source_reference_image_id": payload.get("source_reference_image_id", "ref"),
                "layout": {
                    "product_angle": "front",
                    "product_position": "center",
                    "product_scale_ratio": "50%",
                    "composition": "vertical ecommerce layout",
                    "blank_space": "structured margins",
                },
                "background": {
                    "background_type": "flat graphic",
                    "color_tone": "green beige",
                    "background_elements": ["top banner", "center circle"],
                },
                "text_layer": {
                    "title_position": "top-left",
                    "subtitle_position": "below title",
                    "selling_points_position": "bottom center",
                    "text_hierarchy": "title > subtitle > selling points",
                },
                "lighting": {
                    "direction": "flat",
                    "shadow": "soft",
                    "highlight": "subtle",
                },
                "decorations": [
                    {"type": "circle", "position": "behind product", "style": "flat"}
                ],
            },
            "gpt_image_2_prompt": "Use the reference layout and replace the product.",
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "cost_cents": 14,
            "provider": "apimart",
            "model": "gemini-3.1-pro-preview",
        }

    async def analyze_product_identity(self, payload: dict) -> dict:
        self.product_identity_calls.append(payload)
        return {
            "product_identity": {
                "main_color": "celadon green",
                "material": "ceramic",
                "glaze": "glossy celadon glaze",
                "decorative_trim": "gold rim",
                "shape": "round plate, bowl, and handled cup",
                "key_pattern": "solid green with no blue marble veining",
            },
            "prompt_tokens": 800,
            "completion_tokens": 200,
            "total_tokens": 1000,
            "cost_cents": 8,
            "provider": "apimart",
            "model": "gemini-3.1-pro-preview",
        }

    async def validate_product_fidelity(self, payload: dict) -> dict:
        self.validation_calls.append(payload)
        return {
            "status": "passed",
            "passed": True,
            "checks": {
                "main_color_match": True,
                "pattern_match": True,
                "shape_match": True,
            },
            "reason": "Product identity matched.",
            "prompt_tokens": 600,
            "completion_tokens": 100,
            "total_tokens": 700,
            "cost_cents": 5,
            "provider": "apimart",
            "model": "gemini-3.1-pro-preview",
        }


class _ConcurrencyTrackingReverseProvider(_FakeReverseProvider):
    def __init__(self, *, delay_seconds: float = 0.02) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds
        self.in_flight = 0
        self.max_in_flight = 0
        self.reference_in_flight = 0
        self.max_reference_in_flight = 0

    async def reverse_image(self, payload: dict) -> dict:
        self.reference_in_flight += 1
        self.max_reference_in_flight = max(
            self.max_reference_in_flight,
            self.reference_in_flight,
        )
        try:
            return await self._track(super().reverse_image(payload))
        finally:
            self.reference_in_flight -= 1

    async def analyze_product_identity(self, payload: dict) -> dict:
        return await self._track(super().analyze_product_identity(payload))

    async def _track(self, operation) -> dict:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay_seconds)
            return await operation
        finally:
            self.in_flight -= 1


class _FailingReferenceAnalysisProvider(_FakeReverseProvider):
    def __init__(self, *, failing_asset_id: str) -> None:
        super().__init__()
        self.failing_asset_id = failing_asset_id

    async def reverse_image(self, payload: dict) -> dict:
        result = await super().reverse_image(payload)
        if payload.get("source_reference_image_id") == self.failing_asset_id:
            raise RuntimeError("reference analysis failed")
        return result


class _MismatchThenPassReverseProvider(_FakeReverseProvider):
    def __init__(self, *, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def validate_product_fidelity(self, payload: dict) -> dict:
        self.validation_calls.append(payload)
        failed = len(self.validation_calls) <= self.failures
        return {
            "status": "failed" if failed else "passed",
            "passed": not failed,
            "checks": {
                "main_color_match": not failed,
                "pattern_match": not failed,
                "shape_match": True,
            },
            "reason": (
                "Rendered product is blue-white marble instead of celadon green."
                if failed
                else "Product identity matched."
            ),
            "prompt_tokens": 600,
            "completion_tokens": 100,
            "total_tokens": 700,
            "cost_cents": 5,
            "provider": "apimart",
            "model": "gemini-3.1-pro-preview",
        }


class _ShapeAdvisoryReverseProvider(_FakeReverseProvider):
    async def validate_product_fidelity(self, payload: dict) -> dict:
        result = await super().validate_product_fidelity(payload)
        result["checks"]["shape_match"] = False
        result["reason"] = "Product identity matches; display arrangement differs."
        return result


class _FakeImageProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_image(self, payload: dict) -> dict:
        self.calls.append(payload)
        return {
            "image_bytes": _png_bytes(size=(3, 5)),
            "mime_type": "image/png",
            "provider": "apimart",
            "model": "gpt-image-2",
            "size": payload["size"],
            "resolution": "1k",
            "quality": "high",
            "mode": "edit",
            "credits": "0.06",
            "cost_cents": 4,
        }


class _FailingThenSuccessImageProvider:
    def __init__(self, *, failures: int) -> None:
        self.failures = failures
        self.calls: list[dict] = []

    async def generate_image(self, payload: dict) -> dict:
        self.calls.append(payload)
        if len(self.calls) <= self.failures:
            raise RuntimeError("quality validation failed")
        return {
            "image_bytes": _png_bytes(size=(3, 5), color=(180, 90, 20)),
            "mime_type": "image/png",
            "provider": "apimart",
            "model": "gpt-image-2",
            "size": payload["size"],
            "resolution": "1k",
            "quality": "high",
            "mode": "edit",
            "credits": "0.06",
            "cost_cents": 4,
        }


def _png_bytes(
    size: tuple[int, int] = (2, 2),
    color: tuple[int, int, int] = (20, 120, 220),
) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _seed_image_asset(
    db,
    *,
    tenant_id: str,
    asset_id: str,
    purpose: str = "ecom_ref",
    asset_type: str = "product_image",
    mime_type: str = "image/png",
) -> Asset:
    asset = Asset(
        id=asset_id,
        tenant_id=tenant_id,
        type=asset_type,
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/{asset_id}.png",
        mime_type=mime_type,
        size_bytes=len(_png_bytes()),
        status="ready",
        metadata_={"purpose": purpose},
    )
    db.add(asset)
    db.flush()
    return asset


def _patch_replicate_providers(monkeypatch, *, reverse=None, image=None):
    reverse = reverse or _FakeReverseProvider()
    image = image or _FakeImageProvider()

    def fake_resolve(_db, *, tenant_id, capability, provider=None):
        if capability == "reverse_prompt":
            assert provider in {None, "apimart-gemini"}
            return reverse
        if capability == "image":
            assert provider in {None, "apimart"}
            return image
        raise AssertionError(f"unexpected capability {capability}")

    monkeypatch.setattr("app.services.ecom_replicate.resolve_named_provider", fake_resolve)
    monkeypatch.setattr("app.workers.image_gen.resolve_named_provider", fake_resolve)
    return reverse, image


def _stub_replicate_task(monkeypatch):
    enqueued: list[dict] = []

    def fake_apply_async(*, args, task_id=None, queue=None):
        enqueued.append({"args": args, "task_id": task_id, "queue": queue})
        return SimpleNamespace(status="PENDING")

    monkeypatch.setattr(image_gen.generate_ecom_replicate_task, "apply_async", fake_apply_async)
    return enqueued


def _run_tracked_detail_plan(
    *,
    monkeypatch,
    auth_context,
    auth_db,
    prefix: str,
    concurrency: int,
    delay_seconds: float,
) -> tuple[object, _ConcurrencyTrackingReverseProvider]:
    reverse = _ConcurrencyTrackingReverseProvider(delay_seconds=delay_seconds)
    _patch_replicate_providers(monkeypatch, reverse=reverse)
    monkeypatch.setattr(
        ecom_replicate.settings,
        "engine_ecom_replicate_analysis_concurrency",
        concurrency,
    )
    storage = _FakeStorage()
    with auth_db() as db:
        references = [
            _seed_image_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"{prefix}-ref-{index}",
                purpose="ecom_ref",
            )
            for index in range(12)
        ]
        product = _seed_image_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id=f"{prefix}-product",
            purpose="ecom_product",
        )
        reference_ids = [asset.id for asset in references]
        product_id = product.id
        db.commit()

    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).post(
            "/api/v1/ecom-images/replicate",
            json={
                "reference_image_asset_ids": reference_ids,
                "product_image_asset_ids": [product_id],
                "product_info": {"name": "concurrency test product"},
                "selling_points": ["bounded parallel analysis"],
                "output_mode": "detail",
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)
    return response, reverse


def _create_main_replicate_plan(
    *,
    auth_context,
    auth_db,
    reference_id: str,
    product_id: str,
) -> dict:
    with auth_db() as db:
        ref = _seed_image_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id=reference_id,
        )
        product = _seed_image_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id=product_id,
        )
        ref_id = ref.id
        product_id = product.id
        subscription_id = db.scalars(select(Subscription.id)).one()
        db.commit()

    response = TestClient(app).post(
        "/api/v1/ecom-images/replicate",
        json={
            "reference_image_asset_ids": [ref_id],
            "product_image_asset_ids": [product_id],
            "product_info": {"name": "clean bottle"},
            "selling_points": ["large capacity"],
            "output_mode": "main",
        },
        headers=auth_context["headers"],
    )
    assert response.status_code == 201
    data = response.json()["data"]
    data["subscription_id"] = subscription_id
    return data


def _register_tenant(*, slug: str, email: str) -> dict:
    response = TestClient(app).post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": slug,
            "tenant_name": f"{slug} Studio",
            "email": email,
            "password": "secret-pass",
        },
    )
    assert response.status_code == 201
    data = response.json()["data"]
    return {
        "headers": {"Authorization": f"Bearer {data['token']['access_token']}"},
        "tenant_id": data["tenant"]["id"],
        "user_id": data["user"]["id"],
    }


@pytest.mark.parametrize(
    ("output_mode", "reference_count", "message"),
    [
        ("main", 6, "主图模式最多 5 张参考图"),
        ("detail", 13, "详情模式最多 12 张参考图"),
    ],
)
def test_ecom_replicate_rejects_mode_reference_limit_with_friendly_message(
    auth_context,
    output_mode: str,
    reference_count: int,
    message: str,
) -> None:
    response = TestClient(app).post(
        "/api/v1/ecom-images/replicate",
        json={
            "reference_image_asset_ids": [
                f"reference-{index}" for index in range(reference_count)
            ],
            "product_image_asset_ids": ["product-1"],
            "output_mode": output_mode,
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"] == message
    validation_messages = [item["msg"] for item in body["error"]["detail"]]
    assert message in validation_messages
    assert all("List should have at most" not in item for item in validation_messages)


def test_ecom_replicate_preserves_product_limit_and_nonempty_references() -> None:
    accepted = EcomReplicateRequest.model_validate(
        {
            "reference_image_asset_ids": ["reference-1"],
            "product_image_asset_ids": [
                f"product-{index}" for index in range(4)
            ],
        }
    )
    assert len(accepted.product_image_asset_ids) == 4

    with pytest.raises(ValidationError) as product_error:
        EcomReplicateRequest.model_validate(
            {
                "reference_image_asset_ids": ["reference-1"],
                "product_image_asset_ids": [
                    f"product-{index}" for index in range(5)
                ],
            }
        )
    assert product_error.value.errors()[0]["loc"] == ("product_image_asset_ids",)

    with pytest.raises(ValidationError) as reference_error:
        EcomReplicateRequest.model_validate(
            {
                "reference_image_asset_ids": [],
                "product_image_asset_ids": ["product-1"],
            }
        )
    assert reference_error.value.errors()[0]["loc"] == ("reference_image_asset_ids",)


def test_ecom_replicate_plan_bounds_and_overlaps_full_detail_analysis(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    response, reverse = _run_tracked_detail_plan(
        monkeypatch=monkeypatch,
        auth_context=auth_context,
        auth_db=auth_db,
        prefix="bounded-concurrency",
        concurrency=3,
        delay_seconds=0.02,
    )

    assert response.status_code == 201
    assert len(reverse.calls) == 12
    assert len(reverse.product_identity_calls) == 1
    assert reverse.max_reference_in_flight > 1
    assert reverse.max_in_flight == 3


def test_ecom_replicate_analysis_respects_configured_concurrency(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    serial_response, serial = _run_tracked_detail_plan(
        monkeypatch=monkeypatch,
        auth_context=auth_context,
        auth_db=auth_db,
        prefix="serial-timing",
        concurrency=1,
        delay_seconds=0.05,
    )
    parallel_response, parallel = _run_tracked_detail_plan(
        monkeypatch=monkeypatch,
        auth_context=auth_context,
        auth_db=auth_db,
        prefix="parallel-timing",
        concurrency=4,
        delay_seconds=0.05,
    )

    assert serial_response.status_code == 201
    assert parallel_response.status_code == 201
    assert serial.max_in_flight == 1
    assert parallel.max_in_flight == 4


def test_ecom_replicate_plan_fails_when_one_reference_analysis_fails(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    failing_asset_id = "failing-analysis-ref"
    reverse = _FailingReferenceAnalysisProvider(failing_asset_id=failing_asset_id)
    _patch_replicate_providers(monkeypatch, reverse=reverse)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        with auth_db() as db:
            references = [
                _seed_image_asset(
                    db,
                    tenant_id=auth_context["tenant_id"],
                    asset_id=asset_id,
                    purpose="ecom_ref",
                )
                for asset_id in ("successful-analysis-ref", failing_asset_id)
            ]
            product = _seed_image_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id="failed-plan-product",
                purpose="ecom_product",
            )
            reference_ids = [asset.id for asset in references]
            product_id = product.id
            db.commit()

        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/v1/ecom-images/replicate",
            json={
                "reference_image_asset_ids": reference_ids,
                "product_image_asset_ids": [product_id],
                "product_info": {"name": "failure propagation product"},
                "selling_points": ["no partial plans"],
                "output_mode": "main",
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 500
    with auth_db() as db:
        assert db.query(EcomReplicateJob).count() == 0


def test_ecom_replicate_plan_creates_plan_ready_job_with_total_price(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    reverse, _image = _patch_replicate_providers(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        with auth_db() as db:
            refs = [
                _seed_image_asset(
                    db,
                    tenant_id=auth_context["tenant_id"],
                    asset_id=f"rep-ref-{index}",
                    purpose="ecom_ref",
                )
                for index in range(5)
            ]
            product = _seed_image_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id="rep-product",
                purpose="ecom_product",
            )
            ref_ids = [asset.id for asset in refs]
            product_id = product.id
            subscription_id = db.scalars(select(Subscription.id)).one()
            db.commit()

        response = TestClient(app).post(
            "/api/v1/ecom-images/replicate",
            json={
                "reference_image_asset_ids": ref_ids,
                "product_image_asset_ids": [product_id],
                "product_info": {"name": "清爽随行杯"},
                "selling_points": ["大容量", "便携提手", "清洗方便"],
                "output_mode": "main",
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["status"] == "plan_ready"
    assert body["output_count"] == 5
    assert body["total_credits"] == 650
    assert body["credit_rate"] == 130
    assert len(body["plan"]["outputs"]) == 5
    assert len(reverse.calls) == 5

    with auth_db() as db:
        job = db.get(EcomReplicateJob, body["job_id"])
        outputs = db.scalars(
            select(EcomReplicateOutput).where(EcomReplicateOutput.job_id == job.id)
        ).all()
        subscription = db.get(Subscription, subscription_id)
        usage_count = db.query(UsageRecord).count()

    assert job.status == "plan_ready"
    assert job.output_mode == "main"
    assert job.output_count == 5
    assert job.total_credits == 650
    assert len(outputs) == 5
    assert outputs[-1].theme == "white_background"
    assert subscription.quota_credits_used == 0
    assert usage_count >= 1
    assert any(record["template_id"] for record in job.reference_analysis_json)


def test_ecom_replicate_plan_anchors_every_prompt_to_product_identity(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    reverse, _image = _patch_replicate_providers(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        with auth_db() as db:
            reference = _seed_image_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id="identity-reference-blue-marble",
                purpose="ecom_ref",
            )
            product = _seed_image_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id="identity-product-green-celadon",
                purpose="ecom_product",
            )
            reference_id = reference.id
            product_id = product.id
            db.commit()

        response = TestClient(app).post(
            "/api/v1/ecom-images/replicate",
            json={
                "reference_image_asset_ids": [reference_id],
                "product_image_asset_ids": [product_id],
                "product_info": {"name": "绿色青瓷金边餐具"},
                "selling_points": ["青瓷釉面", "金边工艺", "雅致器型"],
                "output_mode": "main",
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 201
    assert len(reverse.product_identity_calls) == 1
    prompts = [item["prompt"] for item in response.json()["data"]["plan"]["outputs"]]
    assert len(prompts) == 5
    for prompt in prompts:
        assert "PRODUCT IDENTITY" in prompt
        assert "celadon green" in prompt
        assert "glossy celadon glaze" in prompt
        assert "gold rim" in prompt
        assert "MUST be exactly the user's product" in prompt
        assert "COMPLETELY replace and remove the product shown in the reference image" in prompt
        assert "Product color MUST be celadon green" in prompt


@pytest.mark.parametrize("reference_count", [1, 12])
def test_ecom_replicate_detail_supports_short_and_full_reference_sets(
    monkeypatch,
    auth_context,
    auth_db,
    reference_count: int,
) -> None:
    _patch_replicate_providers(monkeypatch)
    with auth_db() as db:
        refs = [
            _seed_image_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"detail-ref-{index}",
                purpose="ecom_ref",
            )
            for index in range(reference_count)
        ]
        product = _seed_image_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="detail-product",
            purpose="ecom_product",
        )
        ref_ids = [asset.id for asset in refs]
        product_id = product.id
        db.commit()

    response = TestClient(app).post(
        "/api/v1/ecom-images/replicate",
        json={
            "reference_image_asset_ids": ref_ids,
            "product_image_asset_ids": [product_id],
            "product_info": {"name": "清爽随行杯"},
            "selling_points": ["大容量"],
            "output_mode": "detail",
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["output_count"] == 12
    assert data["total_credits"] == 1560
    assert data["credit_rate"] == 130
    themes = [item["theme"] for item in data["plan"]["outputs"]]
    assert len(themes) == 12
    assert len(set(themes)) == 12
    assert {
        item["reference_asset_id"] for item in data["plan"]["outputs"]
    } == set(ref_ids)


def test_ecom_replicate_confirm_charges_once_and_enqueues_generation(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    enqueued = _stub_replicate_task(monkeypatch)
    with auth_db() as db:
        refs = [
            _seed_image_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id=f"confirm-ref-{index}",
            )
            for index in range(4)
        ]
        product = _seed_image_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="confirm-product",
        )
        ref_ids = [asset.id for asset in refs]
        product_id = product.id
        subscription_id = db.scalars(select(Subscription.id)).one()
        db.commit()

    client = TestClient(app)
    plan = client.post(
        "/api/v1/ecom-images/replicate",
        json={
            "reference_image_asset_ids": ref_ids,
            "product_image_asset_ids": [product_id],
            "product_info": {"name": "清爽随行杯"},
            "selling_points": ["大容量"],
            "output_mode": "main",
        },
        headers=auth_context["headers"],
    ).json()["data"]

    first = client.post(
        f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
        headers=auth_context["headers"],
    )
    second = client.post(
        f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
        headers=auth_context["headers"],
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["status"] == "generating"
    assert second.json()["data"]["status"] == "generating"
    assert len(enqueued) == 1
    assert enqueued[0]["args"] == [plan["job_id"]]
    assert enqueued[0]["queue"] == "image"

    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        usages = db.scalars(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.provider == "huading",
                UsageRecord.model == "ecom-replicate",
            )
        ).all()

    assert subscription.quota_credits_used == 650
    assert subscription.quota_credits_reserved == 0
    assert len(usages) == 1
    assert usages[0].credits == 650
    assert usages[0].quantity == 5
    assert usages[0].status == "settled"


def test_ecom_replicate_confirm_uses_row_locks_for_concurrent_safety() -> None:
    source = "".join(
        inspect.getsource(function)
        for function in (
            ecom_replicate.confirm_replicate_job,
            quota.consume_active_quota,
            quota._active_subscription_for_update,
        )
    )

    assert source.count(".with_for_update()") >= 2
    assert "Subscription" in source
    assert "status != \"plan_ready\"" in source or "status != 'plan_ready'" in source


def test_ecom_replicate_rejects_banned_copy_without_charge(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    with auth_db() as db:
        ref = _seed_image_asset(db, tenant_id=auth_context["tenant_id"], asset_id="ban-ref")
        product = _seed_image_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="ban-product",
        )
        ref_id = ref.id
        product_id = product.id
        subscription_id = db.scalars(select(Subscription.id)).one()
        db.commit()

    response = TestClient(app).post(
        "/api/v1/ecom-images/replicate",
        json={
            "reference_image_asset_ids": [ref_id],
            "product_image_asset_ids": [product_id],
            "product_info": {"name": "清爽随行杯"},
            "selling_points": ["全网最低", "大容量"],
            "output_mode": "main",
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ECOM_REPLICATE_TEXT_FORBIDDEN"
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_used == 0
        assert db.query(UsageRecord).count() == 0


def test_ecom_replicate_rejects_full_section_13_banned_terms(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    banned_terms = [
        "\u7b2c\u4e00",
        "\u9876\u7ea7",
        "\u6700\u597d",
        "\u5929\u82b1\u677f",
        "\u7206\u6b3e",
        "\u552f\u4e00",
        "\u72ec\u5bb6",
        "\u56fd\u5bb6\u7ea7",
        "\u6c38\u4e45",
        "100%",
        "\u7edd\u5bf9",
        "\u7acb\u523b\u89c1\u6548",
        "\u5168\u7f51\u6700\u4f4e",
        "\u9500\u91cf\u7b2c\u4e00",
    ]
    with auth_db() as db:
        ref = _seed_image_asset(db, tenant_id=auth_context["tenant_id"], asset_id="ban-ref-all")
        product = _seed_image_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="ban-product-all",
        )
        ref_id = ref.id
        product_id = product.id
        db.commit()

    client = TestClient(app)
    for index, term in enumerate(banned_terms):
        response = client.post(
            "/api/v1/ecom-images/replicate",
            json={
                "reference_image_asset_ids": [ref_id],
                "product_image_asset_ids": [product_id],
                "product_info": {"name": "clean bottle"},
                "selling_points": [f"safe copy {index}", term],
                "output_mode": "main",
            },
            headers=auth_context["headers"],
        )

        assert response.status_code == 422, term
        assert response.json()["error"]["code"] == "ECOM_REPLICATE_TEXT_FORBIDDEN"


def test_ecom_replicate_rejects_cross_tenant_reference_asset(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    with auth_db() as db:
        other_tenant = Tenant(id="rep-foreign-tenant", slug="rep-foreign", name="Foreign")
        db.add(other_tenant)
        db.flush()
        ref = _seed_image_asset(db, tenant_id=other_tenant.id, asset_id="foreign-ref")
        product = _seed_image_asset(
            db,
            tenant_id=auth_context["tenant_id"],
            asset_id="own-product",
        )
        ref_id = ref.id
        product_id = product.id
        db.commit()

    response = TestClient(app).post(
        "/api/v1/ecom-images/replicate",
        json={
            "reference_image_asset_ids": [ref_id],
            "product_image_asset_ids": [product_id],
            "product_info": {"name": "清爽随行杯"},
            "selling_points": ["大容量"],
            "output_mode": "main",
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ECOM_REPLICATE_ASSET_NOT_FOUND"


def test_ecom_replicate_worker_records_requested_actual_dimensions_and_raw_bytes(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _reverse, image = _patch_replicate_providers(monkeypatch)
    _stub_replicate_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    try:
        with auth_db() as db:
            ref = _seed_image_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id="worker-ref",
            )
            product = _seed_image_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                asset_id="worker-product",
            )
            ref_id = ref.id
            product_id = product.id
            db.commit()

        client = TestClient(app)
        plan = client.post(
            "/api/v1/ecom-images/replicate",
            json={
                "reference_image_asset_ids": [ref_id],
                "product_image_asset_ids": [product_id],
                "product_info": {"name": "清爽随行杯"},
                "selling_points": ["大容量"],
                "output_mode": "main",
                "size": "768x1024",
            },
            headers=auth_context["headers"],
        ).json()["data"]
        client.post(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
            headers=auth_context["headers"],
        )

        result = image_gen.run_ecom_replicate_generation(plan["job_id"])
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert result["status"] == "SUCCESS"
    assert image.calls
    assert image.calls[0]["size"] == "768x1024"
    assert len(image.calls[0]["image_urls"]) == 2

    with auth_db() as db:
        job = db.get(EcomReplicateJob, plan["job_id"])
        outputs = db.scalars(
            select(EcomReplicateOutput).where(EcomReplicateOutput.job_id == job.id)
        ).all()
        render_costs = db.scalars(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.provider == "apimart",
                UsageRecord.model == "gpt-image-2",
            )
        ).all()

    assert job.status == "completed"
    assert all(output.status == "succeeded" for output in outputs)
    assert outputs[0].requested_size == "768x1024"
    assert outputs[0].actual_width == 3
    assert outputs[0].actual_height == 5
    assert outputs[0].asset_id
    assert storage.saved[outputs[0].storage_key][0] == _png_bytes(size=(3, 5))
    assert len(render_costs) == 5
    assert all(record.cost_cents == 4 for record in render_costs)


def test_ecom_replicate_worker_heartbeats_each_blocking_output_and_poll_exposes_it(
    monkeypatch,
    auth_context,
    auth_db,
    _replicate_progress_store,
) -> None:
    _patch_replicate_providers(monkeypatch)
    _stub_replicate_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(
        image_gen,
        "build_progress_store",
        lambda _url: _replicate_progress_store,
    )
    monkeypatch.setattr(
        image_gen.settings,
        "engine_gen_heartbeat_interval_seconds",
        0.01,
        raising=False,
    )

    def slow_successful_render(
        _db,
        *,
        output,
        **_kwargs,
    ) -> None:
        time.sleep(0.035)
        output.status = "succeeded"
        output.updated_at = datetime.now(UTC)

    monkeypatch.setattr(
        image_gen,
        "_render_ecom_replicate_output_once",
        slow_successful_render,
    )
    try:
        plan = _create_main_replicate_plan(
            auth_context=auth_context,
            auth_db=auth_db,
            reference_id="heartbeat-ref",
            product_id="heartbeat-product",
        )
        TestClient(app).post(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
            headers=auth_context["headers"],
        )

        result = image_gen.run_ecom_replicate_generation(plan["job_id"])
        response = TestClient(app).get(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert result["status"] == "SUCCESS"
    scoped_id = f"{auth_context['tenant_id']}:{plan['job_id']}"
    heartbeats = [
        snapshot
        for key, snapshot in _replicate_progress_store.history
        if key == scoped_id and snapshot.get("heartbeat_at")
    ]
    assert len(heartbeats) >= 5
    assert response.status_code == 200
    assert response.json()["data"]["heartbeat_at"] == heartbeats[-1]["heartbeat_at"]


def test_ecom_replicate_get_returns_generating_job_outputs_without_download_url(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    _stub_replicate_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        plan = _create_main_replicate_plan(
            auth_context=auth_context,
            auth_db=auth_db,
            reference_id="polling-ref",
            product_id="polling-product",
        )
        confirm = TestClient(app).post(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
            headers=auth_context["headers"],
        )
        assert confirm.status_code == 202

        response = TestClient(app).get(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["job_id"] == plan["job_id"]
    assert data["status"] == "generating"
    assert len(data["plan"]["outputs"]) == 5
    assert {output["status"] for output in data["plan"]["outputs"]} == {"planned"}
    assert all(output["asset_id"] is None for output in data["plan"]["outputs"])
    assert all(output["actual_width"] is None for output in data["plan"]["outputs"])
    assert all(output["actual_height"] is None for output in data["plan"]["outputs"])
    assert all(output["download_url"] is None for output in data["plan"]["outputs"])


def test_ecom_replicate_get_degrades_when_heartbeat_store_is_unavailable(
    monkeypatch,
    auth_context,
    auth_db,
    _replicate_progress_store,
) -> None:
    class _UnavailableProgressStore:
        def read(self, _key: str):
            raise redis.RedisError("redis unavailable")

    _patch_replicate_providers(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        plan = _create_main_replicate_plan(
            auth_context=auth_context,
            auth_db=auth_db,
            reference_id="heartbeat-outage-ref",
            product_id="heartbeat-outage-product",
        )
        app.dependency_overrides[get_progress_store] = lambda: _UnavailableProgressStore()
        response = TestClient(app).get(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides[get_progress_store] = lambda: _replicate_progress_store
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    assert response.json()["data"]["heartbeat_at"] is None


def test_ecom_replicate_get_returns_completed_outputs_with_download_urls(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    _stub_replicate_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    try:
        plan = _create_main_replicate_plan(
            auth_context=auth_context,
            auth_db=auth_db,
            reference_id="completed-get-ref",
            product_id="completed-get-product",
        )
        TestClient(app).post(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
            headers=auth_context["headers"],
        )
        result = image_gen.run_ecom_replicate_generation(plan["job_id"])
        assert result["status"] == "SUCCESS"

        response = TestClient(app).get(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "completed"
    first_output = data["plan"]["outputs"][0]
    assert first_output["status"] == "succeeded"
    assert first_output["asset_id"]
    assert first_output["actual_width"] == 3
    assert first_output["actual_height"] == 5
    assert first_output["download_url"].startswith(
        f"https://storage.test/tenants/{auth_context['tenant_id']}/ecom-replicate/"
        f"{plan['job_id']}/00.png?ttl="
    )
    assert "download=1" in first_output["download_url"]
    assert all(output["download_url"] for output in data["plan"]["outputs"])


def test_ecom_replicate_get_cross_tenant_returns_404(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    _stub_replicate_task(monkeypatch)
    plan = _create_main_replicate_plan(
        auth_context=auth_context,
        auth_db=auth_db,
        reference_id="cross-get-ref",
        product_id="cross-get-product",
    )
    other = _register_tenant(slug="other-get", email="other-get@example.com")

    response = TestClient(app).get(
        f"/api/v1/ecom-images/replicate/{plan['job_id']}",
        headers=other["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ECOM_REPLICATE_JOB_NOT_FOUND"


def test_soft_deleted_ecom_replicate_job_is_not_available_from_native_detail(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    _stub_replicate_task(monkeypatch)
    plan = _create_main_replicate_plan(
        auth_context=auth_context,
        auth_db=auth_db,
        reference_id="soft-deleted-detail-ref",
        product_id="soft-deleted-detail-product",
    )
    with auth_db() as db:
        job = db.get(EcomReplicateJob, plan["job_id"])
        job.status = "failed"
        db.commit()

    client = TestClient(app)
    deleted = client.delete(
        f"/api/v1/history/images/ecom_detail/{plan['job_id']}",
        headers=auth_context["headers"],
    )
    response = client.get(
        f"/api/v1/ecom-images/replicate/{plan['job_id']}",
        headers=auth_context["headers"],
    )

    assert deleted.status_code == 200
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ECOM_REPLICATE_JOB_NOT_FOUND"


def test_soft_deleted_ecom_replicate_job_cannot_be_confirmed(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    enqueued = _stub_replicate_task(monkeypatch)
    plan = _create_main_replicate_plan(
        auth_context=auth_context,
        auth_db=auth_db,
        reference_id="soft-deleted-confirm-ref",
        product_id="soft-deleted-confirm-product",
    )
    with auth_db() as db:
        job = db.get(EcomReplicateJob, plan["job_id"])
        job.status = "failed"
        db.commit()

    client = TestClient(app)
    deleted = client.delete(
        f"/api/v1/history/images/ecom_detail/{plan['job_id']}",
        headers=auth_context["headers"],
    )
    response = client.post(
        f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
        headers=auth_context["headers"],
    )

    assert deleted.status_code == 200
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ECOM_REPLICATE_JOB_NOT_FOUND"
    assert enqueued == []


def test_ecom_replicate_worker_retries_single_failed_output_without_extra_charge(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    image = _FailingThenSuccessImageProvider(failures=1)
    _patch_replicate_providers(monkeypatch, image=image)
    _stub_replicate_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(image_gen.settings, "engine_ecom_replicate_max_retry", 2, raising=False)
    try:
        plan = _create_main_replicate_plan(
            auth_context=auth_context,
            auth_db=auth_db,
            reference_id="retry-ref",
            product_id="retry-product",
        )
        TestClient(app).post(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
            headers=auth_context["headers"],
        )

        result = image_gen.run_ecom_replicate_generation(plan["job_id"])
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert result["status"] == "SUCCESS"
    assert len(image.calls) == 6
    with auth_db() as db:
        job = db.get(EcomReplicateJob, plan["job_id"])
        outputs = db.scalars(
            select(EcomReplicateOutput).where(EcomReplicateOutput.job_id == job.id)
        ).all()
        tenant_charges = db.scalars(
            select(UsageRecord).where(
                UsageRecord.provider == "huading",
                UsageRecord.model == "ecom-replicate",
            )
        ).all()

    assert job.status == "completed"
    assert outputs[0].retry_count == 1
    assert all(output.status == "succeeded" for output in outputs)
    assert len(tenant_charges) == 1
    assert tenant_charges[0].credits == 650


def test_ecom_replicate_product_mismatch_retries_before_persisting_output(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    reverse = _MismatchThenPassReverseProvider(failures=1)
    image = _FakeImageProvider()
    _patch_replicate_providers(monkeypatch, reverse=reverse, image=image)
    _stub_replicate_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(image_gen.settings, "engine_ecom_replicate_max_retry", 2, raising=False)
    try:
        plan = _create_main_replicate_plan(
            auth_context=auth_context,
            auth_db=auth_db,
            reference_id="quality-retry-reference",
            product_id="quality-retry-product",
        )
        TestClient(app).post(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
            headers=auth_context["headers"],
        )

        result = image_gen.run_ecom_replicate_generation(plan["job_id"])
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert result["status"] == "SUCCESS"
    assert len(image.calls) == 6
    assert len(reverse.validation_calls) == 6
    assert len(storage.put_calls) == 5
    with auth_db() as db:
        outputs = db.scalars(
            select(EcomReplicateOutput)
            .where(EcomReplicateOutput.job_id == plan["job_id"])
            .order_by(EcomReplicateOutput.index.asc())
        ).all()
        tenant_charges = db.scalars(
            select(UsageRecord).where(
                UsageRecord.provider == "huading",
                UsageRecord.model == "ecom-replicate",
            )
        ).all()
        render_costs = db.scalars(
            select(UsageRecord).where(
                UsageRecord.provider == "apimart",
                UsageRecord.model == "gpt-image-2",
            )
        ).all()

    assert outputs[0].retry_count == 1
    assert [item["status"] for item in outputs[0].validation_json["attempts"]] == [
        "failed",
        "passed",
    ]
    assert all(output.status == "succeeded" for output in outputs)
    assert len(tenant_charges) == 1
    assert tenant_charges[0].credits == 650
    assert len(render_costs) == 6


def test_ecom_replicate_shape_advisory_does_not_trigger_retry(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    reverse = _ShapeAdvisoryReverseProvider()
    image = _FakeImageProvider()
    _patch_replicate_providers(monkeypatch, reverse=reverse, image=image)
    _stub_replicate_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(image_gen.settings, "engine_ecom_replicate_max_retry", 2, raising=False)
    try:
        plan = _create_main_replicate_plan(
            auth_context=auth_context,
            auth_db=auth_db,
            reference_id="shape-advisory-reference",
            product_id="shape-advisory-product",
        )
        TestClient(app).post(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
            headers=auth_context["headers"],
        )

        result = image_gen.run_ecom_replicate_generation(plan["job_id"])
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert result["status"] == "SUCCESS"
    assert len(image.calls) == 5
    assert len(reverse.validation_calls) == 5
    assert len(storage.put_calls) == 5
    with auth_db() as db:
        outputs = db.scalars(
            select(EcomReplicateOutput)
            .where(EcomReplicateOutput.job_id == plan["job_id"])
            .order_by(EcomReplicateOutput.index.asc())
        ).all()

    assert all(output.status == "succeeded" for output in outputs)
    assert all(output.retry_count == 0 for output in outputs)
    assert all(output.validation_json["status"] == "passed" for output in outputs)
    assert all(
        output.validation_json["checks"]["shape_match"] is False
        for output in outputs
    )


def test_ecom_replicate_product_mismatch_exhaustion_never_persists_bad_output(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    reverse = _MismatchThenPassReverseProvider(failures=3)
    image = _FakeImageProvider()
    _patch_replicate_providers(monkeypatch, reverse=reverse, image=image)
    _stub_replicate_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(image_gen.settings, "engine_ecom_replicate_max_retry", 2, raising=False)
    try:
        plan = _create_main_replicate_plan(
            auth_context=auth_context,
            auth_db=auth_db,
            reference_id="quality-failed-reference",
            product_id="quality-failed-product",
        )
        TestClient(app).post(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
            headers=auth_context["headers"],
        )

        result = image_gen.run_ecom_replicate_generation(plan["job_id"])
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert result["status"] == "PARTIAL_FAILURE"
    assert len(image.calls) == 7
    assert len(reverse.validation_calls) == 7
    assert len(storage.put_calls) == 4
    with auth_db() as db:
        outputs = db.scalars(
            select(EcomReplicateOutput)
            .where(EcomReplicateOutput.job_id == plan["job_id"])
            .order_by(EcomReplicateOutput.index.asc())
        ).all()

    rejected = outputs[0]
    assert rejected.status == "failed"
    assert rejected.asset_id is None
    assert rejected.storage_key is None
    assert rejected.retry_count == 3
    assert rejected.error_code == "ECOM_REPLICATE_PRODUCT_MISMATCH"
    assert [item["status"] for item in rejected.validation_json["attempts"]] == [
        "failed",
        "failed",
        "failed",
    ]
    assert all(output.status == "succeeded" for output in outputs[1:])


def test_ecom_replicate_worker_marks_partial_failed_after_retry_exhaustion(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    image = _FailingThenSuccessImageProvider(failures=3)
    _patch_replicate_providers(monkeypatch, image=image)
    _stub_replicate_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(image_gen.settings, "engine_ecom_replicate_max_retry", 2, raising=False)
    try:
        plan = _create_main_replicate_plan(
            auth_context=auth_context,
            auth_db=auth_db,
            reference_id="partial-ref",
            product_id="partial-product",
        )
        TestClient(app).post(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
            headers=auth_context["headers"],
        )

        result = image_gen.run_ecom_replicate_generation(plan["job_id"])
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert result["status"] == "PARTIAL_FAILURE"
    with auth_db() as db:
        job = db.get(EcomReplicateJob, plan["job_id"])
        outputs = db.scalars(
            select(EcomReplicateOutput)
            .where(EcomReplicateOutput.job_id == job.id)
            .order_by(EcomReplicateOutput.index.asc())
        ).all()
        tenant_charges = db.scalars(
            select(UsageRecord).where(
                UsageRecord.provider == "huading",
                UsageRecord.model == "ecom-replicate",
            )
        ).all()

    assert job.status == "partial_failed"
    assert outputs[0].status == "failed"
    assert outputs[0].retry_count == 3
    assert all(output.status == "succeeded" for output in outputs[1:])
    assert len(tenant_charges) == 1


def test_ecom_replicate_manual_output_retry_requeues_failed_output_without_charge(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    enqueued = _stub_replicate_task(monkeypatch)
    plan = _create_main_replicate_plan(
        auth_context=auth_context,
        auth_db=auth_db,
        reference_id="manual-retry-ref",
        product_id="manual-retry-product",
    )
    client = TestClient(app)
    confirm = client.post(
        f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
        headers=auth_context["headers"],
    )
    assert confirm.status_code == 202
    with auth_db() as db:
        job = db.get(EcomReplicateJob, plan["job_id"])
        output = db.scalar(
            select(EcomReplicateOutput).where(
                EcomReplicateOutput.job_id == job.id,
                EcomReplicateOutput.index == 0,
            )
        )
        job.status = "partial_failed"
        output.status = "failed"
        output.retry_count = 3
        output.error_code = "ECOM_REPLICATE_RENDER_FAILED"
        output.error_message = "quality validation failed"
        db.commit()
    enqueued.clear()

    response = client.post(
        f"/api/v1/ecom-images/replicate/{plan['job_id']}/outputs/0/retry",
        headers=auth_context["headers"],
    )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["index"] == 0
    assert data["status"] == "planned"
    assert enqueued == [{"args": [plan["job_id"], 0], "task_id": plan["job_id"], "queue": "image"}]
    with auth_db() as db:
        tenant_charges = db.scalars(
            select(UsageRecord).where(
                UsageRecord.provider == "huading",
                UsageRecord.model == "ecom-replicate",
            )
        ).all()
        output = db.scalar(
            select(EcomReplicateOutput).where(
                EcomReplicateOutput.job_id == plan["job_id"],
                EcomReplicateOutput.index == 0,
            )
        )

    assert len(tenant_charges) == 1
    assert output.status == "planned"
    assert output.error_code is None


def test_soft_deleted_ecom_replicate_job_output_cannot_be_retried(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    enqueued = _stub_replicate_task(monkeypatch)
    plan = _create_main_replicate_plan(
        auth_context=auth_context,
        auth_db=auth_db,
        reference_id="soft-deleted-retry-ref",
        product_id="soft-deleted-retry-product",
    )
    client = TestClient(app)
    assert (
        client.post(
            f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
            headers=auth_context["headers"],
        ).status_code
        == 202
    )
    with auth_db() as db:
        job = db.get(EcomReplicateJob, plan["job_id"])
        output = db.scalar(
            select(EcomReplicateOutput).where(
                EcomReplicateOutput.job_id == job.id,
                EcomReplicateOutput.index == 0,
            )
        )
        job.status = "partial_failed"
        output.status = "failed"
        db.commit()
    enqueued.clear()

    deleted = client.delete(
        f"/api/v1/history/images/ecom_detail/{plan['job_id']}",
        headers=auth_context["headers"],
    )
    response = client.post(
        f"/api/v1/ecom-images/replicate/{plan['job_id']}/outputs/0/retry",
        headers=auth_context["headers"],
    )

    assert deleted.status_code == 200
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ECOM_REPLICATE_JOB_NOT_FOUND"
    assert enqueued == []
    with auth_db() as db:
        output = db.scalar(
            select(EcomReplicateOutput).where(
                EcomReplicateOutput.job_id == plan["job_id"],
                EcomReplicateOutput.index == 0,
            )
        )
        assert output.status == "failed"


def test_ecom_replicate_manual_output_retry_rejects_non_failed_output(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    _patch_replicate_providers(monkeypatch)
    _stub_replicate_task(monkeypatch)
    plan = _create_main_replicate_plan(
        auth_context=auth_context,
        auth_db=auth_db,
        reference_id="manual-reject-ref",
        product_id="manual-reject-product",
    )
    TestClient(app).post(
        f"/api/v1/ecom-images/replicate/{plan['job_id']}/confirm",
        headers=auth_context["headers"],
    )

    response = TestClient(app).post(
        f"/api/v1/ecom-images/replicate/{plan['job_id']}/outputs/0/retry",
        headers=auth_context["headers"],
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ECOM_REPLICATE_OUTPUT_NOT_RETRYABLE"


def test_poster_endpoints_are_disabled_and_existing_history_hidden(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)
    app.dependency_overrides[get_progress_store] = lambda: _FakeProgressStore()
    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    assert (
        client.get(
            "/api/v1/ecom-images/poster-templates",
            headers=auth_context["headers"],
        ).status_code
        == 410
    )
    assert (
        client.post(
            "/api/v1/ecom-images/poster",
            json={
                "source_asset_id": "any",
                "template_id": "minimal",
                "title": "上新",
                "subtitle": "福利",
            },
            headers=auth_context["headers"],
        ).status_code
        == 410
    )

    with auth_db() as db:
        db.add_all(
            [
                VideoTask(
                    id="hidden-poster-task",
                    tenant_id=auth_context["tenant_id"],
                    created_by_user_id=auth_context["user_id"],
                    status="done",
                    mode="photo",
                    video_mode="photo",
                    progress=100,
                    topic="hidden poster",
                    params={"kind": "ecom_poster"},
                    storage_key=f"tenants/{auth_context['tenant_id']}/photos/poster.png",
                ),
                VideoTask(
                    id="visible-photo-task",
                    tenant_id=auth_context["tenant_id"],
                    created_by_user_id=auth_context["user_id"],
                    status="done",
                    mode="photo",
                    video_mode="photo",
                    progress=100,
                    topic="visible photo",
                    params={"kind": "ecom_cutout"},
                    storage_key=f"tenants/{auth_context['tenant_id']}/photos/photo.png",
                ),
            ]
        )
        db.commit()

    response = client.get(
        "/api/v1/videos?mode=photo",
        headers=auth_context["headers"],
    )
    app.dependency_overrides.pop(get_progress_store, None)
    app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]["items"]]
    assert ids == ["visible-photo-task"]
    with auth_db() as db:
        assert db.get(VideoTask, "hidden-poster-task") is not None
