from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.api.deps import get_object_storage
from app.core.exceptions import AppError
from app.db.models import (
    Asset,
    EcomReplicateJob,
    EcomReplicateOutput,
    ReversePromptJob,
    TaskAsset,
    Tenant,
    VideoTask,
)
from app.main import app
from app.services.image_history import _validated_tenant_storage_key
from app.services.storage.local import LocalObjectStorage


class _FakeStorage:
    bucket = "history-test"

    def __init__(self) -> None:
        self.presigned_keys: list[str] = []
        self.deleted_keys: list[str] = []
        self.fail_deletes = False

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        self.presigned_keys.append(key)
        suffix = "&download=1" if download_filename else ""
        return f"https://storage.test/{key}?ttl={expires_in}{suffix}"

    def delete_object(self, key: str) -> None:
        self.deleted_keys.append(key)
        if self.fail_deletes:
            raise RuntimeError("simulated storage delete failure")


def _photo_task(
    *,
    task_id: str,
    tenant_id: str,
    created_at: datetime,
    topic: str,
    params: dict[str, object] | None = None,
) -> VideoTask:
    storage_key = f"tenants/{tenant_id}/videos/{task_id}/output.png"
    return VideoTask(
        id=task_id,
        tenant_id=tenant_id,
        status="done",
        topic=topic,
        mode="photo",
        video_mode="photo",
        progress=100,
        params=params or {},
        storage_bucket="history-test",
        storage_key=storage_key,
        thumbnail_key=storage_key,
        content_type="image/png",
        created_at=created_at,
        finished_at=created_at,
    )


def _replicate_job(
    *,
    job_id: str,
    tenant_id: str,
    status: str,
    created_at: datetime,
    name: str,
    product_asset_id: str,
    output_count: int,
) -> EcomReplicateJob:
    return EcomReplicateJob(
        id=job_id,
        tenant_id=tenant_id,
        status=status,
        output_mode="detail",
        requested_size="768x1024",
        requested_aspect="3:4",
        reference_image_asset_ids=[],
        product_image_asset_ids=[product_asset_id],
        product_info={"name": name},
        selling_points=[],
        reference_analysis_json=[],
        template_mapping_json={},
        generation_plan_json={},
        output_count=output_count,
        created_at=created_at,
        updated_at=created_at,
    )


def _replicate_output(
    *,
    output_id: str,
    job_id: str,
    tenant_id: str,
    index: int,
    status: str,
    storage_key: str | None,
) -> EcomReplicateOutput:
    return EcomReplicateOutput(
        id=output_id,
        job_id=job_id,
        tenant_id=tenant_id,
        index=index,
        theme=f"theme-{index}",
        status=status,
        requested_size="768x1024",
        requested_aspect="3:4",
        actual_width=768 if storage_key else None,
        actual_height=1024 if storage_key else None,
        storage_key=storage_key,
    )


def _link_photo_output(
    db,
    *,
    task: VideoTask,
    asset_id: str,
    width: int,
    height: int,
) -> Asset:
    asset = Asset(
        id=asset_id,
        tenant_id=task.tenant_id,
        type="generated_image",
        source="generated",
        storage_key=str(task.storage_key),
        mime_type="image/png",
        width=width,
        height=height,
        status="ready",
        metadata_={"size": f"{width}x{height}"},
    )
    db.add(asset)
    db.flush()
    db.add(TaskAsset(video_task_id=task.id, asset_id=asset.id, role="output_image"))
    return asset


def test_image_history_hard_deletes_image_generation_record(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    task = _photo_task(
        task_id="delete-image-generation",
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        topic="待删除商品图",
    )
    storage_key = str(task.storage_key)
    with auth_db() as db:
        db.add(task)
        db.flush()
        asset = _link_photo_output(
            db,
            task=task,
            asset_id="delete-image-generation-asset",
            width=1024,
            height=1024,
        )
        asset_id = asset.id
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        deleted = client.delete(
            "/api/v1/history/images/image_gen/delete-image-generation",
            headers=auth_context["headers"],
        )
        listing = client.get(
            "/api/v1/history/images",
            params={"category": "image_gen"},
            headers=auth_context["headers"],
        )
        detail = client.get(
            "/api/v1/history/images/image_gen/delete-image-generation",
            headers=auth_context["headers"],
        )
        repeated = client.delete(
            "/api/v1/history/images/image_gen/delete-image-generation",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert deleted.status_code == 200
    assert deleted.json()["data"] == {
        "id": "delete-image-generation",
        "deleted": True,
    }
    assert listing.status_code == 200
    assert listing.json()["data"]["items"] == []
    assert detail.status_code == 404
    assert repeated.status_code == 404
    assert detail.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    assert repeated.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    with auth_db() as db:
        assert db.get(VideoTask, "delete-image-generation") is None
        assert db.get(Asset, asset_id) is None
        assert db.scalar(
            select(TaskAsset).where(
                TaskAsset.video_task_id == "delete-image-generation"
            )
        ) is None
    assert storage.deleted_keys == [storage_key]


def test_image_history_preserves_photo_asset_referenced_by_reverse_prompt(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    task_id = "delete-photo-reverse-source"
    asset_id = f"{task_id}-asset"
    reverse_job_id = f"{task_id}-reverse"
    task = _photo_task(
        task_id=task_id,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        topic="shared reverse prompt source",
    )
    storage_key = str(task.storage_key)
    with auth_db() as db:
        db.add(task)
        db.flush()
        _link_photo_output(
            db,
            task=task,
            asset_id=asset_id,
            width=1024,
            height=1024,
        )
        db.add(
            ReversePromptJob(
                id=reverse_job_id,
                tenant_id=tenant_id,
                source_kind="image",
                source_asset_id=asset_id,
                source_storage_key=storage_key,
                target_format="seedance_2_0",
                status="succeeded",
            )
        )
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).delete(
            f"/api/v1/history/images/image_gen/{task_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    with auth_db() as db:
        assert db.get(VideoTask, task_id) is None
        assert db.get(Asset, asset_id) is not None
        reverse_job = db.get(ReversePromptJob, reverse_job_id)
        assert reverse_job.source_asset_id == asset_id
        assert reverse_job.source_storage_key == storage_key
    assert storage.deleted_keys == []


def test_image_history_preserves_photo_asset_used_by_replicate_output(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    task_id = "delete-photo-replicate-product"
    asset_id = f"{task_id}-asset"
    job_id = f"{task_id}-job"
    output_id = f"{task_id}-output"
    task = _photo_task(
        task_id=task_id,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        topic="shared replicate product",
    )
    with auth_db() as db:
        db.add(task)
        db.flush()
        _link_photo_output(
            db,
            task=task,
            asset_id=asset_id,
            width=1024,
            height=1024,
        )
        db.add(
            _replicate_job(
                job_id=job_id,
                tenant_id=tenant_id,
                status="completed",
                created_at=datetime.now(UTC),
                name="shared replicate product",
                product_asset_id="unrelated-json-product",
                output_count=1,
            )
        )
        db.flush()
        output = _replicate_output(
            output_id=output_id,
            job_id=job_id,
            tenant_id=tenant_id,
            index=0,
            status="planned",
            storage_key=None,
        )
        output.product_asset_id = asset_id
        db.add(output)
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).delete(
            f"/api/v1/history/images/image_gen/{task_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    with auth_db() as db:
        assert db.get(VideoTask, task_id) is None
        assert db.get(Asset, asset_id) is not None
        assert db.get(EcomReplicateOutput, output_id).product_asset_id == asset_id
    assert storage.deleted_keys == []


def test_image_history_preserves_photo_media_referenced_only_by_storage_key(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    task_id = "delete-photo-reverse-key-only"
    asset_id = f"{task_id}-asset"
    reverse_job_id = f"{task_id}-reverse"
    task = _photo_task(
        task_id=task_id,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        topic="shared reverse prompt key",
    )
    storage_key = str(task.storage_key)
    with auth_db() as db:
        db.add(task)
        db.flush()
        _link_photo_output(
            db,
            task=task,
            asset_id=asset_id,
            width=1024,
            height=1024,
        )
        db.add(
            ReversePromptJob(
                id=reverse_job_id,
                tenant_id=tenant_id,
                source_kind="image",
                source_storage_key=storage_key,
                target_format="seedance_2_0",
                status="succeeded",
            )
        )
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).delete(
            f"/api/v1/history/images/image_gen/{task_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    with auth_db() as db:
        assert db.get(VideoTask, task_id) is None
        assert db.get(Asset, asset_id) is None
        assert db.get(ReversePromptJob, reverse_job_id).source_storage_key == storage_key
    assert storage.deleted_keys == []


def test_image_history_preserves_photo_media_used_as_another_task_thumbnail(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    task_id = "delete-photo-shared-thumbnail"
    asset_id = f"{task_id}-asset"
    task = _photo_task(
        task_id=task_id,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        topic="shared thumbnail source",
    )
    storage_key = str(task.storage_key)
    consumer = VideoTask(
        id=f"{task_id}-consumer",
        tenant_id=tenant_id,
        status="done",
        topic="thumbnail consumer",
        mode="avatar_talk",
        video_mode="avatar_talk",
        params={},
        thumbnail_key=storage_key,
        created_at=datetime.now(UTC),
    )
    consumer_id = consumer.id
    with auth_db() as db:
        db.add_all([task, consumer])
        db.flush()
        _link_photo_output(
            db,
            task=task,
            asset_id=asset_id,
            width=1024,
            height=1024,
        )
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).delete(
            f"/api/v1/history/images/image_gen/{task_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    with auth_db() as db:
        assert db.get(VideoTask, task_id) is None
        assert db.get(Asset, asset_id) is None
        assert db.get(VideoTask, consumer_id).thumbnail_key == storage_key
    assert storage.deleted_keys == []


@pytest.mark.parametrize(
    ("category", "category_params"),
    [
        ("ecom_white", {"kind": "ecom_cutout", "background": "white"}),
        ("ecom_model", {"kind": "ecom_model", "style_id": "studio_white"}),
        ("cover", {"kind": "cover", "purpose": "cover"}),
    ],
)
def test_image_history_hard_deletes_complete_photo_batch(
    auth_context,
    auth_db,
    category: str,
    category_params: dict[str, object],
) -> None:
    tenant_id = auth_context["tenant_id"]
    batch_id = f"delete-{category}-batch"
    task_ids = [f"delete-{category}-first", f"delete-{category}-second"]
    now = datetime.now(UTC)
    tasks = [
        _photo_task(
            task_id=task_id,
            tenant_id=tenant_id,
            created_at=now + timedelta(seconds=index),
            topic=f"delete {category}",
            params={**category_params, "batch_id": batch_id},
        )
        for index, task_id in enumerate(task_ids)
    ]
    storage_keys = [str(task.storage_key) for task in tasks]
    asset_ids: list[str] = []
    with auth_db() as db:
        db.add_all(tasks)
        db.flush()
        for task in tasks:
            asset = _link_photo_output(
                db,
                task=task,
                asset_id=f"{task.id}-asset",
                width=1024,
                height=1024,
            )
            asset_ids.append(asset.id)
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        member_delete = client.delete(
            f"/api/v1/history/images/{category}/{task_ids[0]}",
            headers=auth_context["headers"],
        )
        assert member_delete.status_code == 404
        assert member_delete.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
        with auth_db() as db:
            assert all(db.get(VideoTask, task_id) is not None for task_id in task_ids)
            assert all(db.get(Asset, asset_id) is not None for asset_id in asset_ids)
        assert storage.deleted_keys == []

        deleted = client.delete(
            f"/api/v1/history/images/{category}/{batch_id}",
            headers=auth_context["headers"],
        )
        listing = client.get(
            "/api/v1/history/images",
            params={"category": category},
            headers=auth_context["headers"],
        )
        detail = client.get(
            f"/api/v1/history/images/{category}/{batch_id}",
            headers=auth_context["headers"],
        )
        repeated = client.delete(
            f"/api/v1/history/images/{category}/{batch_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"id": batch_id, "deleted": True}
    assert listing.status_code == 200
    assert listing.json()["data"]["items"] == []
    assert detail.status_code == 404
    assert repeated.status_code == 404
    assert detail.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    assert repeated.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    with auth_db() as db:
        assert all(db.get(VideoTask, task_id) is None for task_id in task_ids)
        assert all(db.get(Asset, asset_id) is None for asset_id in asset_ids)
        assert list(
            db.scalars(
                select(TaskAsset).where(TaskAsset.video_task_id.in_(task_ids))
            )
        ) == []
    assert storage.deleted_keys == storage_keys


def test_image_history_photo_batch_delete_rolls_back_every_member_on_failure(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    batch_id = "delete-photo-atomic-batch"
    task_ids = ["delete-photo-atomic-first", "delete-photo-atomic-second"]
    asset_ids = [f"{task_id}-asset" for task_id in task_ids]
    tasks = [
        _photo_task(
            task_id=task_id,
            tenant_id=tenant_id,
            created_at=datetime.now(UTC) + timedelta(seconds=index),
            topic="atomic photo batch",
            params={"kind": "ecom_model", "batch_id": batch_id},
        )
        for index, task_id in enumerate(task_ids)
    ]
    with auth_db() as db:
        db.add_all(tasks)
        db.flush()
        for task, asset_id in zip(tasks, asset_ids, strict=True):
            _link_photo_output(
                db,
                task=task,
                asset_id=asset_id,
                width=1024,
                height=1024,
            )
        db.commit()

    def fail_when_second_member_is_deleted(session, _flush_context, _instances) -> None:
        deleted_task_ids = {
            item.id for item in session.deleted if isinstance(item, VideoTask)
        }
        if task_ids[1] in deleted_task_ids:
            raise RuntimeError("injected second-member delete failure")

    storage = _FakeStorage()
    event.listen(auth_db.class_, "before_flush", fail_when_second_member_is_deleted)
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app, raise_server_exceptions=False).delete(
            f"/api/v1/history/images/ecom_model/{batch_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)
        event.remove(auth_db.class_, "before_flush", fail_when_second_member_is_deleted)

    assert response.status_code == 500
    with auth_db() as db:
        assert all(db.get(VideoTask, task_id) is not None for task_id in task_ids)
        assert all(db.get(Asset, asset_id) is not None for asset_id in asset_ids)
        links = list(
            db.scalars(select(TaskAsset).where(TaskAsset.video_task_id.in_(task_ids)))
        )
        assert {link.video_task_id for link in links} == set(task_ids)
    assert storage.deleted_keys == []


def test_image_history_detail_exposes_each_output_size_evidence(
    auth_context,
    auth_db,
) -> None:
    task = _photo_task(
        task_id="aspect-evidence-photo",
        tenant_id=auth_context["tenant_id"],
        created_at=datetime.now(UTC),
        topic="宽幅商品图",
        params={
            "requested_aspect_ratio": "21:9",
            "resolved_aspect_ratio": "3:2",
            "resolved_size": "1536x1024",
            "actual_aspect_ratio": "16:9",
            "actual_size": "160x90",
        },
    )
    task.aspect_ratio = "21:9"
    with auth_db() as db:
        db.add(task)
        db.flush()
        asset = _link_photo_output(
            db,
            task=task,
            asset_id="aspect-evidence-asset",
            width=160,
            height=90,
        )
        asset.metadata_ = {
            **(asset.metadata_ or {}),
            "requested_aspect_ratio": "21:9",
            "resolved_aspect_ratio": "3:2",
            "resolved_size": "1536x1024",
            "actual_aspect_ratio": "16:9",
            "actual_size": "160x90",
        }
        db.commit()

    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    try:
        response = TestClient(app).get(
            "/api/v1/history/images/image_gen/aspect-evidence-photo",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["requested_aspect_ratio"] == "21:9"
    assert item["resolved_aspect_ratio"] == "3:2"
    assert item["resolved_size"] == "1536x1024"
    assert item["actual_aspect_ratio"] == "16:9"
    assert item["actual_size"] == "160x90"
    assert item["width"] == 160
    assert item["height"] == 90


def test_image_history_paginates_image_generation_newest_first(auth_context, auth_db) -> None:
    now = datetime.now(UTC)
    db = auth_db()
    db.add_all(
        [
            _photo_task(
                task_id="plain-image-older",
                tenant_id=auth_context["tenant_id"],
                created_at=now - timedelta(minutes=2),
                topic="旧商品图",
            ),
            _photo_task(
                task_id="plain-image-newer",
                tenant_id=auth_context["tenant_id"],
                created_at=now - timedelta(minutes=1),
                topic="新商品图",
            ),
        ]
    )
    db.commit()
    db.close()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        response = client.get(
            "/api/v1/history/images",
            params={"category": "image_gen", "page": 1, "page_size": 1},
            headers=auth_context["headers"],
        )
        second_page = client.get(
            "/api/v1/history/images",
            params={"category": "image_gen", "page": 2, "page_size": 1},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    created_at = datetime.fromisoformat(item.pop("created_at"))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    assert created_at == now - timedelta(minutes=1)
    assert item == {
        "id": "plain-image-newer",
        "category": "image_gen",
        "title": "新商品图",
        "cover_url": (
            "https://storage.test/"
            f"tenants/{auth_context['tenant_id']}/videos/plain-image-newer/output.png"
            "?ttl=3600"
        ),
        "status": "ready",
        "item_count": 1,
    }
    assert second_page.status_code == 200
    assert [item["id"] for item in second_page.json()["data"]["items"]] == [
        "plain-image-older"
    ]


def test_image_history_groups_white_background_batches(auth_context, auth_db) -> None:
    now = datetime.now(UTC)
    tenant_id = auth_context["tenant_id"]
    db = auth_db()
    db.add_all(
        [
            _photo_task(
                task_id="white-batch-first",
                tenant_id=tenant_id,
                created_at=now - timedelta(minutes=3),
                topic="white cutout",
                params={
                    "kind": "ecom_cutout",
                    "background": "white",
                    "batch_id": "white-batch",
                },
            ),
            _photo_task(
                task_id="white-batch-second",
                tenant_id=tenant_id,
                created_at=now - timedelta(minutes=2),
                topic="white cutout",
                params={
                    "kind": "ecom_cutout",
                    "background": "white",
                    "batch_id": "white-batch",
                },
            ),
            _photo_task(
                task_id="white-single",
                tenant_id=tenant_id,
                created_at=now - timedelta(minutes=4),
                topic="transparent cutout",
                params={"kind": "ecom_cutout", "background": "transparent"},
            ),
        ]
    )
    db.commit()
    db.close()

    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    try:
        response = TestClient(app).get(
            "/api/v1/history/images",
            params={"category": "ecom_white"},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert [item["id"] for item in data["items"]] == ["white-batch", "white-single"]
    assert data["items"][0]["title"] == "白底图"
    assert data["items"][0]["item_count"] == 2
    assert data["items"][0]["cover_url"].endswith("/white-batch-first/output.png?ttl=3600")
    assert data["items"][1]["title"] == "透明底图"
    assert data["items"][1]["item_count"] == 1


def test_image_history_unifies_photo_categories_and_excludes_non_history_rows(
    auth_context,
    auth_db,
) -> None:
    now = datetime.now(UTC)
    tenant_id = auth_context["tenant_id"]
    other_tenant_id = "other-history-tenant"
    db = auth_db()
    db.add(Tenant(id=other_tenant_id, slug="other-history", name="Other History"))

    plain = _photo_task(
        task_id="plain-history",
        tenant_id=tenant_id,
        created_at=now - timedelta(minutes=5),
        topic="普通商品图",
    )
    white = _photo_task(
        task_id="white-history",
        tenant_id=tenant_id,
        created_at=now - timedelta(minutes=4),
        topic="white cutout",
        params={"kind": "ecom_cutout", "background": "white"},
    )
    model = _photo_task(
        task_id="model-history",
        tenant_id=tenant_id,
        created_at=now - timedelta(minutes=3),
        topic="model prompt",
        params={
            "kind": "ecom_model",
            "style_id": "studio_white",
            "extra_prompt": "夏日通勤",
        },
    )
    poster = _photo_task(
        task_id="poster-hidden",
        tenant_id=tenant_id,
        created_at=now - timedelta(minutes=2),
        topic="海报",
        params={"kind": "ecom_poster"},
    )
    cover = _photo_task(
        task_id="cover-hidden",
        tenant_id=tenant_id,
        created_at=now - timedelta(minutes=1),
        topic="封面",
        params={"kind": "cover", "purpose": "cover"},
    )
    failed = _photo_task(
        task_id="failed-hidden",
        tenant_id=tenant_id,
        created_at=now,
        topic="失败图片",
    )
    failed.status = "failed"
    failed.storage_key = None
    failed.thumbnail_key = None
    other_tenant = _photo_task(
        task_id="other-tenant-hidden",
        tenant_id=other_tenant_id,
        created_at=now + timedelta(minutes=1),
        topic="其他租户图片",
    )
    db.add_all([plain, white, model, poster, cover, failed, other_tenant])
    db.commit()
    db.close()

    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    try:
        response = TestClient(app).get(
            "/api/v1/history/images",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 4
    assert [(item["id"], item["category"]) for item in data["items"]] == [
        ("cover-hidden", "cover"),
        ("model-history", "ecom_model"),
        ("white-history", "ecom_white"),
        ("plain-history", "image_gen"),
    ]
    assert data["items"][0]["title"] == "封面"
    assert data["items"][1]["title"] == "夏日通勤"
    assert all(item["status"] == "ready" for item in data["items"])


def test_image_history_lists_and_opens_cover_without_mixing_image_generation(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    cover = _photo_task(
        task_id="cover-history-item",
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        topic="商品主封面",
        params={
            "kind": "cover",
            "purpose": "cover",
            "source": "frame",
            "source_video_task_id": "source-video-task",
            "timestamp_sec": 2.5,
            "layout_template_id": "title-bottom",
        },
    )
    safe_key = str(cover.storage_key)
    foreign_thumbnail = "tenants/another-tenant/thumbnails/foreign.png"
    cover.thumbnail_key = foreign_thumbnail
    poster = _photo_task(
        task_id="poster-still-hidden",
        tenant_id=tenant_id,
        created_at=datetime.now(UTC) - timedelta(seconds=1),
        topic="旧营销海报",
        params={"kind": "ecom_poster"},
    )
    with auth_db() as db:
        db.add_all([cover, poster])
        db.flush()
        _link_photo_output(
            db,
            task=cover,
            asset_id="cover-history-asset",
            width=1280,
            height=720,
        )
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        cover_list = client.get(
            "/api/v1/history/images",
            params={"category": "cover"},
            headers=auth_context["headers"],
        )
        cover_detail = client.get(
            "/api/v1/history/images/cover/cover-history-item",
            headers=auth_context["headers"],
        )
        image_gen_list = client.get(
            "/api/v1/history/images",
            params={"category": "image_gen"},
            headers=auth_context["headers"],
        )
        all_images = client.get(
            "/api/v1/history/images",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert cover_list.status_code == 200
    assert cover_list.json()["data"] == {
        "items": [
            {
                "id": "cover-history-item",
                "category": "cover",
                "title": "商品主封面",
                "cover_url": f"https://storage.test/{safe_key}?ttl=3600",
                "created_at": cover_list.json()["data"]["items"][0]["created_at"],
                "status": "ready",
                "item_count": 1,
            }
        ],
        "total": 1,
        "page": 1,
        "page_size": 20,
    }
    assert cover_detail.status_code == 200
    detail = cover_detail.json()["data"]
    assert detail["category"] == "cover"
    assert detail["items"][0]["width"] == 1280
    assert detail["items"][0]["height"] == 720
    assert detail["items"][0]["download_url"].startswith(
        f"https://storage.test/{safe_key}?ttl=3600"
    )
    assert detail["meta"] == {
        "task_ids": ["cover-history-item"],
        "source": "frame",
        "source_video_task_id": "source-video-task",
        "timestamp_sec": 2.5,
        "layout_template_id": "title-bottom",
    }
    assert image_gen_list.status_code == 200
    assert image_gen_list.json()["data"]["items"] == []
    assert [item["id"] for item in all_images.json()["data"]["items"]] == [
        "cover-history-item"
    ]
    assert foreign_thumbnail not in cover_list.text
    assert foreign_thumbnail not in storage.presigned_keys
    assert safe_key in storage.presigned_keys


def test_image_history_lists_only_terminal_replicate_jobs(auth_context, auth_db) -> None:
    now = datetime.now(UTC)
    tenant_id = auth_context["tenant_id"]
    product_asset_id = "replicate-product"
    db = auth_db()
    db.add(
        Asset(
            id=product_asset_id,
            tenant_id=tenant_id,
            type="product_image",
            source="upload",
            storage_key=f"tenants/{tenant_id}/uploads/product.png",
            mime_type="image/png",
            status="ready",
        )
    )
    completed = _replicate_job(
        job_id="detail-completed",
        tenant_id=tenant_id,
        status="completed",
        created_at=now - timedelta(minutes=3),
        name="完成套图",
        product_asset_id=product_asset_id,
        output_count=1,
    )
    partial = _replicate_job(
        job_id="detail-partial",
        tenant_id=tenant_id,
        status="partial_failed",
        created_at=now - timedelta(minutes=2),
        name="部分成功套图",
        product_asset_id=product_asset_id,
        output_count=2,
    )
    failed = _replicate_job(
        job_id="detail-failed",
        tenant_id=tenant_id,
        status="failed",
        created_at=now - timedelta(minutes=1),
        name="失败套图",
        product_asset_id=product_asset_id,
        output_count=1,
    )
    planning = _replicate_job(
        job_id="detail-planning-hidden",
        tenant_id=tenant_id,
        status="planning",
        created_at=now,
        name="规划中套图",
        product_asset_id=product_asset_id,
        output_count=1,
    )
    db.add_all([completed, partial, failed, planning])
    db.flush()
    db.add_all(
        [
            _replicate_output(
                output_id="completed-output",
                job_id=completed.id,
                tenant_id=tenant_id,
                index=0,
                status="succeeded",
                storage_key=f"tenants/{tenant_id}/ecom-replicate/completed.png",
            ),
            _replicate_output(
                output_id="partial-output-ok",
                job_id=partial.id,
                tenant_id=tenant_id,
                index=0,
                status="succeeded",
                storage_key=f"tenants/{tenant_id}/ecom-replicate/partial.png",
            ),
            _replicate_output(
                output_id="partial-output-failed",
                job_id=partial.id,
                tenant_id=tenant_id,
                index=1,
                status="failed",
                storage_key=None,
            ),
            _replicate_output(
                output_id="failed-output",
                job_id=failed.id,
                tenant_id=tenant_id,
                index=0,
                status="failed",
                storage_key=None,
            ),
        ]
    )
    db.commit()
    db.close()

    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    try:
        response = TestClient(app).get(
            "/api/v1/history/images",
            params={"category": "ecom_detail"},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [(item["id"], item["status"], item["item_count"]) for item in items] == [
        ("detail-failed", "failed", 0),
        ("detail-partial", "partial_failed", 1),
        ("detail-completed", "completed", 1),
    ]
    assert items[0]["title"] == "失败套图"
    assert items[0]["cover_url"].endswith("/uploads/product.png?ttl=3600")
    assert items[1]["cover_url"].endswith(
        "/ecom-replicate/partial.png?ttl=3600&download=1"
    )


def test_image_history_hard_deletes_replicate_job_and_preserves_source_assets(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    job_id = "delete-detail-job"
    product_asset_id = "delete-detail-product"
    reference_asset_id = "delete-detail-reference"
    source_asset_ids = [product_asset_id, reference_asset_id]
    generated_asset_ids = [
        "delete-detail-generated-first",
        "delete-detail-generated-second",
    ]
    output_ids = ["delete-detail-output-first", "delete-detail-output-second"]
    storage_keys = [
        f"tenants/{tenant_id}/ecom-replicate/{job_id}/00.png",
        f"tenants/{tenant_id}/ecom-replicate/{job_id}/01.png",
    ]
    job = _replicate_job(
        job_id=job_id,
        tenant_id=tenant_id,
        status="partial_failed",
        created_at=datetime.now(UTC),
        name="delete detail set",
        product_asset_id=product_asset_id,
        output_count=2,
    )
    job.reference_image_asset_ids = [reference_asset_id]
    outputs = [
        _replicate_output(
            output_id=output_ids[0],
            job_id=job_id,
            tenant_id=tenant_id,
            index=0,
            status="succeeded",
            storage_key=storage_keys[0],
        ),
        _replicate_output(
            output_id=output_ids[1],
            job_id=job_id,
            tenant_id=tenant_id,
            index=1,
            status="failed",
            storage_key=storage_keys[1],
        ),
    ]
    with auth_db() as db:
        db.add_all(
            [
                Asset(
                    id=product_asset_id,
                    tenant_id=tenant_id,
                    type="product_image",
                    source="upload",
                    storage_key=f"tenants/{tenant_id}/uploads/product.png",
                    mime_type="image/png",
                    status="ready",
                ),
                Asset(
                    id=reference_asset_id,
                    tenant_id=tenant_id,
                    type="product_image",
                    source="upload",
                    storage_key=f"tenants/{tenant_id}/uploads/reference.png",
                    mime_type="image/png",
                    status="ready",
                ),
                job,
            ]
        )
        db.flush()
        db.add_all(outputs)
        db.add_all(
            [
                Asset(
                    id=asset_id,
                    tenant_id=tenant_id,
                    type="generated_image",
                    source="generated",
                    storage_key=storage_key,
                    mime_type="image/png",
                    status="ready",
                    metadata_={
                        "kind": "ecom_replicate",
                        "job_id": job_id,
                        "output_id": output_id,
                        "output_index": index,
                    },
                )
                for index, (asset_id, storage_key, output_id) in enumerate(
                    zip(
                        generated_asset_ids,
                        storage_keys,
                        output_ids,
                        strict=True,
                    )
                )
            ]
        )
        db.flush()
        for output, asset_id in zip(outputs, generated_asset_ids, strict=True):
            output.asset_id = asset_id
            output.product_asset_id = product_asset_id
            output.reference_asset_id = reference_asset_id
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        deleted = client.delete(
            f"/api/v1/history/images/ecom_detail/{job_id}",
            headers=auth_context["headers"],
        )
        listing = client.get(
            "/api/v1/history/images",
            params={"category": "ecom_detail"},
            headers=auth_context["headers"],
        )
        detail = client.get(
            f"/api/v1/history/images/ecom_detail/{job_id}",
            headers=auth_context["headers"],
        )
        repeated = client.delete(
            f"/api/v1/history/images/ecom_detail/{job_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"id": job_id, "deleted": True}
    assert listing.status_code == 200
    assert listing.json()["data"]["items"] == []
    assert detail.status_code == 404
    assert repeated.status_code == 404
    assert detail.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    assert repeated.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    with auth_db() as db:
        assert db.get(EcomReplicateJob, job_id) is None
        assert all(db.get(EcomReplicateOutput, output_id) is None for output_id in output_ids)
        assert all(db.get(Asset, asset_id) is None for asset_id in generated_asset_ids)
        assert all(db.get(Asset, asset_id) is not None for asset_id in source_asset_ids)
    assert storage.deleted_keys == storage_keys


def test_image_history_preserves_upload_key_when_replicate_output_has_no_asset_id(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    job_id = "delete-detail-unlinked-upload"
    output_id = f"{job_id}-output"
    upload_asset_id = f"{job_id}-upload"
    upload_key = f"tenants/{tenant_id}/uploads/product.png"
    with auth_db() as db:
        db.add(
            Asset(
                id=upload_asset_id,
                tenant_id=tenant_id,
                type="product_image",
                source="upload",
                storage_key=upload_key,
                mime_type="image/png",
                status="ready",
            )
        )
        db.add(
            _replicate_job(
                job_id=job_id,
                tenant_id=tenant_id,
                status="completed",
                created_at=datetime.now(UTC),
                name="unlinked upload output",
                product_asset_id=upload_asset_id,
                output_count=1,
            )
        )
        db.flush()
        db.add(
            _replicate_output(
                output_id=output_id,
                job_id=job_id,
                tenant_id=tenant_id,
                index=0,
                status="succeeded",
                storage_key=upload_key,
            )
        )
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).delete(
            f"/api/v1/history/images/ecom_detail/{job_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    with auth_db() as db:
        assert db.get(EcomReplicateJob, job_id) is None
        assert db.get(EcomReplicateOutput, output_id) is None
        assert db.get(Asset, upload_asset_id) is not None
    assert storage.deleted_keys == []


def test_image_history_delete_hides_cross_tenant_records(
    auth_context,
    auth_db,
) -> None:
    other_tenant_id = "delete-history-other-tenant"
    photo_id = "delete-history-other-photo"
    job_id = "delete-history-other-detail"
    other_photo = _photo_task(
        task_id=photo_id,
        tenant_id=other_tenant_id,
        created_at=datetime.now(UTC),
        topic="other tenant photo",
    )
    disguised_key = f"tenants/{auth_context['tenant_id']}/videos/{photo_id}/output.png"
    other_photo.storage_key = disguised_key
    other_photo.thumbnail_key = disguised_key
    with auth_db() as db:
        db.add(
            Tenant(
                id=other_tenant_id,
                slug="delete-history-other",
                name="Delete History Other",
            )
        )
        db.flush()
        db.add(other_photo)
        db.add(
            _replicate_job(
                job_id=job_id,
                tenant_id=other_tenant_id,
                status="failed",
                created_at=datetime.now(UTC),
                name="other tenant detail",
                product_asset_id="other-tenant-product",
                output_count=0,
            )
        )
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        photo_response = client.delete(
            f"/api/v1/history/images/image_gen/{photo_id}",
            headers=auth_context["headers"],
        )
        detail_response = client.delete(
            f"/api/v1/history/images/ecom_detail/{job_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert photo_response.status_code == 404
    assert detail_response.status_code == 404
    assert photo_response.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    assert detail_response.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    with auth_db() as db:
        assert db.get(VideoTask, photo_id) is not None
        assert db.get(EcomReplicateJob, job_id) is not None
    assert storage.deleted_keys == []


def test_image_history_replicate_storage_delete_is_best_effort(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    job_id = "delete-detail-storage-failure"
    output_id = f"{job_id}-output"
    asset_id = f"{job_id}-asset"
    storage_key = f"tenants/{tenant_id}/ecom-replicate/{job_id}/00.png"
    output = _replicate_output(
        output_id=output_id,
        job_id=job_id,
        tenant_id=tenant_id,
        index=0,
        status="succeeded",
        storage_key=storage_key,
    )
    with auth_db() as db:
        db.add(
            _replicate_job(
                job_id=job_id,
                tenant_id=tenant_id,
                status="completed",
                created_at=datetime.now(UTC),
                name="storage failure detail",
                product_asset_id="storage-failure-product",
                output_count=1,
            )
        )
        db.flush()
        db.add(output)
        db.add(
            Asset(
                id=asset_id,
                tenant_id=tenant_id,
                type="generated_image",
                source="generated",
                storage_key=storage_key,
                mime_type="image/png",
                status="ready",
                metadata_={
                    "kind": "ecom_replicate",
                    "job_id": job_id,
                    "output_id": output_id,
                    "output_index": 0,
                },
            )
        )
        db.flush()
        output.asset_id = asset_id
        db.commit()

    storage = _FakeStorage()
    storage.fail_deletes = True
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).delete(
            f"/api/v1/history/images/ecom_detail/{job_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    assert response.json()["data"] == {"id": job_id, "deleted": True}
    with auth_db() as db:
        assert db.get(EcomReplicateJob, job_id) is None
        assert db.get(EcomReplicateOutput, output_id) is None
        assert db.get(Asset, asset_id) is None
    assert storage.deleted_keys == [storage_key]


def test_image_history_delete_rejects_replicate_output_link_to_source_asset(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    job_id = "delete-detail-source-output-link"
    output_id = f"{job_id}-output"
    source_asset_id = f"{job_id}-source"
    source_key = f"tenants/{tenant_id}/uploads/product.png"
    output = _replicate_output(
        output_id=output_id,
        job_id=job_id,
        tenant_id=tenant_id,
        index=0,
        status="succeeded",
        storage_key=source_key,
    )
    with auth_db() as db:
        db.add(
            Asset(
                id=source_asset_id,
                tenant_id=tenant_id,
                type="product_image",
                source="upload",
                storage_key=source_key,
                mime_type="image/png",
                status="ready",
            )
        )
        db.add(
            _replicate_job(
                job_id=job_id,
                tenant_id=tenant_id,
                status="completed",
                created_at=datetime.now(UTC),
                name="source output link",
                product_asset_id=source_asset_id,
                output_count=1,
            )
        )
        db.flush()
        db.add(output)
        db.flush()
        output.asset_id = source_asset_id
        output.product_asset_id = source_asset_id
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).delete(
            f"/api/v1/history/images/ecom_detail/{job_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    with auth_db() as db:
        assert db.get(EcomReplicateJob, job_id) is not None
        assert db.get(EcomReplicateOutput, output_id) is not None
        assert db.get(Asset, source_asset_id) is not None
    assert storage.deleted_keys == []


@pytest.mark.parametrize(
    "foreign_target",
    ["task", "thumbnail", "asset", "asset_tenant"],
)
def test_image_history_delete_preflights_every_photo_storage_key(
    auth_context,
    auth_db,
    foreign_target: str,
) -> None:
    tenant_id = auth_context["tenant_id"]
    task_id = f"delete-photo-foreign-{foreign_target}"
    asset_id = f"{task_id}-asset"
    foreign_key = f"tenants/foreign-tenant/images/{foreign_target}.png"
    task = _photo_task(
        task_id=task_id,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        topic="foreign storage key",
    )
    with auth_db() as db:
        if foreign_target == "asset_tenant":
            db.add(
                Tenant(
                    id="foreign-photo-asset-tenant",
                    slug="foreign-photo-asset",
                    name="Foreign Photo Asset",
                )
            )
            db.flush()
        db.add(task)
        db.flush()
        asset = _link_photo_output(
            db,
            task=task,
            asset_id=asset_id,
            width=1024,
            height=1024,
        )
        if foreign_target == "task":
            task.storage_key = foreign_key
        elif foreign_target == "thumbnail":
            task.thumbnail_key = foreign_key
        elif foreign_target == "asset":
            asset.storage_key = foreign_key
        else:
            asset.tenant_id = "foreign-photo-asset-tenant"
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).delete(
            f"/api/v1/history/images/image_gen/{task_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    with auth_db() as db:
        assert db.get(VideoTask, task_id) is not None
        assert db.get(Asset, asset_id) is not None
        assert db.scalar(
            select(TaskAsset).where(TaskAsset.video_task_id == task_id)
        ) is not None
    assert storage.deleted_keys == []


def test_image_history_delete_rejects_tenant_key_path_traversal(
    auth_context,
    auth_db,
    tmp_path,
) -> None:
    tenant_id = auth_context["tenant_id"]
    victim_tenant_id = "history-traversal-victim"
    task_id = "delete-photo-path-traversal"
    victim_key = f"tenants/{victim_tenant_id}/victim.png"
    traversal_key = f"tenants/{tenant_id}/../{victim_tenant_id}/victim.png"
    task = _photo_task(
        task_id=task_id,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        topic="path traversal delete",
    )
    task.storage_key = traversal_key
    task.thumbnail_key = traversal_key
    with auth_db() as db:
        db.add(
            Tenant(
                id=victim_tenant_id,
                slug="history-traversal-victim",
                name="History Traversal Victim",
            )
        )
        db.add(task)
        db.commit()

    storage = LocalObjectStorage(str(tmp_path))
    storage.put_bytes(victim_key, b"victim", content_type="image/png")
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).delete(
            f"/api/v1/history/images/image_gen/{task_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    with auth_db() as db:
        assert db.get(VideoTask, task_id) is not None
    assert storage.get_bytes(victim_key) == b"victim"


def test_image_history_detail_rejects_tenant_key_path_traversal(
    auth_context,
    auth_db,
    tmp_path,
) -> None:
    tenant_id = auth_context["tenant_id"]
    victim_tenant_id = "history-presign-victim"
    task_id = "read-photo-path-traversal"
    victim_key = f"tenants/{victim_tenant_id}/victim.png"
    traversal_key = f"tenants/{tenant_id}/../{victim_tenant_id}/victim.png"
    task = _photo_task(
        task_id=task_id,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        topic="path traversal read",
    )
    task.storage_key = traversal_key
    task.thumbnail_key = traversal_key
    with auth_db() as db:
        db.add(
            Tenant(
                id=victim_tenant_id,
                slug="history-presign-victim",
                name="History Presign Victim",
            )
        )
        db.add(task)
        db.commit()

    storage = LocalObjectStorage(str(tmp_path))
    storage.put_bytes(victim_key, b"victim", content_type="image/png")
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).get(
            f"/api/v1/history/images/image_gen/{task_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    assert storage.get_bytes(victim_key) == b"victim"


def test_image_history_tenant_key_validator_rejects_encoded_and_noncanonical_paths() -> None:
    tenant_id = "tenant-key-validation"
    safe_key = f"tenants/{tenant_id}/images/output.png"
    assert _validated_tenant_storage_key(tenant_id, safe_key) == safe_key

    unsafe_keys = (
        f"tenants/{tenant_id}/../victim/output.png",
        f"tenants/{tenant_id}/%2e%2e/victim/output.png",
        f"tenants/{tenant_id}/%252e%252e%252fvictim/output.png",
        f"tenants/{tenant_id}/images\\..\\victim.png",
        f"/tenants/{tenant_id}/images/output.png",
        f"tenants/{tenant_id}//images/output.png",
        f"tenants/{tenant_id}/./images/output.png",
    )
    for storage_key in unsafe_keys:
        with pytest.raises(AppError) as exc_info:
            _validated_tenant_storage_key(tenant_id, storage_key)
        assert exc_info.value.code == "IMAGE_HISTORY_NOT_FOUND"
        assert exc_info.value.status_code == 404


@pytest.mark.parametrize("foreign_target", ["output", "asset"])
def test_image_history_delete_preflights_every_replicate_storage_key(
    auth_context,
    auth_db,
    foreign_target: str,
) -> None:
    tenant_id = auth_context["tenant_id"]
    job_id = f"delete-detail-foreign-{foreign_target}"
    output_id = f"{job_id}-output"
    asset_id = f"{job_id}-asset"
    safe_key = f"tenants/{tenant_id}/ecom-replicate/{job_id}/00.png"
    foreign_key = f"tenants/foreign-tenant/ecom-replicate/{job_id}/00.png"
    output_key = foreign_key if foreign_target == "output" else safe_key
    asset_key = foreign_key if foreign_target == "asset" else safe_key
    job = _replicate_job(
        job_id=job_id,
        tenant_id=tenant_id,
        status="completed",
        created_at=datetime.now(UTC),
        name="foreign replicate storage key",
        product_asset_id="foreign-key-product",
        output_count=1,
    )
    output = _replicate_output(
        output_id=output_id,
        job_id=job_id,
        tenant_id=tenant_id,
        index=0,
        status="succeeded",
        storage_key=output_key,
    )
    with auth_db() as db:
        db.add(job)
        db.flush()
        db.add(output)
        db.add(
            Asset(
                id=asset_id,
                tenant_id=tenant_id,
                type="generated_image",
                source="generated",
                storage_key=asset_key,
                mime_type="image/png",
                status="ready",
                metadata_={"kind": "ecom_replicate", "job_id": job_id},
            )
        )
        db.flush()
        output.asset_id = asset_id
        db.commit()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).delete(
            f"/api/v1/history/images/ecom_detail/{job_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    with auth_db() as db:
        assert db.get(EcomReplicateJob, job_id) is not None
        assert db.get(EcomReplicateOutput, output_id) is not None
        assert db.get(Asset, asset_id) is not None
    assert storage.deleted_keys == []


def test_image_history_delete_rejects_unknown_category(
    auth_context,
) -> None:
    response = TestClient(app).delete(
        "/api/v1/history/images/not-a-category/history-id",
        headers=auth_context["headers"],
    )

    assert response.status_code == 422


def test_image_history_reopens_complete_model_batch(auth_context, auth_db) -> None:
    now = datetime.now(UTC)
    tenant_id = auth_context["tenant_id"]
    params = {
        "kind": "ecom_model",
        "batch_id": "model-history-batch",
        "style_id": "street",
        "gender": "female",
        "extra_prompt": "都市通勤",
        "source_asset_id": "source-product",
    }
    first = _photo_task(
        task_id="model-batch-first",
        tenant_id=tenant_id,
        created_at=now - timedelta(minutes=2),
        topic="model prompt",
        params=params,
    )
    second = _photo_task(
        task_id="model-batch-second",
        tenant_id=tenant_id,
        created_at=now - timedelta(minutes=1),
        topic="model prompt",
        params=params,
    )
    db = auth_db()
    db.add_all([first, second])
    db.flush()
    _link_photo_output(db, task=first, asset_id="model-asset-first", width=800, height=1200)
    _link_photo_output(db, task=second, asset_id="model-asset-second", width=900, height=1350)
    db.commit()
    db.close()

    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    try:
        response = TestClient(app).get(
            "/api/v1/history/images/ecom_model/model-history-batch",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == "model-history-batch"
    assert data["category"] == "ecom_model"
    assert data["status"] == "ready"
    assert data["items"] == [
        {
            "index": 0,
            "download_url": (
                "https://storage.test/"
                f"tenants/{tenant_id}/videos/model-batch-first/output.png"
                "?ttl=3600&download=1"
            ),
            "width": 800,
            "height": 1200,
        },
        {
            "index": 1,
            "download_url": (
                "https://storage.test/"
                f"tenants/{tenant_id}/videos/model-batch-second/output.png"
                "?ttl=3600&download=1"
            ),
            "width": 900,
            "height": 1350,
        },
    ]
    assert data["meta"] == {
        "batch_id": "model-history-batch",
        "task_ids": ["model-batch-first", "model-batch-second"],
        "style_id": "street",
        "gender": "female",
        "extra_prompt": "都市通勤",
        "source_asset_id": "source-product",
    }


def test_image_history_reuses_replicate_detail_download_urls(auth_context, auth_db) -> None:
    now = datetime.now(UTC)
    tenant_id = auth_context["tenant_id"]
    product_asset_id = "detail-source-product"
    job = _replicate_job(
        job_id="detail-history-job",
        tenant_id=tenant_id,
        status="partial_failed",
        created_at=now,
        name="便携咖啡杯详情套图",
        product_asset_id=product_asset_id,
        output_count=3,
    )
    job_id = job.id
    job.selling_points = ["防漏保温"]
    job.generation_plan_json = {"layout": "detail"}
    db = auth_db()
    db.add(
        Asset(
            id=product_asset_id,
            tenant_id=tenant_id,
            type="product_image",
            source="upload",
            storage_key=f"tenants/{tenant_id}/uploads/detail-product.png",
            mime_type="image/png",
            status="ready",
        )
    )
    db.add(job)
    db.flush()
    db.add_all(
        [
            _replicate_output(
                output_id="detail-history-output-0",
                job_id=job.id,
                tenant_id=tenant_id,
                index=0,
                status="succeeded",
                storage_key=f"tenants/{tenant_id}/ecom-replicate/detail-0.png",
            ),
            _replicate_output(
                output_id="detail-history-output-1",
                job_id=job.id,
                tenant_id=tenant_id,
                index=1,
                status="failed",
                storage_key=None,
            ),
            _replicate_output(
                output_id="detail-history-output-2",
                job_id=job.id,
                tenant_id=tenant_id,
                index=2,
                status="succeeded",
                storage_key=f"tenants/{tenant_id}/ecom-replicate/detail-2.png",
            ),
        ]
    )
    db.commit()
    db.close()

    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    client = TestClient(app)
    try:
        existing_response = client.get(
            f"/api/v1/ecom-images/replicate/{job_id}",
            headers=auth_context["headers"],
        )
        history_response = client.get(
            f"/api/v1/history/images/ecom_detail/{job_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert existing_response.status_code == 200
    assert history_response.status_code == 200
    existing_outputs = existing_response.json()["data"]["plan"]["outputs"]
    expected_urls = [item["download_url"] for item in existing_outputs if item["download_url"]]
    data = history_response.json()["data"]
    assert [item["download_url"] for item in data["items"]] == expected_urls
    assert [(item["index"], item["theme"]) for item in data["items"]] == [
        (0, "theme-0"),
        (2, "theme-2"),
    ]
    assert all((item["width"], item["height"]) == (768, 1024) for item in data["items"])
    assert data["meta"] == {
        "output_mode": "detail",
        "requested_size": "768x1024",
        "requested_aspect": "3:4",
        "output_count": 3,
        "product_info": {"name": "便携咖啡杯详情套图"},
        "selling_points": ["防漏保温"],
        "reference_analysis_json": [],
        "template_mapping_json": {},
        "generation_plan_json": {"layout": "detail"},
    }


def test_image_history_requires_authentication(auth_db) -> None:
    response = TestClient(app).get("/api/v1/history/images")

    assert response.status_code == 401


def test_image_history_returns_empty_page(auth_context) -> None:
    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    try:
        response = TestClient(app).get(
            "/api/v1/history/images",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
    }


def test_image_history_detail_hides_cross_tenant_rows(auth_context, auth_db) -> None:
    other_tenant_id = "other-detail-tenant"
    now = datetime.now(UTC)
    db = auth_db()
    db.add(Tenant(id=other_tenant_id, slug="other-detail", name="Other Detail"))
    db.flush()
    db.add(
        _photo_task(
            task_id="other-photo-detail",
            tenant_id=other_tenant_id,
            created_at=now,
            topic="其他租户图片",
        )
    )
    db.add(
        _replicate_job(
            job_id="other-replicate-detail",
            tenant_id=other_tenant_id,
            status="completed",
            created_at=now,
            name="其他租户套图",
            product_asset_id="other-product",
            output_count=0,
        )
    )
    db.commit()
    db.close()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        list_response = client.get(
            "/api/v1/history/images",
            headers=auth_context["headers"],
        )
        photo_response = client.get(
            "/api/v1/history/images/image_gen/other-photo-detail",
            headers=auth_context["headers"],
        )
        detail_response = client.get(
            "/api/v1/history/images/ecom_detail/other-replicate-detail",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert list_response.status_code == 200
    assert list_response.json()["data"]["items"] == []
    assert photo_response.status_code == 404
    assert detail_response.status_code == 404
    assert photo_response.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"
    assert detail_response.json()["error"]["code"] == "IMAGE_HISTORY_NOT_FOUND"


def test_image_history_global_page_includes_replicate_jobs(auth_context, auth_db) -> None:
    now = datetime.now(UTC)
    tenant_id = auth_context["tenant_id"]
    product_asset_id = "global-detail-product"
    db = auth_db()
    db.add(
        Asset(
            id=product_asset_id,
            tenant_id=tenant_id,
            type="product_image",
            source="upload",
            storage_key=f"tenants/{tenant_id}/uploads/global-product.png",
            mime_type="image/png",
            status="ready",
        )
    )
    db.add(
        _photo_task(
            task_id="global-plain-older",
            tenant_id=tenant_id,
            created_at=now - timedelta(minutes=1),
            topic="较早普通图",
        )
    )
    db.add(
        _replicate_job(
            job_id="global-detail-newer",
            tenant_id=tenant_id,
            status="failed",
            created_at=now,
            name="较新详情图",
            product_asset_id=product_asset_id,
            output_count=0,
        )
    )
    db.commit()
    db.close()

    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    try:
        response = TestClient(app).get(
            "/api/v1/history/images",
            params={"page_size": 1},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert [(item["id"], item["category"]) for item in data["items"]] == [
        ("global-detail-newer", "ecom_detail")
    ]


def test_image_history_reopens_image_generation_and_white_background(
    auth_context,
    auth_db,
) -> None:
    now = datetime.now(UTC)
    tenant_id = auth_context["tenant_id"]
    plain = _photo_task(
        task_id="plain-detail",
        tenant_id=tenant_id,
        created_at=now - timedelta(minutes=1),
        topic="精致保温杯",
        params={"image_size": "640x480", "image_quality": "high"},
    )
    white = _photo_task(
        task_id="white-detail",
        tenant_id=tenant_id,
        created_at=now,
        topic="white cutout",
        params={
            "kind": "ecom_cutout",
            "background": "white",
            "source_asset_id": "white-source",
            "image_size": "1024x1024",
        },
    )
    db = auth_db()
    db.add_all([plain, white])
    db.commit()
    db.close()

    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    client = TestClient(app)
    try:
        plain_response = client.get(
            "/api/v1/history/images/image_gen/plain-detail",
            headers=auth_context["headers"],
        )
        white_response = client.get(
            "/api/v1/history/images/ecom_white/white-detail",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert plain_response.status_code == 200
    assert white_response.status_code == 200
    plain_data = plain_response.json()["data"]
    white_data = white_response.json()["data"]
    assert plain_data["items"][0]["width"] == 640
    assert plain_data["items"][0]["height"] == 480
    assert plain_data["meta"] == {
        "task_ids": ["plain-detail"],
        "prompt": "精致保温杯",
        "image_size": "640x480",
        "image_quality": "high",
    }
    assert white_data["items"][0]["width"] == 1024
    assert white_data["items"][0]["height"] == 1024
    assert white_data["meta"] == {
        "task_ids": ["white-detail"],
        "background": "white",
        "source_asset_id": "white-source",
    }


def test_image_history_rejects_foreign_storage_key_on_tenant_row(
    auth_context,
    auth_db,
) -> None:
    task = _photo_task(
        task_id="foreign-key-photo",
        tenant_id=auth_context["tenant_id"],
        created_at=datetime.now(UTC),
        topic="脏数据图片",
    )
    task.storage_key = "tenants/another-tenant/videos/foreign/output.png"
    task.thumbnail_key = task.storage_key
    db = auth_db()
    db.add(task)
    db.commit()
    db.close()

    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    client = TestClient(app)
    try:
        list_response = client.get(
            "/api/v1/history/images",
            params={"category": "image_gen"},
            headers=auth_context["headers"],
        )
        detail_response = client.get(
            "/api/v1/history/images/image_gen/foreign-key-photo",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert list_response.status_code == 200
    assert list_response.json()["data"]["items"] == []
    assert detail_response.status_code == 404


@pytest.mark.parametrize(
    ("category", "params"),
    [
        ("image_gen", {}),
        ("ecom_white", {"kind": "ecom_cutout", "background": "white"}),
        ("ecom_model", {"kind": "ecom_model", "style_id": "street"}),
    ],
)
def test_image_history_cover_ignores_foreign_thumbnail_key(
    auth_context,
    auth_db,
    category: str,
    params: dict[str, object],
) -> None:
    tenant_id = auth_context["tenant_id"]
    task_id = f"thumbnail-scope-{category}"
    task = _photo_task(
        task_id=task_id,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        topic="封面租户校验",
        params=params,
    )
    safe_key = str(task.storage_key)
    foreign_key = "tenants/another-tenant/thumbnails/foreign.png"
    task.thumbnail_key = foreign_key
    db = auth_db()
    db.add(task)
    db.commit()
    db.close()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).get(
            "/api/v1/history/images",
            params={"category": category},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert safe_key in item["cover_url"]
    assert foreign_key not in response.text
    assert safe_key in storage.presigned_keys
    assert foreign_key not in storage.presigned_keys


def test_image_history_rejects_foreign_replicate_output_key(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    product_asset_id = "safe-cover-product"
    job = _replicate_job(
        job_id="foreign-output-job",
        tenant_id=tenant_id,
        status="completed",
        created_at=datetime.now(UTC),
        name="异常地址套图",
        product_asset_id=product_asset_id,
        output_count=1,
    )
    db = auth_db()
    db.add(
        Asset(
            id=product_asset_id,
            tenant_id=tenant_id,
            type="product_image",
            source="upload",
            storage_key=f"tenants/{tenant_id}/uploads/safe-cover.png",
            mime_type="image/png",
            status="ready",
        )
    )
    db.add(job)
    db.flush()
    db.add(
        _replicate_output(
            output_id="foreign-output",
            job_id=job.id,
            tenant_id=tenant_id,
            index=0,
            status="succeeded",
            storage_key="tenants/another-tenant/ecom-replicate/foreign.png",
        )
    )
    db.commit()
    db.close()

    storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(app)
    try:
        list_response = client.get(
            "/api/v1/history/images",
            params={"category": "ecom_detail"},
            headers=auth_context["headers"],
        )
        detail_response = client.get(
            "/api/v1/history/images/ecom_detail/foreign-output-job",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    list_item = list_response.json()["data"]["items"][0]
    assert list_item["item_count"] == 0
    assert list_item["cover_url"].endswith("/uploads/safe-cover.png?ttl=3600")
    assert detail_response.json()["data"]["items"] == []
    assert "another-tenant" not in list_response.text
    assert "another-tenant" not in detail_response.text
    assert all("another-tenant" not in key for key in storage.presigned_keys)
