import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect, select, text

from app.db.models import (
    Asset,
    ReversePromptJob,
    Subscription,
    TaskAsset,
    Tenant,
    UsageRecord,
    VideoTask,
)
from app.main import app


class _HistoryStorage:
    bucket = "reverse-history-test"

    def __init__(self) -> None:
        self.presigned_keys: list[str] = []
        self.deleted_keys: list[str] = []

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        self.presigned_keys.append(key)
        return f"https://storage.test/{key}?ttl={expires_in}"

    def delete_object(self, key: str) -> None:
        self.deleted_keys.append(key)


def _reverse_job(
    *,
    job_id: str,
    tenant_id: str,
    source_kind: str,
    status: str,
    created_at: datetime,
    source_storage_key: str,
    result_json: dict[str, object] | None = None,
) -> ReversePromptJob:
    return ReversePromptJob(
        id=job_id,
        tenant_id=tenant_id,
        source_kind=source_kind,
        source_storage_key=source_storage_key,
        target_format="seedance_2_0",
        status=status,
        result_json=result_json,
        created_at=created_at,
        updated_at=created_at,
    )


def _mapped_snapshot(value: object) -> dict[str, object]:
    return {
        attribute.key: getattr(value, attribute.key)
        for attribute in inspect(value).mapper.column_attrs
    }


def _usage_snapshot(record: UsageRecord) -> dict[str, object]:
    snapshot = _mapped_snapshot(record)
    for key in ("quantity", "credits", "provider_cost_usd"):
        if snapshot[key] is not None:
            snapshot[key] = str(snapshot[key])
    for key in ("created_at", "settled_at"):
        value = snapshot[key]
        if isinstance(value, datetime):
            snapshot[key] = value.replace(tzinfo=UTC).isoformat()
    return snapshot


def test_reverse_prompt_history_lists_all_statuses_with_tenant_safe_sources(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    tenant_id = auth_context["tenant_id"]
    other_tenant_id = "reverse-history-other"
    now = datetime.now(UTC)
    jobs = [
        _reverse_job(
            job_id="history-saved",
            tenant_id=tenant_id,
            source_kind="image",
            status="saved",
            created_at=now,
            source_storage_key=f"tenants/{tenant_id}/uploads/saved.png",
            result_json={"prompt_zh": "  高级香水静物广告  "},
        ),
        _reverse_job(
            job_id="history-running-video",
            tenant_id=tenant_id,
            source_kind="video",
            status="running",
            created_at=now - timedelta(minutes=1),
            source_storage_key=f"tenants/{tenant_id}/uploads/running.mp4",
        ),
        _reverse_job(
            job_id="history-failed",
            tenant_id=tenant_id,
            source_kind="image",
            status="failed",
            created_at=now - timedelta(minutes=2),
            source_storage_key="tenants/another-tenant/uploads/foreign.png",
        ),
        _reverse_job(
            job_id="history-queued-video",
            tenant_id=tenant_id,
            source_kind="video",
            status="queued",
            created_at=now - timedelta(minutes=3),
            source_storage_key=f"tenants/{tenant_id}/uploads/queued.mp4",
        ),
        _reverse_job(
            job_id="history-succeeded",
            tenant_id=tenant_id,
            source_kind="image",
            status="succeeded",
            created_at=now - timedelta(minutes=4),
            source_storage_key=f"tenants/{tenant_id}/uploads/succeeded.png",
            result_json={"prompt_zh": "", "subject": "青瓷茶具"},
        ),
    ]
    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug="reverse-history-other", name="Other"))
        db.flush()
        db.add_all(jobs)
        db.add(
            _reverse_job(
                job_id="history-other-tenant",
                tenant_id=other_tenant_id,
                source_kind="image",
                status="succeeded",
                created_at=now + timedelta(minutes=1),
                source_storage_key=f"tenants/{other_tenant_id}/uploads/private.png",
                result_json={"prompt_zh": "其他租户秘密提示词"},
            )
        )
        db.commit()

    storage = _HistoryStorage()
    monkeypatch.setattr(
        "app.api.v1.routes.reverse_prompt.get_object_storage",
        lambda: storage,
    )
    client = TestClient(app)
    page_one = client.get(
        "/api/v1/reverse-prompt/jobs",
        params={"page": 1, "page_size": 2},
        headers=auth_context["headers"],
    )
    page_two = client.get(
        "/api/v1/reverse-prompt/jobs",
        params={"page": 2, "page_size": 2},
        headers=auth_context["headers"],
    )
    page_three = client.get(
        "/api/v1/reverse-prompt/jobs",
        params={"page": 3, "page_size": 2},
        headers=auth_context["headers"],
    )
    image_only = client.get(
        "/api/v1/reverse-prompt/jobs",
        params={"source_kind": "image", "page_size": 100},
        headers=auth_context["headers"],
    )
    video_only = client.get(
        "/api/v1/reverse-prompt/jobs",
        params={"source_kind": "video"},
        headers=auth_context["headers"],
    )

    assert page_one.status_code == 200
    assert page_two.status_code == 200
    assert page_three.status_code == 200
    assert image_only.status_code == 200
    assert video_only.status_code == 200

    page_one_data = page_one.json()["data"]
    assert page_one_data["total"] == 5
    assert page_one_data["page"] == 1
    assert page_one_data["page_size"] == 2
    assert [item["id"] for item in page_one_data["items"]] == [
        "history-saved",
        "history-running-video",
    ]
    assert page_one_data["items"][0]["summary"] == "高级香水静物广告"
    assert page_one_data["items"][0]["source_thumbnail_url"].endswith(
        f"tenants/{tenant_id}/uploads/saved.png?ttl=3600"
    )
    assert page_one_data["items"][1]["source_thumbnail_url"] is None
    assert [item["id"] for item in page_two.json()["data"]["items"]] == [
        "history-failed",
        "history-queued-video",
    ]
    assert page_two.json()["data"]["items"][0]["source_thumbnail_url"] is None
    assert [item["id"] for item in page_three.json()["data"]["items"]] == ["history-succeeded"]
    assert page_three.json()["data"]["items"][0]["summary"] == "青瓷茶具"
    assert [item["status"] for item in image_only.json()["data"]["items"]] == [
        "saved",
        "failed",
        "succeeded",
    ]
    assert [item["status"] for item in video_only.json()["data"]["items"]] == [
        "running",
        "queued",
    ]
    assert "history-other-tenant" not in page_one.text + page_two.text + page_three.text
    assert all("another-tenant" not in key for key in storage.presigned_keys)


def test_reverse_prompt_delete_soft_hides_job_everywhere_and_keeps_source(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    tenant_id = auth_context["tenant_id"]
    other_tenant_id = "reverse-delete-other"
    source_key = f"tenants/{tenant_id}/uploads/delete-source.png"
    source_asset_id = "reverse-delete-source"
    source_asset = Asset(
        id=source_asset_id,
        tenant_id=tenant_id,
        type="avatar_image",
        source="upload",
        storage_key=source_key,
        mime_type="image/png",
        status="ready",
    )
    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug="reverse-delete-other", name="Other"))
        db.add(source_asset)
        db.flush()
        db.add_all(
            [
                ReversePromptJob(
                    id="reverse-delete-job",
                    tenant_id=tenant_id,
                    source_kind="image",
                    source_asset_id=source_asset.id,
                    source_storage_key=source_key,
                    target_format="seedance_2_0",
                    status="failed",
                ),
                _reverse_job(
                    job_id="reverse-delete-foreign",
                    tenant_id=other_tenant_id,
                    source_kind="image",
                    status="failed",
                    created_at=datetime.now(UTC),
                    source_storage_key=(f"tenants/{other_tenant_id}/uploads/delete-foreign.png"),
                ),
            ]
        )
        db.commit()

    storage = _HistoryStorage()
    monkeypatch.setattr(
        "app.api.v1.routes.reverse_prompt.get_object_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        "app.services.reverse_prompt.resolve",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("deleted job reached provider")
        ),
    )
    client = TestClient(app)
    deleted = client.delete(
        "/api/v1/reverse-prompt/jobs/reverse-delete-job",
        headers=auth_context["headers"],
    )

    assert deleted.status_code == 200
    assert deleted.json()["data"]["id"] == "reverse-delete-job"
    assert datetime.fromisoformat(deleted.json()["data"]["deleted_at"])

    list_response = client.get(
        "/api/v1/reverse-prompt/jobs",
        headers=auth_context["headers"],
    )
    detail_response = client.get(
        "/api/v1/reverse-prompt/jobs/reverse-delete-job",
        headers=auth_context["headers"],
    )
    regenerate_response = client.post(
        "/api/v1/reverse-prompt/jobs/reverse-delete-job/regenerate",
        headers=auth_context["headers"],
    )
    save_response = client.post(
        "/api/v1/reverse-prompt/jobs/reverse-delete-job/save",
        headers=auth_context["headers"],
    )
    repeated_delete = client.delete(
        "/api/v1/reverse-prompt/jobs/reverse-delete-job",
        headers=auth_context["headers"],
    )
    cross_tenant_delete = client.delete(
        "/api/v1/reverse-prompt/jobs/reverse-delete-foreign",
        headers=auth_context["headers"],
    )
    cross_tenant_detail = client.get(
        "/api/v1/reverse-prompt/jobs/reverse-delete-foreign",
        headers=auth_context["headers"],
    )

    assert list_response.status_code == 200
    assert list_response.json()["data"]["items"] == []
    for response in (
        detail_response,
        regenerate_response,
        save_response,
        repeated_delete,
        cross_tenant_delete,
        cross_tenant_detail,
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "REVERSE_PROMPT_JOB_NOT_FOUND"

    with auth_db() as db:
        job = db.get(ReversePromptJob, "reverse-delete-job")
        source = db.get(Asset, source_asset_id)
        assert job is not None
        assert job.deleted_at is not None
        assert source is not None
        assert source.deleted_at is None
        assert source.storage_key == source_key
    assert storage.deleted_keys == []


def test_reverse_prompt_clear_history_requires_explicit_valid_scope(
    auth_context,
) -> None:
    client = TestClient(app)
    missing = client.delete(
        "/api/v1/reverse-prompt/jobs",
        headers=auth_context["headers"],
    )
    invalid = client.delete(
        "/api/v1/reverse-prompt/jobs",
        params={"scope": "audio"},
        headers=auth_context["headers"],
    )

    assert missing.status_code == 422
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    ("scope", "deleted_ids", "remaining_ids"),
    [
        ("all", {"reverse-clear-scope-image", "reverse-clear-scope-video"}, set()),
        ("image", {"reverse-clear-scope-image"}, {"reverse-clear-scope-video"}),
        ("video", {"reverse-clear-scope-video"}, {"reverse-clear-scope-image"}),
    ],
)
def test_reverse_prompt_clear_history_respects_explicit_scope(
    auth_context,
    auth_db,
    scope: str,
    deleted_ids: set[str],
    remaining_ids: set[str],
) -> None:
    tenant_id = auth_context["tenant_id"]
    now = datetime.now(UTC)
    with auth_db() as db:
        db.add_all(
            [
                _reverse_job(
                    job_id="reverse-clear-scope-image",
                    tenant_id=tenant_id,
                    source_kind="image",
                    status="succeeded",
                    created_at=now,
                    source_storage_key=(
                        f"tenants/{tenant_id}/uploads/reverse-clear-scope.png"
                    ),
                ),
                _reverse_job(
                    job_id="reverse-clear-scope-video",
                    tenant_id=tenant_id,
                    source_kind="video",
                    status="failed",
                    created_at=now - timedelta(seconds=1),
                    source_storage_key=(
                        f"tenants/{tenant_id}/uploads/reverse-clear-scope.mp4"
                    ),
                ),
            ]
        )
        db.commit()

    client = TestClient(app)
    response = client.delete(
        "/api/v1/reverse-prompt/jobs",
        params={"scope": scope},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"deleted_count": len(deleted_ids)}
    with auth_db() as db:
        assert {
            job_id
            for job_id in deleted_ids
            if db.get(ReversePromptJob, job_id).deleted_at is not None
        } == deleted_ids
        assert {
            job_id
            for job_id in remaining_ids
            if db.get(ReversePromptJob, job_id).deleted_at is None
        } == remaining_ids


def test_reverse_prompt_clear_history_keeps_linked_media_and_billing_unchanged(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    tenant_id = auth_context["tenant_id"]
    other_tenant_id = "reverse-clear-other"
    now = datetime.now(UTC)
    usage_created_at = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
    usage_settled_at = usage_created_at + timedelta(seconds=8)
    source_asset_id = "reverse-clear-source"
    source_key = f"tenants/{tenant_id}/uploads/clear-source.png"
    source_task_id = "reverse-clear-source-task"
    source_video_key = f"tenants/{tenant_id}/videos/clear-source.mp4"
    task_asset_id = "reverse-clear-task-asset"
    image_job_id = "reverse-clear-image"
    video_job_id = "reverse-clear-video"
    source_asset = Asset(
        id=source_asset_id,
        tenant_id=tenant_id,
        type="avatar_image",
        source="upload",
        storage_key=source_key,
        mime_type="image/png",
        size_bytes=2048,
        width=1024,
        height=1024,
        status="ready",
        metadata_={"purpose": "reverse-clear-history-bearing-test"},
    )
    source_task = VideoTask(
        id=source_task_id,
        tenant_id=tenant_id,
        status="done",
        mode="photo",
        video_mode="photo",
        storage_bucket="huading-videos",
        storage_key=source_video_key,
        content_type="video/mp4",
        size_bytes=4096,
        duration_sec=8.5,
    )
    task_asset = TaskAsset(
        id=task_asset_id,
        video_task_id=source_task.id,
        asset_id=source_asset.id,
        role="input_reference_image",
    )
    image_job = _reverse_job(
        job_id=image_job_id,
        tenant_id=tenant_id,
        source_kind="image",
        status="succeeded",
        created_at=now,
        source_storage_key=source_key,
    )
    image_job.source_asset_id = source_asset_id
    video_job = _reverse_job(
        job_id=video_job_id,
        tenant_id=tenant_id,
        source_kind="video",
        status="failed",
        created_at=now - timedelta(minutes=1),
        source_storage_key=source_video_key,
    )
    video_job.source_video_task_id = source_task_id
    already_deleted_at = now - timedelta(days=1)
    already_deleted_job = _reverse_job(
        job_id="reverse-clear-already-deleted",
        tenant_id=tenant_id,
        source_kind="image",
        status="saved",
        created_at=now - timedelta(minutes=2),
        source_storage_key=source_key,
    )
    already_deleted_job.deleted_at = already_deleted_at

    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug=other_tenant_id, name="Other"))
        db.add_all([source_asset, source_task])
        db.flush()
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        assert subscription is not None
        subscription_id = subscription.id
        subscription.quota_credits_used = 321
        subscription.quota_credits_reserved = 45
        db.add(task_asset)
        db.add_all(
            [
                image_job,
                video_job,
                already_deleted_job,
                _reverse_job(
                    job_id="reverse-clear-foreign",
                    tenant_id=other_tenant_id,
                    source_kind="image",
                    status="succeeded",
                    created_at=now,
                    source_storage_key=(
                        f"tenants/{other_tenant_id}/uploads/clear-foreign.png"
                    ),
                ),
            ]
        )
        db.add_all(
            [
                UsageRecord(
                    id="reverse-clear-usage-1-settled",
                    tenant_id=tenant_id,
                    subscription_id=subscription_id,
                    reverse_prompt_job_id=image_job_id,
                    capability="reverse_prompt",
                    provider="apimart",
                    model="gemini-3.1-pro-preview",
                    unit="call",
                    quantity=Decimal("1.000"),
                    credits=Decimal("30.000000"),
                    cost_cents=14,
                    provider_cost_usd=Decimal("0.01930240"),
                    currency="CNY",
                    status="settled",
                    created_at=usage_created_at,
                    settled_at=usage_settled_at,
                ),
                UsageRecord(
                    id="reverse-clear-usage-2-reserved",
                    tenant_id=tenant_id,
                    subscription_id=subscription_id,
                    reverse_prompt_job_id=video_job_id,
                    capability="reverse_prompt_video",
                    provider="apimart",
                    model="gemini-3.1-pro-preview",
                    unit="call",
                    quantity=Decimal("1.000"),
                    credits=Decimal("180.000000"),
                    cost_cents=0,
                    provider_cost_usd=None,
                    currency="CNY",
                    status="reserved",
                    created_at=usage_created_at + timedelta(seconds=1),
                    settled_at=None,
                ),
            ]
        )
        db.commit()

    expected_usage = [
        {
            "id": "reverse-clear-usage-1-settled",
            "tenant_id": tenant_id,
            "subscription_id": subscription_id,
            "video_task_id": None,
                "reverse_prompt_job_id": image_job_id,
                "chat_message_id": None,
                "billing_operation_id": None,
                "billing_item_index": None,
                "billing_pricing_line_index": None,
                "capability": "reverse_prompt",
            "provider": "apimart",
            "model": "gemini-3.1-pro-preview",
            "unit": "call",
            "quantity": "1.000",
            "credits": "30.000000",
            "cost_cents": 14,
                "provider_cost_usd": "0.01930240",
                "provider_usage": None,
                "currency": "CNY",
            "status": "settled",
            "created_at": usage_created_at.isoformat(),
            "settled_at": usage_settled_at.isoformat(),
        },
        {
            "id": "reverse-clear-usage-2-reserved",
            "tenant_id": tenant_id,
            "subscription_id": subscription_id,
            "video_task_id": None,
                "reverse_prompt_job_id": video_job_id,
                "chat_message_id": None,
                "billing_operation_id": None,
                "billing_item_index": None,
                "billing_pricing_line_index": None,
                "capability": "reverse_prompt_video",
            "provider": "apimart",
            "model": "gemini-3.1-pro-preview",
            "unit": "call",
            "quantity": "1.000",
            "credits": "180.000000",
            "cost_cents": 0,
                "provider_cost_usd": None,
                "provider_usage": None,
                "currency": "CNY",
            "status": "reserved",
            "created_at": (usage_created_at + timedelta(seconds=1)).isoformat(),
            "settled_at": None,
        },
    ]
    with auth_db() as db:
        asset_before = _mapped_snapshot(db.get(Asset, source_asset_id))
        task_before = _mapped_snapshot(db.get(VideoTask, source_task_id))
        task_asset_before = _mapped_snapshot(db.get(TaskAsset, task_asset_id))
        subscription_before = _mapped_snapshot(db.get(Subscription, subscription_id))
        usage_before = [
            _usage_snapshot(record)
            for record in db.scalars(
                select(UsageRecord)
                .where(UsageRecord.tenant_id == tenant_id)
                .order_by(UsageRecord.id)
            )
        ]
    assert usage_before == expected_usage
    assert subscription_before["quota_credits_used"] == 321
    assert subscription_before["quota_credits_reserved"] == 45

    storage = _HistoryStorage()
    monkeypatch.setattr(
        "app.api.v1.routes.reverse_prompt.get_object_storage",
        lambda: storage,
    )
    client = TestClient(app)
    response = client.delete(
        "/api/v1/reverse-prompt/jobs",
        params={"scope": "all"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"deleted_count": 2}
    listed = client.get(
        "/api/v1/reverse-prompt/jobs",
        headers=auth_context["headers"],
    )
    assert listed.status_code == 200
    assert listed.json()["data"]["items"] == []
    assert listed.json()["data"]["total"] == 0

    with auth_db() as db:
        assert db.get(ReversePromptJob, "reverse-clear-image").deleted_at is not None
        assert db.get(ReversePromptJob, "reverse-clear-video").deleted_at is not None
        persisted_deleted_at = db.get(
            ReversePromptJob,
            "reverse-clear-already-deleted",
        ).deleted_at
        assert persisted_deleted_at is not None
        assert persisted_deleted_at.replace(tzinfo=UTC) == already_deleted_at
        assert db.get(ReversePromptJob, "reverse-clear-foreign").deleted_at is None
        assert _mapped_snapshot(db.get(Asset, source_asset_id)) == asset_before
        assert _mapped_snapshot(db.get(VideoTask, source_task_id)) == task_before
        assert _mapped_snapshot(db.get(TaskAsset, task_asset_id)) == task_asset_before
        subscription_after = _mapped_snapshot(db.get(Subscription, subscription_id))
        usage_after = [
            _usage_snapshot(record)
            for record in db.scalars(
                select(UsageRecord)
                .where(UsageRecord.tenant_id == tenant_id)
                .order_by(UsageRecord.id)
            )
        ]
    assert subscription_after == subscription_before
    assert subscription_after["quota_credits_used"] == 321
    assert subscription_after["quota_credits_reserved"] == 45
    assert usage_after == expected_usage
    assert usage_after == usage_before
    assert storage.presigned_keys == []
    assert storage.deleted_keys == []


def test_reverse_prompt_clear_history_rolls_back_every_row_when_third_update_fails(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    now = datetime.now(UTC)
    with auth_db() as db:
        db.add_all(
            [
                _reverse_job(
                    job_id=f"reverse-clear-atomic-{index}",
                    tenant_id=tenant_id,
                    source_kind="image",
                    status="succeeded",
                    created_at=now + timedelta(seconds=index),
                    source_storage_key=(
                        f"tenants/{tenant_id}/uploads/reverse-clear-atomic-{index}.png"
                    ),
                )
                for index in range(1, 4)
            ]
        )
        db.commit()
        db.execute(
            text(
                """
                CREATE TRIGGER reverse_prompt_fail_third_clear
                BEFORE UPDATE OF deleted_at ON reverse_prompt_jobs
                WHEN OLD.id = 'reverse-clear-atomic-3'
                BEGIN
                    SELECT RAISE(ABORT, 'injected third-row update failure');
                END
                """
            )
        )
        db.commit()

    client = TestClient(app, raise_server_exceptions=False)
    response = client.delete(
        "/api/v1/reverse-prompt/jobs",
        params={"scope": "all"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 500
    with auth_db() as db:
        assert all(
            db.get(ReversePromptJob, f"reverse-clear-atomic-{index}").deleted_at is None
            for index in range(1, 4)
        )


def test_reverse_prompt_history_rejects_invalid_filters(auth_context) -> None:
    client = TestClient(app)
    for params in (
        {"source_kind": "audio"},
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
    ):
        response = client.get(
            "/api/v1/reverse-prompt/jobs",
            params=params,
            headers=auth_context["headers"],
        )
        assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("get", ""),
        ("post", "/regenerate"),
        ("post", "/save"),
        ("delete", ""),
    ],
    ids=["detail", "regenerate", "save", "repeated-delete"],
)
def test_soft_deleted_reverse_prompt_job_is_not_found_by_each_endpoint(
    auth_context,
    auth_db,
    monkeypatch,
    method: str,
    suffix: str,
) -> None:
    tenant_id = auth_context["tenant_id"]
    source_asset_id = f"deleted-source-{method}-{suffix.replace('/', '') or 'detail'}"
    job_id = f"deleted-job-{method}-{suffix.replace('/', '') or 'detail'}"
    source_key = f"tenants/{tenant_id}/uploads/{source_asset_id}.png"
    with auth_db() as db:
        db.add(
            Asset(
                id=source_asset_id,
                tenant_id=tenant_id,
                type="avatar_image",
                source="upload",
                storage_key=source_key,
                mime_type="image/png",
                status="ready",
            )
        )
        db.flush()
        db.add(
            ReversePromptJob(
                id=job_id,
                tenant_id=tenant_id,
                source_kind="image",
                source_asset_id=source_asset_id,
                source_storage_key=source_key,
                target_format="seedance_2_0",
                status="failed",
                deleted_at=datetime.now(UTC),
            )
        )
        db.commit()

    monkeypatch.setattr(
        "app.api.v1.routes.reverse_prompt.get_object_storage",
        lambda: _HistoryStorage(),
    )
    monkeypatch.setattr(
        "app.services.reverse_prompt.resolve",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("deleted job reached provider")
        ),
    )
    response = getattr(TestClient(app), method)(
        f"/api/v1/reverse-prompt/jobs/{job_id}{suffix}",
        headers=auth_context["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REVERSE_PROMPT_JOB_NOT_FOUND"


def test_reverse_prompt_soft_delete_migration_upgrades_and_downgrades() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260716_0027_reverse_prompt_job_soft_delete.py"
    )
    spec = importlib.util.spec_from_file_location(
        "reverse_prompt_soft_delete_migration",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260716_0027"
    assert migration.down_revision == "20260713_0026"

    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    Table("reverse_prompt_jobs", metadata, Column("id", String(36), primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        columns_after_upgrade = {
            column["name"] for column in inspect(connection).get_columns("reverse_prompt_jobs")
        }
        assert columns_after_upgrade == {"id", "deleted_at"}

        migration.downgrade()
        columns_after_downgrade = {
            column["name"] for column in inspect(connection).get_columns("reverse_prompt_jobs")
        }
        assert columns_after_downgrade == {"id"}
