import asyncio
import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import httpx
import openai
import pytest
from PIL import Image
from sqlalchemy import func, select

from app.db.models import (
    Asset,
    Plan,
    ProviderConfig,
    Subscription,
    TaskAsset,
    UsageRecord,
    VideoTask,
)
from app.providers.base import ImageProviderCapabilities, ResolvedProvider


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
    capabilities = ImageProviderCapabilities(
        supported_resolutions=frozenset({"1k", "2k", "4k"}),
        max_reference_images=6,
    )

    def __init__(
        self,
        *,
        image_bytes: bytes = b"photo-png",
        expected_input_bytes: bytes | None = b"input-image",
        cost_cents: int = 0,
        provider_name: str = "apimart",
        result_size: str | None = None,
        fail: bool = False,
        failure: Exception | None = None,
    ) -> None:
        self.image_bytes = image_bytes
        self.expected_input_bytes = expected_input_bytes
        self.cost_cents = cost_cents
        self.provider_name = provider_name
        self.result_size = result_size
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
            "provider": self.provider_name,
            "model": "gpt-image-2",
            "size": self.result_size or payload.get("size"),
            "resolution": payload.get("resolution"),
            "quality": payload.get("quality"),
            "cost_cents": self.cost_cents,
        }


class _SlowFakeProvider(_FakeProvider):
    async def generate_image(self, payload: dict):
        await asyncio.sleep(0.08)
        return await super().generate_image(payload)


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


def _sized_png_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _exif_rotated_jpeg_bytes() -> bytes:
    image = Image.new("RGB", (40, 80), "white")
    exif = Image.Exif()
    exif[274] = 6
    buffer = BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
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

    fake_provider = provider

    def fake_resolve_named(_db, *, tenant_id, capability, provider):
        assert provider == fake_provider.provider_name
        return fake_provider

    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(image_gen, "build_progress_store", lambda _redis_url: store)
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(
        image_gen,
        "resolve_with_name",
        lambda _db, *, tenant_id, capability: ResolvedProvider(
            name=fake_provider.provider_name,
            provider=fake_provider,
        ),
    )
    monkeypatch.setattr(
        image_gen,
        "resolve_named_provider",
        fake_resolve_named,
    )
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
            "size": "1:1",
            "resolution": "1k",
            "quality": "high",
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


def test_image_worker_heartbeats_during_provider_call_without_fake_progress(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-heartbeat-unit"
    with auth_db() as db:
        _seed_reserved_photo(
            db,
            auth_context["tenant_id"],
            auth_context["user_id"],
            task_id,
        )
    storage = _FakeStorage()
    store = _MemProgressStore()
    provider = _SlowFakeProvider(image_bytes=b"heartbeat-png", cost_cents=4)
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)
    monkeypatch.setattr(
        image_gen.settings,
        "engine_gen_heartbeat_interval_seconds",
        0.02,
        raising=False,
    )

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "slow premium product photo",
        }
    )

    assert result["status"] == "SUCCESS"
    scoped_id = f"{auth_context['tenant_id']}:{task_id}"
    heartbeat_snapshots = [
        snapshot
        for key, snapshot in store.history
        if key == scoped_id
        and snapshot.get("heartbeat_at")
        and snapshot.get("stage") == "generating"
    ]
    assert len(heartbeat_snapshots) >= 2
    assert all(snapshot["progress"] == 30 for snapshot in heartbeat_snapshots)
    assert all(snapshot["stage"] == "generating" for snapshot in heartbeat_snapshots)


@pytest.mark.parametrize(
    ("image_resolution", "prompt_resolution"),
    [("1k", "4K"), ("2k", "1K"), ("4k", "1K")],
)
def test_image_worker_uses_resolution_field_without_rewriting_conflicting_prompt(
    image_resolution,
    prompt_resolution,
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = f"photo-resolution-{image_resolution}-unit"
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
        task = db.get(VideoTask, task_id)
        task.params = {**task.params, "image_resolution": image_resolution}
        db.commit()
    storage = _FakeStorage()
    store = _MemProgressStore()
    provider = _FakeProvider(image_bytes=_sized_png_bytes(160, 90))
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)
    prompt = f"Generate a {prompt_resolution} ultra-clear premium product image."

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": prompt,
            "image_resolution": image_resolution,
        }
    )

    assert result["status"] == "SUCCESS"
    assert provider.payloads[0]["prompt"] == prompt
    assert provider.payloads[0]["resolution"] == image_resolution
    assert provider.payloads[0]["quality"] == "high"
    assert provider.payloads[0]["n"] == 1
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
    assert task.params["image_resolution"] == image_resolution
    assert task.params["actual_width"] == 160
    assert task.params["actual_height"] == 90


def test_image_worker_composes_photo_prompt_layers_and_enabled_strength(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-layered-prompt-unit"
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    store = _MemProgressStore()
    provider = _FakeProvider()
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "Reimagine the product on a sculptural pedestal.",
            "master_prompt": "Warm editorial campaign styling.",
            "master_negative_prompt": "watermarks and illegible text",
            "negative_prompt": "duplicate handles and warped edges",
            "similarity_strength": 20,
        }
    )

    assert result["status"] == "SUCCESS"
    assert set(provider.payloads[0]) == {"prompt", "size", "resolution", "quality", "n"}
    assert provider.payloads[0]["prompt"] == (
        "Warm editorial campaign styling.\n\n"
        "Reimagine the product on a sculptural pedestal.\n\n"
        "Reference strength controls:\n"
        "- Reference similarity (20%): Apply this as a light preference; visible "
        "departures are welcome. Keep the result visually similar to all reference "
        "images in overall appearance, composition, palette, proportions, and "
        "distinctive details.\n\n"
        "Avoid the following where possible; this is soft guidance, not a hard "
        "constraint:\n"
        "- Task-wide: watermarks and illegible text\n"
        "- Image-specific: duplicate handles and warped edges"
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "similarity_strength",
        "creativity_strength",
        "subject_strength",
    ],
)
@pytest.mark.parametrize("strength", [20, 80])
def test_image_worker_wires_each_strength_to_final_provider_prompt(
    field_name,
    strength,
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = f"photo-{field_name}-{strength}-unit"
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    store = _MemProgressStore()
    provider = _FakeProvider()
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)
    prompt = "Base image prompt."

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": prompt,
            field_name: strength,
        }
    )

    assert result["status"] == "SUCCESS"
    assert provider.payloads[0]["prompt"] == image_gen.build_photo_prompt(
        prompt,
        **{field_name: strength},
    )


@pytest.mark.parametrize(
    ("field_name", "label", "instruction"),
    [
        (
            "similarity_strength",
            "Reference similarity",
            "Keep the result visually similar to all reference images in overall "
            "appearance, composition, palette, proportions, and distinctive details.",
        ),
        (
            "creativity_strength",
            "Creative freedom",
            "Introduce new composition, styling, lighting, color, and decorative ideas "
            "in areas not protected by other enabled controls.",
        ),
        (
            "subject_strength",
            "Subject preservation",
            "Preserve each referenced subject's identity, count, shape, proportions, "
            "colors, logos, text, materials, and defining details.",
        ),
    ],
)
def test_photo_strengths_compile_distinct_twenty_and_eighty_percent_guidance(
    field_name: str,
    label: str,
    instruction: str,
) -> None:
    from app.workers.image_gen import build_photo_prompt

    low_prompt = build_photo_prompt("Base image prompt.", **{field_name: 20})
    high_prompt = build_photo_prompt("Base image prompt.", **{field_name: 80})

    assert low_prompt == (
        "Base image prompt.\n\nReference strength controls:\n"
        f"- {label} (20%): Apply this as a light preference; visible departures are "
        f"welcome. {instruction}"
    )
    assert high_prompt == (
        "Base image prompt.\n\nReference strength controls:\n"
        f"- {label} (80%): Apply this as a strict priority; permit only small departures. "
        f"{instruction}"
    )
    assert low_prompt != high_prompt
    normalized_levels = {
        build_photo_prompt("Base image prompt.", **{field_name: value}).replace(
            f"({value}%)",
            "(strength%)",
        )
        for value in range(10, 101, 10)
    }
    assert len(normalized_levels) == 10


@pytest.mark.parametrize(
    ("similarity", "creativity", "expected_resolution"),
    [
        (
            80,
            20,
            "Conflict resolution: Reference similarity takes priority over creative "
            "freedom; apply creative changes only where they do not weaken reference "
            "fidelity.",
        ),
        (
            20,
            80,
            "Conflict resolution: Creative freedom takes priority for composition, "
            "styling, lighting, and environment; keep referenced subjects recognizable.",
        ),
        (
            80,
            80,
            "Conflict resolution: At equal strengths, preserve reference-defining subject "
            "identity while applying creativity only to composition, styling, lighting, "
            "and non-identity details.",
        ),
    ],
)
def test_photo_prompt_resolves_similarity_creativity_conflicts(
    similarity: int,
    creativity: int,
    expected_resolution: str,
) -> None:
    from app.workers.image_gen import build_photo_prompt

    prompt = build_photo_prompt(
        "Base image prompt.",
        similarity_strength=similarity,
        creativity_strength=creativity,
    )

    assert expected_resolution in prompt
    assert prompt.index("Reference similarity") < prompt.index("Creative freedom")
    assert prompt.index("Creative freedom") < prompt.index("Conflict resolution")


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
    assert usage.cost_cents == 6


def test_image_worker_cover_keeps_scalar_reference_edit_compatibility(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-cover-edit-compat-unit"
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    storage.saved[f"tenants/{auth_context['tenant_id']}/uploads/product.png"] = (
        b"cover-reference",
        "image/png",
    )
    store = _MemProgressStore()
    provider = _FakeProvider(expected_input_bytes=b"cover-reference")
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "AI cover prompt",
            "purpose": "cover",
            "image_key": "uploads/product.png",
        }
    )

    assert result["status"] == "SUCCESS"
    payload = provider.payloads[0]
    assert payload["input_image_url"] == payload["image_urls"][0]
    assert _decode_data_uri(payload["image_urls"][0])[1] == b"cover-reference"
    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    with auth_db() as db:
        asset = db.scalars(select(Asset).where(Asset.storage_key == output_key)).one()
    assert asset.metadata_["purpose"] == "cover"


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
    assert provider.payloads[0]["size"] == "2:3"
    assert provider.payloads[0]["resolution"] == "1k"
    assert provider.payloads[0]["quality"] == "high"
    assert provider.input_path is not None
    assert not Path(provider.input_path).exists()


def test_image_worker_auto_uses_exif_dimensions_and_records_three_sizes(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-auto-exif-unit"
    source_bytes = _exif_rotated_jpeg_bytes()
    output_bytes = _sized_png_bytes(160, 90)
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    storage.saved[f"tenants/{auth_context['tenant_id']}/uploads/product.jpg"] = (
        source_bytes,
        "image/jpeg",
    )
    store = _MemProgressStore()
    provider = _FakeProvider(
        image_bytes=output_bytes,
        expected_input_bytes=source_bytes,
        result_size="16:9",
    )
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "preserve product",
            "image_key": "uploads/product.jpg",
            "aspect_ratio": "auto",
        }
    )

    assert result["status"] == "SUCCESS"
    payload = provider.payloads[0]
    assert payload["size"] == "16:9"
    assert payload["resolution"] == "1k"
    assert payload["quality"] == "high"
    assert "auto" not in payload.values()

    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        asset = db.scalars(select(Asset).where(Asset.storage_key == output_key)).one()

    assert task.params["requested_aspect_ratio"] == "auto"
    assert task.params["resolved_aspect_ratio"] == "16:9"
    assert task.params["actual_aspect_ratio"] == "16:9"
    assert asset.width == 160
    assert asset.height == 90
    assert asset.metadata_["requested_aspect_ratio"] == "auto"
    assert asset.metadata_["resolved_aspect_ratio"] == "16:9"
    assert asset.metadata_["resolved_size"] == "16:9"
    assert asset.metadata_["actual_aspect_ratio"] == "16:9"
    assert asset.metadata_["actual_size"] == "160x90"


def test_image_worker_records_openai_fallback_without_mislabeling_requested_ratio(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-openai-fallback-size-unit"
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    store = _MemProgressStore()
    provider = _FakeProvider(
        image_bytes=_sized_png_bytes(160, 90),
        expected_input_bytes=None,
        provider_name="openai",
        result_size="1536x1024",
    )
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "wide product campaign",
            "aspect_ratio": "21:9",
        }
    )

    assert result["status"] == "SUCCESS"
    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    with auth_db() as db:
        asset = db.scalars(select(Asset).where(Asset.storage_key == output_key)).one()
        usage = db.scalars(
            select(UsageRecord).where(UsageRecord.video_task_id == task_id)
        ).one()

    assert asset.provider == "openai"
    assert usage.provider == "openai"
    assert usage.model == "gpt-image-2"
    assert asset.metadata_["requested_aspect_ratio"] == "21:9"
    assert asset.metadata_["resolved_aspect_ratio"] == "3:2"
    assert asset.metadata_["resolved_size"] == "1536x1024"
    assert asset.metadata_["actual_aspect_ratio"] == "16:9"


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


def test_image_worker_uses_bound_apimart_when_default_changes_to_openai(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.providers import base as provider_base
    from app.workers import image_gen

    class _CapabilityTrackingProvider(_FakeProvider):
        def __init__(self, *, capabilities: ImageProviderCapabilities, **kwargs) -> None:
            super().__init__(**kwargs)
            self._capabilities = capabilities
            self.capability_checks = 0

        @property
        def capabilities(self) -> ImageProviderCapabilities:
            self.capability_checks += 1
            return self._capabilities

    task_id = "photo-bound-apimart-unit"
    image_keys = ["uploads/product-front.png", "uploads/product-side.png"]
    image_bytes = [b"product-front", b"product-side"]
    worker_params = {
        "tenant_id": auth_context["tenant_id"],
        "video_task_id": task_id,
        "topic": "combine both product references at full resolution",
        "image_keys": image_keys,
        "image_resolution": "4k",
        "image_provider": "apimart",
    }
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
        task = db.get(VideoTask, task_id)
        task.params = {**(task.params or {}), **worker_params}
        db.add_all(
            [
                ProviderConfig(
                    tenant_id=auth_context["tenant_id"],
                    capability="image",
                    provider="openai",
                    config={"api_key": "test-openai-key"},
                    is_active=True,
                ),
                ProviderConfig(
                    tenant_id=None,
                    capability="image",
                    provider="apimart",
                    config={"api_key": "test-apimart-key"},
                    is_active=True,
                ),
            ]
        )
        db.commit()

    storage = _FakeStorage()
    for image_key, content in zip(image_keys, image_bytes, strict=True):
        storage.saved[f"tenants/{auth_context['tenant_id']}/{image_key}"] = (
            content,
            "image/png",
        )
    store = _MemProgressStore()
    default_openai = _CapabilityTrackingProvider(
        capabilities=ImageProviderCapabilities(
            supported_resolutions=frozenset({"1k"}),
            max_reference_images=1,
        ),
        image_bytes=b"silently-downgraded-openai-1k",
        expected_input_bytes=image_bytes[0],
        provider_name="openai",
        result_size="1024x1024",
    )
    bound_apimart = _CapabilityTrackingProvider(
        capabilities=ImageProviderCapabilities(
            supported_resolutions=frozenset({"1k", "2k", "4k"}),
            max_reference_images=6,
        ),
        image_bytes=b"bound-apimart-4k",
        expected_input_bytes=image_bytes[0],
        provider_name="apimart",
        result_size="1:1",
    )
    monkeypatch.setitem(
        provider_base._REGISTRY,
        ("image", "openai"),
        lambda _config: default_openai,
    )
    monkeypatch.setitem(
        provider_base._REGISTRY,
        ("image", "apimart"),
        lambda _config: bound_apimart,
    )
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(image_gen, "build_progress_store", lambda _redis_url: store)
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "label_artifact_bytes", lambda content, **_kwargs: content)

    result = image_gen.run_image_generation(worker_params)

    assert result["status"] == "SUCCESS"
    output_key = f"tenants/{auth_context['tenant_id']}/photos/{task_id}/output.png"
    with auth_db() as db:
        asset = db.scalars(select(Asset).where(Asset.storage_key == output_key)).one()
    assert asset.provider == "apimart"
    assert storage.saved[output_key] == (b"bound-apimart-4k", "image/png")
    assert default_openai.payloads == []
    assert bound_apimart.capability_checks >= 1
    assert bound_apimart.payloads[0]["resolution"] == "4k"
    assert len(bound_apimart.payloads[0]["image_urls"]) == 2


def test_image_worker_uses_durable_request_fields_when_queue_payload_drifts(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.providers import base as provider_base
    from app.workers import image_gen

    task_id = "photo-durable-provider-request-unit"
    durable_image_key = "uploads/durable-product.png"
    queue_only_image_key = "uploads/queue-only-product.png"
    durable_params = {
        "image_provider": "openai",
        "image_resolution": "1k",
        "image_keys": [durable_image_key],
    }
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
        task = db.get(VideoTask, task_id)
        task.params = {**(task.params or {}), **durable_params}
        db.add(
            ProviderConfig(
                tenant_id=auth_context["tenant_id"],
                capability="image",
                provider="openai",
                config={"api_key": "test-openai-key"},
                is_active=True,
            )
        )
        db.commit()

    storage = _FakeStorage()
    storage.saved[f"tenants/{auth_context['tenant_id']}/{durable_image_key}"] = (
        b"durable-product",
        "image/png",
    )
    storage.saved[f"tenants/{auth_context['tenant_id']}/{queue_only_image_key}"] = (
        b"queue-only-product",
        "image/png",
    )
    store = _MemProgressStore()
    provider = _FakeProvider(
        image_bytes=b"openai-result",
        expected_input_bytes=b"durable-product",
        provider_name="openai",
    )
    provider.capabilities = ImageProviderCapabilities(
        supported_resolutions=frozenset({"1k"}),
        max_reference_images=1,
    )
    monkeypatch.setitem(
        provider_base._REGISTRY,
        ("image", "openai"),
        lambda _config: provider,
    )
    monkeypatch.setattr(image_gen, "SessionLocal", auth_db)
    monkeypatch.setattr(image_gen, "build_progress_store", lambda _redis_url: store)
    monkeypatch.setattr(image_gen, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(image_gen, "label_artifact_bytes", lambda content, **_kwargs: content)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "preserve the durable product reference",
            "image_provider": "openai",
            "image_resolution": "4k",
            "image_keys": [durable_image_key, queue_only_image_key],
        }
    )

    assert result["status"] == "SUCCESS"
    assert provider.payloads[0]["resolution"] == "1k"
    assert len(provider.payloads[0]["image_urls"]) == 1
    assert _decode_data_uri(provider.payloads[0]["image_urls"][0])[1] == b"durable-product"


def test_image_worker_ignores_queue_only_poster_kind_for_bound_photo(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-queue-poster-injection-unit"
    source_key = f"tenants/{auth_context['tenant_id']}/uploads/product.png"
    source_bytes = _product_png_bytes()
    with auth_db() as db:
        subscription_id = _seed_reserved_photo(
            db,
            auth_context["tenant_id"],
            auth_context["user_id"],
            task_id,
        )
        task = db.get(VideoTask, task_id)
        task.params = {
            **(task.params or {}),
            "image_provider": "apimart",
            "image_resolution": "1k",
            "source_storage_key": source_key,
        }
        db.commit()

    storage = _FakeStorage()
    storage.saved[source_key] = (source_bytes, "image/png")
    store = _MemProgressStore()
    provider = _FakeProvider(
        image_bytes=_sized_png_bytes(32, 32),
        expected_input_bytes=source_bytes,
    )
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "premium product photo",
            "kind": "ecom_poster",
            "template_id": "promo_bold",
            "title": "queue-only title",
            "subtitle": "queue-only subtitle",
            "source_storage_key": source_key,
        }
    )

    assert result["status"] == "SUCCESS"
    assert len(provider.payloads) == 1
    with auth_db() as db:
        usage = db.scalars(
            select(UsageRecord).where(UsageRecord.video_task_id == task_id)
        ).one()
        subscription = db.get(Subscription, subscription_id)
    assert usage.status == "settled"
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 30


def test_image_worker_empty_source_list_cannot_hide_scalar_reference_from_capability_guard(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-empty-source-list-capability-unit"
    image_key = "uploads/legacy-product.png"
    with auth_db() as db:
        subscription_id = _seed_reserved_photo(
            db,
            auth_context["tenant_id"],
            auth_context["user_id"],
            task_id,
        )
        task = db.get(VideoTask, task_id)
        task.params = {
            **(task.params or {}),
            "image_provider": "future-no-reference",
            "image_resolution": "1k",
            "source_storage_keys": [],
            "image_key": image_key,
        }
        db.commit()

    storage = _FakeStorage()
    storage.saved[f"tenants/{auth_context['tenant_id']}/{image_key}"] = (
        b"legacy-product",
        "image/png",
    )
    store = _MemProgressStore()
    provider = _FakeProvider(
        expected_input_bytes=b"legacy-product",
        provider_name="future-no-reference",
    )
    provider.capabilities = ImageProviderCapabilities(
        supported_resolutions=frozenset({"1k"}),
        max_reference_images=0,
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
    assert result["error_code"] == "IMAGE_PROVIDER_REFERENCE_IMAGES_UNSUPPORTED"
    assert provider.payloads == []
    with auth_db() as db:
        usage = db.scalars(
            select(UsageRecord).where(UsageRecord.video_task_id == task_id)
        ).one()
        subscription = db.get(Subscription, subscription_id)
    assert usage.status == "released"
    assert subscription.quota_credits_reserved == 0


@pytest.mark.parametrize("reference_count", [1, 6])
def test_image_worker_prefers_photo_image_keys_over_scalar_fallback(
    monkeypatch,
    auth_context,
    auth_db,
    reference_count: int,
) -> None:
    task_id = f"photo-{reference_count}-references-unit"
    image_keys = [f"uploads/reference-{index}.png" for index in range(reference_count)]
    image_bytes = [f"reference-{index}".encode() for index in range(reference_count)]
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    legacy_scalar_key = "uploads/legacy-scalar.png"
    storage.saved[f"tenants/{auth_context['tenant_id']}/{legacy_scalar_key}"] = (
        b"legacy-scalar",
        "image/png",
    )
    for image_key, content in zip(image_keys, image_bytes, strict=True):
        storage.saved[f"tenants/{auth_context['tenant_id']}/{image_key}"] = (
            content,
            "image/png",
        )
    store = _MemProgressStore()
    provider = _FakeProvider(expected_input_bytes=image_bytes[0])
    image_gen = _patch_worker(monkeypatch, auth_db, storage, store, provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "combine all six product references",
            "source_storage_keys": [],
            "image_keys": image_keys,
            "image_key": legacy_scalar_key,
        }
    )

    assert result["status"] == "SUCCESS"
    payload = provider.payloads[0]
    assert len(payload["image_urls"]) == reference_count
    assert payload["input_image_url"] == payload["image_urls"][0]
    decoded = [_decode_data_uri(url)[1] for url in payload["image_urls"]]
    assert decoded == image_bytes
    assert b"legacy-scalar" not in decoded


def test_image_worker_auto_aspect_uses_product_before_model_reference(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    task_id = "photo-model-product-primary"
    product_key = f"tenants/{auth_context['tenant_id']}/uploads/product-primary.png"
    model_key = f"tenants/{auth_context['tenant_id']}/uploads/model-secondary.png"
    product_bytes = _sized_png_bytes(80, 120)
    model_bytes = _sized_png_bytes(160, 90)
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    storage.saved[product_key] = (product_bytes, "image/png")
    storage.saved[model_key] = (model_bytes, "image/png")
    provider = _FakeProvider(expected_input_bytes=product_bytes)
    image_gen = _patch_worker(monkeypatch, auth_db, storage, _MemProgressStore(), provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "preserve product dimensions",
            "aspect_ratio": "auto",
            "source_storage_keys": [product_key, model_key],
        }
    )

    assert result["status"] == "SUCCESS"
    assert provider.payloads[0]["size"] == "2:3"
    assert provider.payloads[0]["input_image_url"] == provider.payloads[0]["image_urls"][0]


def test_image_worker_caps_aggregate_multi_image_data_uri_payload(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.workers import image_gen

    task_id = "photo-multi-aggregate-budget"
    total_budget = 48 * 1024
    source_bytes = _noisy_png_bytes(size=512)
    source_keys = [
        f"tenants/{auth_context['tenant_id']}/uploads/aggregate-{index}.png"
        for index in range(6)
    ]
    assert len(source_bytes) * len(source_keys) > total_budget
    monkeypatch.setattr(image_gen, "_APIMART_INPUT_IMAGE_SAFE_BYTES", 32 * 1024)
    monkeypatch.setattr(
        image_gen,
        "_APIMART_INPUT_IMAGES_TOTAL_SAFE_BYTES",
        total_budget,
        raising=False,
    )
    with auth_db() as db:
        _seed_reserved_photo(db, auth_context["tenant_id"], auth_context["user_id"], task_id)
    storage = _FakeStorage()
    for source_key in source_keys:
        storage.saved[source_key] = (source_bytes, "image/png")
    provider = _FakeProvider(expected_input_bytes=source_bytes)
    image_gen = _patch_worker(monkeypatch, auth_db, storage, _MemProgressStore(), provider)

    result = image_gen.run_image_generation(
        {
            "tenant_id": auth_context["tenant_id"],
            "video_task_id": task_id,
            "topic": "combine six product and model references",
            "source_storage_keys": source_keys,
        }
    )

    assert result["status"] == "SUCCESS"
    image_urls = provider.payloads[0]["image_urls"]
    decoded_images = [_decode_data_uri(image_url)[1] for image_url in image_urls]
    assert len(decoded_images) == 6
    assert sum(map(len, decoded_images)) <= total_budget
    max_data_uri_chars = ((total_budget + 2) // 3) * 4 + len(image_urls) * 32
    assert sum(map(len, image_urls)) <= max_data_uri_chars


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


def test_image_worker_data_uri_size_guard_applies_exif_before_compressing(monkeypatch) -> None:
    from app.workers import image_gen

    source_bytes = _exif_rotated_jpeg_bytes()
    monkeypatch.setattr(image_gen, "_APIMART_INPUT_IMAGE_SAFE_BYTES", 500)

    mime_type, compressed = _decode_data_uri(
        image_gen._input_image_data_uri("tenants/t/uploads/rotated.jpg", source_bytes)
    )

    assert mime_type == "image/jpeg"
    with Image.open(BytesIO(compressed)) as image:
        assert image.size == (80, 40)


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
