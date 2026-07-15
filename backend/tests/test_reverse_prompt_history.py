import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect

from app.db.models import Asset, ReversePromptJob, Tenant
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
