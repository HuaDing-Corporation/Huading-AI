import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import httpx
import openai
from PIL import Image
from sqlalchemy import func, select

from app.db.models import Asset, Plan, Subscription, TaskAsset, UsageRecord, VideoTask


class _FakeStorage:
    def __init__(self) -> None:
        self.bucket = "photo-test-bucket"
        self.saved: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []

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

    def delete_object(self, key: str) -> None:
        self.deleted.append(key)
        self.saved.pop(key, None)


class _MemProgressStore:
    def __init__(self) -> None:
        self.history: list[tuple[str, dict]] = []
        self.data: dict[str, dict] = {}

    def update(self, task_id: str, **fields) -> None:
        snapshot = self.data.get(task_id, {})
        snapshot.update({k: v for k, v in fields.items() if v is not None})
        snapshot["task_id"] = task_id
        self.data[task_id] = snapshot
        self.history.append((task_id, dict(snapshot)))


class _FakeProvider:
    def __init__(
        self,
        *,
        image_bytes: bytes = b"photo-png",
        expected_input_bytes: bytes | None = b"input-image",
        cost_cents: int = 0,
        fail: bool = False,
        failure: Exception | None = None,
    ) -> None:
        self.image_bytes = image_bytes
        self.expected_input_bytes = expected_input_bytes
        self.cost_cents = cost_cents
        self.fail = fail
        self.failure = failure
        self.payloads: list[dict] = []
        self.input_path: str | None = None

    async def generate_image(self, payload: dict):
        self.payloads.append(payload)
        self.input_path = payload.get("input_image_path")
        if self.fail:
            raise self.failure or RuntimeError("image provider failed")
        if self.input_path:
            with open(self.input_path, "rb") as handle:
                content = handle.read()
            if self.expected_input_bytes is not None:
                assert content == self.expected_input_bytes
        return {
            "image_bytes": self.image_bytes,
            "mime_type": "image/png",
            "model": "gpt-image-2",
            "cost_cents": self.cost_cents,
        }


def _openai_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/images")


def _bad_request(message: str, body: dict) -> openai.BadRequestError:
    response = httpx.Response(400, request=_openai_request(), json=body)
    return openai.BadRequestError(message, response=response, body=body)


def _png_bytes(mode: str) -> bytes:
    image_mode = "RGBA" if mode == "transparent" else "RGB"
    image = Image.new(image_mode, (2, 2), (255, 255, 255, 0) if mode == "transparent" else "white")
    if mode == "transparent":
        image.putpixel((0, 0), (220, 30, 30, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _product_png_bytes() -> bytes:
    image = Image.new("RGBA", (80, 60), (0, 0, 0, 0))
    for x in range(10, 70):
        for y in range(8, 52):
            image.putpixel((x, y), (40, 120, 220, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _opaque_product_on_white_png() -> bytes:
    image = Image.new("RGB", (80, 60), "white")
    for x in range(10, 70):
        for y in range(8, 52):
            image.putpixel((x, y), (40, 120, 220))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _alpha_extrema(content: bytes) -> tuple[int, int]:
    image = Image.open(BytesIO(content))
    assert image.mode == "RGBA"
    return image.getchannel("A").getextrema()


def _decode_data_uri(uri: str) -> tuple[str, bytes]:
    assert not uri.startswith(("http://", "https://"))
    header, encoded = uri.split(",", 1)
    assert header.startswith("data:image/")
    assert header.endswith(";base64")
    return header.removeprefix("data:").removesuffix(";base64"), base64.b64decode(encoded)


def _noisy_png_bytes(size: int = 96) -> bytes:
    image = Image.new("RGB", (size, size))
    for x in range(size):
        for y in range(size):
            image.putpixel(
                (x, y),
                (
                    (x * 17 + y * 3) % 256,
                    (x * 5 + y * 11) % 256,
                    (x * 7 + y * 13) % 256,
                ),
            )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _seed_reserved_photo(db, tenant_id: str, user_id: str, task_id: str) -> str:
    now = datetime.now(UTC)
    plan = Plan(
        code=f"photo-plan-{task_id}",
        name="Photo Plan",
        price_cents=0,
        period="monthly",
        quota_credits=100,
        max_concurrent=1,
        seat_limit=3,
    )
    db.add(plan)
    db.flush()
    subscription = Subscription(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status="active",
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=30),
        quota_credits_total=100,
        quota_credits_used=10,
        quota_credits_reserved=20,
    )
    task = VideoTask(
        id=task_id,
        tenant_id=tenant_id,
        created_by_user_id=user_id,
        status="queued",
        mode="photo",
        video_mode="photo",
        progress=0,
        topic="premium product photo",
        params={"image_size": "1024x1024", "image_quality": "medium"},
    )
    db.add_all([subscription, task])
    db.flush()
    db.add(
        UsageRecord(
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            video_task_id=task_id,
            capability="image",
            provider="apimart",
            model="gpt-image-2",
            unit="image",
            quantity=Decimal("1"),
            credits=Decimal("20.00"),
            cost_cents=0,
            status="reserved",
        )
    )
    db.commit()
    return subscription.id


def _seed_unreserved_poster_photo(db, tenant_id: str, user_id: str, task_id: str) -> str:
    source_key = f"tenants/{tenant_id}/uploads/poster-product.png"
    task = VideoTask(
        id=task_id,
        tenant_id=tenant_id,
        created_by_user_id=user_id,
        status="queued",
        mode="photo",
        video_mode="photo",
        progress=0,
        topic="新品上新",
        params={
            "kind": "ecom_poster",
            "template_id": "promo_bold",
            "title": "新品上新",
            "subtitle": "今日专享",
            "source_asset_id": "poster-source",
            "source_storage_key": source_key,
        },
    )
    db.add(task)
    db.commit()
    return source_key


def _seed_terminal_photo_history(
    db,
    *,
    tenant_id: str,
    prefix: str,
    count: int = 20,
) -> list[str]:
    storage_keys: list[str] = []
    for index in range(count):
        storage_key = f"tenants/{tenant_id}/photos/{prefix}-{index:02d}.png"
        storage_keys.append(storage_key)
        db.add(
            VideoTask(
                id=f"{prefix}-{index:02d}",
                tenant_id=tenant_id,
                mode="photo",
                video_mode="photo",
                status="done",
                progress=100,
                storage_key=storage_key,
                created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            )
        )
    db.commit()
    return storage_keys


def _patch_worker(monkeypatch, auth_db, storage: _FakeStorage, store: _MemProgressStore, provider):
    from app.workers import image_gen

    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(image_gen, "build_progress_store", lambda _redis_url: store)
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "resolve", lambda _db, *, tenant_id, capability: provider)
    monkeypatch.setattr(image_gen, "label_artifact_bytes", lambda content, **_kwargs: content)
    return image_gen


def test_classify_image_error_returns_stable_codes() -> None:
    from app.providers.image.apimart import APIMartImageProviderError
    from app.workers import image_gen

    moderation_error = _bad_request(
        "request blocked",
        {"error": {"code": "moderation_blocked", "message": "request blocked"}},
    )
    safety_error = _bad_request(
        "blocked by safety system",
        {"error": {"message": "blocked by safety system"}},
    )
    invalid_request_error = _bad_request(
        "invalid image size",
        {"error": {"code": "invalid_image_size", "message": "invalid image size"}},
    )

    cases = [
        (openai.APIConnectionError(request=_openai_request()), "IMAGE_CONNECTION_ERROR"),
        (moderation_error, "IMAGE_MODERATION_BLOCKED"),
        (safety_error, "IMAGE_MODERATION_BLOCKED"),
        (invalid_request_error, "IMAGE_INVALID_REQUEST"),
        (APIMartImageProviderError("insufficient balance", status_code=402), "IMAGE_GEN_FAILED"),
        (RuntimeError("provider crashed"), "IMAGE_GEN_FAILED"),
    ]

    for exc, expected_code in cases:
        assert image_gen.classify_image_error(exc) == expected_code


def test_image_worker_text_to_image_finishes_and_settles_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-success-unit"
    with auth_db() as db:
        subscription_id = _seed_reserved_photo(
            db,
            auth_context["tenant_id"],
            auth_context["user_id"],
            task_id,
        )
    storage = _FakeStorage()
    store = _MemProgressStore()
    provider = _FakeProvider(image_bytes=b"final-png", cost_cents=144)
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "premium product photo",
            "image_size": "1024x1024",
            "image_quality": "medium",
        }
    )

    assert result["status"] == "SUCCESS"
    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    assert storage.saved[output_key] == (b"final-png", "image/png")
    assert provider.payloads == [
        {
            "prompt": "premium product photo",
            "size": "1024x1024",
            "quality": "medium",
            "n": 1,
        }
    ]
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        subscription = db.get(Subscription, subscription_id)
        usage = db.scalars(select(UsageRecord).where(UsageRecord.video_task_id == task_id)).one()
        asset = db.scalars(select(Asset).where(Asset.storage_key == output_key)).one()
        task_asset = db.scalars(select(TaskAsset).where(TaskAsset.video_task_id == task_id)).one()

    assert task.status == "done"
    assert task.progress == 100
    assert task.storage_key == output_key
    assert task.content_type == "image/png"
    assert task.size_bytes == len(b"final-png")
    assert asset.type == "generated_image"
    assert asset.source == "generated"
    assert asset.status == "ready"
    assert task_asset.asset_id == asset.id
    assert task_asset.role == "output_image"
    assert usage.status == "settled"
    assert usage.quantity == Decimal("1.000")
    assert usage.cost_cents == 144
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 30
    scoped_id = f"{auth_context['tenant_id']}:{task_id}"
    assert [snapshot["stage"] for key, snapshot in store.history if key == scoped_id] == [
        "running",
        "generating",
        "got",
        "uploading",
        "done",
    ]


def test_image_worker_marks_generated_image_as_cover_when_requested(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-cover-unit"
    with auth_db() as db:
        _seed_reserved_photo(
            db,
            auth_context["tenant_id"],
            auth_context["user_id"],
            task_id,
        )
    storage = _FakeStorage()
    store = _MemProgressStore()
    provider = _FakeProvider(image_bytes=b"cover-png")
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "AI cover prompt",
            "purpose": "cover",
        }
    )

    assert result["status"] == "SUCCESS"
    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    with auth_db() as db:
        asset = db.scalars(select(Asset).where(Asset.storage_key == output_key)).one()
        usage = db.scalars(select(UsageRecord).where(UsageRecord.video_task_id == task_id)).one()

    assert asset.metadata_["purpose"] == "cover"
    assert asset.metadata_["kind"] == "cover"
    assert usage.cost_cents == 4


def test_image_worker_edit_resolves_tenant_upload_and_cleans_temp_file(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-edit-unit"
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    storage.saved[f"tenants/{auth_context['tenant_id']}/uploads/product.png"] = (
        b"input-image",
        "image/png",
    )
    store = _MemProgressStore()
    provider = _FakeProvider()
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "replace background",
            "image_key": "uploads/product.png",
            "image_size": "1024x1536",
            "image_quality": "high",
        }
    )

    assert provider.payloads[0]["input_image_path"] == provider.input_path
    mime_type, input_bytes = _decode_data_uri(provider.payloads[0]["input_image_url"])
    assert mime_type == "image/png"
    assert input_bytes == b"input-image"
    assert provider.payloads[0]["image_urls"] == [provider.payloads[0]["input_image_url"]]
    assert provider.payloads[0]["size"] == "1024x1536"
    assert provider.payloads[0]["quality"] == "high"
    assert provider.input_path is not None
    assert not Path(provider.input_path).exists()


def test_image_worker_data_uri_mime_detection_uses_magic_suffix_and_png_default(
    monkeypatch,
) -> None:
    from app.workers import image_gen

    monkeypatch.setattr(image_gen, "_APIMART_INPUT_IMAGE_SAFE_BYTES", 1024 * 1024)

    cases = [
        ("tenants/t/uploads/product.png", b"\x89PNG\r\n\x1a\npng-data", "image/png"),
        ("tenants/t/uploads/product.jpg", b"\xff\xd8\xffjpeg-data", "image/jpeg"),
        ("tenants/t/uploads/product.webp", b"RIFF\x01\x00\x00\x00WEBPwebp-data", "image/webp"),
        ("tenants/t/uploads/product.unknown", b"plain-bytes", "image/png"),
        ("tenants/t/uploads/product.jpeg", b"plain-bytes", "image/jpeg"),
    ]

    for storage_key, content, expected_mime_type in cases:
        mime_type, decoded = _decode_data_uri(
            image_gen._input_image_data_uri(storage_key, content)
        )

        assert mime_type == expected_mime_type
        assert decoded == content


def test_image_worker_converts_all_source_storage_keys_to_data_uris(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-multi-unit"
    first_key = f"tenants/{auth_context['tenant_id']}/uploads/product-a.png"
    second_key = f"tenants/{auth_context['tenant_id']}/uploads/product-b.jpg"
    first_bytes = b"first-image"
    second_bytes = b"\xff\xd8\xffsecond-image"
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    storage.saved[first_key] = (first_bytes, "image/png")
    storage.saved[second_key] = (second_bytes, "image/jpeg")
    store = _MemProgressStore()
    provider = _FakeProvider(expected_input_bytes=first_bytes)
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "combine product references",
            "source_storage_keys": [first_key, second_key],
        }
    )

    assert result["status"] == "SUCCESS"
    payload = provider.payloads[0]
    assert payload["input_image_url"] == payload["image_urls"][0]
    assert len(payload["image_urls"]) == 2
    first_mime, first_decoded = _decode_data_uri(payload["image_urls"][0])
    second_mime, second_decoded = _decode_data_uri(payload["image_urls"][1])
    assert first_mime == "image/png"
    assert first_decoded == first_bytes
    assert second_mime == "image/jpeg"
    assert second_decoded == second_bytes


def test_image_worker_data_uri_size_guard_compresses_large_inputs(monkeypatch) -> None:
    from app.workers import image_gen

    source_bytes = _noisy_png_bytes(size=384)
    monkeypatch.setattr(image_gen, "_APIMART_INPUT_IMAGE_SAFE_BYTES", 8 * 1024)

    mime_type, compressed = _decode_data_uri(
        image_gen._input_image_data_uri("tenants/t/uploads/large.png", source_bytes)
    )

    assert len(source_bytes) > 8 * 1024
    assert mime_type == "image/jpeg"
    assert len(compressed) <= 8 * 1024
    Image.open(BytesIO(compressed)).verify()


def test_image_worker_failure_marks_failed_and_releases_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-failure-unit"
    with auth_db() as db:
        subscription_id = _seed_reserved_photo(
            db,
            auth_context["tenant_id"],
            auth_context["user_id"],
            task_id,
        )
        old_storage_keys = _seed_terminal_photo_history(
            db,
            tenant_id=auth_context["tenant_id"],
            prefix="photo-failure-history",
        )
    storage = _FakeStorage()
    store = _MemProgressStore()
    provider = _FakeProvider(fail=True)
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "premium product photo",
        }
    )

    assert result["status"] == "FAILURE"
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        subscription = db.get(Subscription, subscription_id)
        usage = db.scalars(select(UsageRecord).where(UsageRecord.video_task_id == task_id)).one()
        photo_ids = {
            row.id for row in db.scalars(select(VideoTask).where(VideoTask.mode == "photo"))
        }

    assert task.status == "failed"
    assert task.error_code == "IMAGE_GEN_FAILED"
    assert "image provider failed" in task.error_message
    assert usage.status == "released"
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 10
    assert len(photo_ids) == 20
    assert "photo-failure-history-00" not in photo_ids
    assert task_id in photo_ids
    assert storage.deleted == [old_storage_keys[0]]
    scoped_id = f"{auth_context['tenant_id']}:{task_id}"
    assert store.data[scoped_id]["status"] == "failed"
    assert store.data[scoped_id]["error_code"] == "IMAGE_GEN_FAILED"


def test_image_worker_failure_uses_classified_moderation_error_code(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-moderation-unit"
    with auth_db() as db:
        subscription_id = _seed_reserved_photo(
            db,
            auth_context["tenant_id"],
            auth_context["user_id"],
            task_id,
        )
    storage = _FakeStorage()
    store = _MemProgressStore()
    provider = _FakeProvider(
        fail=True,
        failure=_bad_request(
            "request rejected by safety system",
            {
                "error": {
                    "code": "moderation_blocked",
                    "message": "request rejected by safety system",
                }
            },
        ),
    )
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "premium product photo",
        }
    )

    assert result["status"] == "FAILURE"
    assert result["error_code"] == "IMAGE_MODERATION_BLOCKED"
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        subscription = db.get(Subscription, subscription_id)
        usage = db.scalars(select(UsageRecord).where(UsageRecord.video_task_id == task_id)).one()

    assert task.status == "failed"
    assert task.error_code == "IMAGE_MODERATION_BLOCKED"
    assert "safety system" in task.error_message
    assert usage.status == "released"
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 10
    scoped_id = f"{auth_context['tenant_id']}:{task_id}"
    assert store.data[scoped_id]["status"] == "failed"
    assert store.data[scoped_id]["error_code"] == "IMAGE_MODERATION_BLOCKED"


def test_image_worker_ecom_transparent_cutout_uses_source_asset_and_keeps_alpha(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "ecom-alpha-unit"
    source_key = f"tenants/{auth_context['tenant_id']}/uploads/product.png"
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    storage.saved[source_key] = (b"input-image", "image/png")
    store = _MemProgressStore()
    provider = _FakeProvider(image_bytes=_png_bytes("transparent"))
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "cut out the product",
            "kind": "ecom_cutout",
            "background": "transparent",
            "source_asset_id": "product-alpha-source",
            "source_storage_key": source_key,
        }
    )

    assert result["status"] == "SUCCESS"
    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    payload = provider.payloads[0]
    assert payload["input_image_path"] == provider.input_path
    mime_type, input_bytes = _decode_data_uri(payload["input_image_url"])
    assert mime_type == "image/png"
    assert input_bytes == b"input-image"
    assert payload["image_urls"] == [payload["input_image_url"]]
    assert "transparent" in payload["prompt"].lower()
    assert "alpha" in payload["prompt"].lower()
    assert "background" not in payload
    assert "response_format" not in payload
    assert "input_fidelity" not in payload
    saved_bytes = storage.saved[output_key][0]
    assert _alpha_extrema(saved_bytes)[0] < 255
    with auth_db() as db:
        asset = db.scalars(select(Asset).where(Asset.storage_key == output_key)).one()
    assert asset.metadata_["kind"] == "ecom_cutout"
    assert asset.metadata_["background"] == "transparent"
    assert asset.metadata_["source_asset_id"] == "product-alpha-source"


def test_image_worker_ecom_model_uses_source_asset_and_stores_model_metadata(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "ecom-model-unit"
    source_key = f"tenants/{auth_context['tenant_id']}/uploads/model-product.png"
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    storage.saved[source_key] = (b"input-image", "image/png")
    store = _MemProgressStore()
    provider = _FakeProvider(image_bytes=_png_bytes("opaque"))
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": (
                "Compose the product onto a female AI fashion model in a street fashion "
                "scene. Preserve the exact product shape, logo, colors, and proportions."
            ),
            "kind": "ecom_model",
            "gender": "female",
            "style_id": "street",
            "extra_prompt": "avoid hats",
            "source_asset_id": "product-model-source",
            "source_storage_key": source_key,
        }
    )

    assert result["status"] == "SUCCESS"
    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    payload = provider.payloads[0]
    assert payload["input_image_path"] == provider.input_path
    mime_type, input_bytes = _decode_data_uri(payload["input_image_url"])
    assert mime_type == "image/png"
    assert input_bytes == b"input-image"
    assert payload["image_urls"] == [payload["input_image_url"]]
    assert "female ai fashion model" in payload["prompt"].lower()
    assert "preserve the exact product shape" in payload["prompt"].lower()
    assert "response_format" not in payload
    assert "input_fidelity" not in payload
    with auth_db() as db:
        asset = db.scalars(select(Asset).where(Asset.storage_key == output_key)).one()
    assert asset.metadata_["kind"] == "ecom_model"
    assert asset.metadata_["gender"] == "female"
    assert asset.metadata_["style_id"] == "street"
    assert asset.metadata_["extra_prompt"] == "avoid hats"
    assert asset.metadata_["source_asset_id"] == "product-model-source"


def test_image_worker_ecom_poster_composes_locally_without_provider_or_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "ecom-poster-unit"
    with auth_db() as db:
        source_key = _seed_unreserved_poster_photo(
            db,
            auth_context["tenant_id"],
            auth_context["user_id"],
            task_id,
        )
        subscription_id = db.scalars(select(Subscription.id)).one()
        used_before = db.get(Subscription, subscription_id).quota_credits_used
        reserved_before = db.get(Subscription, subscription_id).quota_credits_reserved
    storage = _FakeStorage()
    storage.saved[source_key] = (_product_png_bytes(), "image/png")
    store = _MemProgressStore()
    provider = _FakeProvider(fail=True, failure=AssertionError("provider should not be called"))
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "kind": "ecom_poster",
            "template_id": "promo_bold",
            "title": "新品上新",
            "subtitle": "今日专享",
            "source_asset_id": "poster-source",
            "source_storage_key": source_key,
        }
    )

    assert result["status"] == "SUCCESS"
    assert provider.payloads == []
    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    saved_bytes, content_type = storage.saved[output_key]
    assert content_type == "image/png"
    image = Image.open(BytesIO(saved_bytes))
    assert image.size == (1080, 1350)
    assert image.getbbox() is not None

    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        asset = db.scalars(select(Asset).where(Asset.storage_key == output_key)).one()
        usage_count = db.scalar(
            select(func.count())
            .select_from(UsageRecord)
            .where(UsageRecord.video_task_id == task_id)
        )
        subscription = db.get(Subscription, subscription_id)

    assert task.status == "done"
    assert task.progress == 100
    assert task.storage_key == output_key
    assert asset.metadata_["kind"] == "ecom_poster"
    assert asset.metadata_["template_id"] == "promo_bold"
    assert asset.metadata_["title"] == "新品上新"
    assert asset.metadata_["subtitle"] == "今日专享"
    assert asset.metadata_["source_asset_id"] == "poster-source"
    assert usage_count == 0
    assert subscription.quota_credits_used == used_before
    assert subscription.quota_credits_reserved == reserved_before


def test_image_worker_ecom_transparent_cutout_local_alpha_fallback(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "ecom-opaque-unit"
    source_key = f"tenants/{auth_context['tenant_id']}/uploads/product.png"
    with auth_db() as db:
        subscription_id = _seed_reserved_photo(
            db,
            auth_context["tenant_id"],
            auth_context["user_id"],
            task_id,
        )
    storage = _FakeStorage()
    storage.saved[source_key] = (b"input-image", "image/png")
    store = _MemProgressStore()
    provider = _FakeProvider(image_bytes=_opaque_product_on_white_png())
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "cut out the product",
            "kind": "ecom_cutout",
            "background": "transparent",
            "source_storage_key": source_key,
        }
    )

    assert result["status"] == "SUCCESS"
    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    saved_bytes = storage.saved[output_key][0]
    alpha_min, alpha_max = _alpha_extrema(saved_bytes)
    assert alpha_min == 0
    assert alpha_max == 255
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        subscription = db.get(Subscription, subscription_id)
        usage = db.scalars(select(UsageRecord).where(UsageRecord.video_task_id == task_id)).one()
    assert task.status == "done"
    assert task.error_code is None
    assert usage.status == "settled"
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 30
