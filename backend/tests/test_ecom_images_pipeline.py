from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_object_storage
from app.db.models import Asset, BatchJob, Subscription, Tenant, UsageRecord, VideoTask
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
    assert payload["video_task_id"] == data["task_id"]

    with auth_db() as db:
        task = db.get(VideoTask, data["task_id"])
        usage = db.scalars(
            select(UsageRecord).where(UsageRecord.video_task_id == data["task_id"])
        ).one()
        subscription = db.get(Subscription, subscription_id)

    assert task.mode == "photo"
    assert task.video_mode == "photo"
    assert task.params["kind"] == "ecom_cutout"
    assert task.params["background"] == "transparent"
    assert task.params["source_asset_id"] == source["id"]
    assert task.params["source_storage_key"] == source["storage_key"]
    assert usage.status == "reserved"
    assert usage.credits == 20
    assert subscription.quota_credits_reserved == 20


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
            for index in range(25)
        ]

    enqueued = _stub_image_task(monkeypatch)
    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        resp = TestClient(app).post(
            "/api/v1/ecom-images/cutout/batch",
            json={
                "items": [
                    {"source_asset_id": source["id"], "background": "white"}
                    for source in sources
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

    with auth_db() as db:
        task_count = db.scalar(
            select(func.count()).select_from(VideoTask).where(VideoTask.mode == "photo")
        )
        batch_rows = db.scalar(select(func.count()).select_from(BatchJob))
        reserved_count = db.scalar(
            select(func.count())
            .select_from(UsageRecord)
            .where(UsageRecord.status == "reserved")
        )
        subscription = db.scalars(select(Subscription)).one()

    assert task_count == 20
    assert batch_rows == 0
    assert reserved_count == 20
    assert subscription.quota_credits_reserved == 400


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
