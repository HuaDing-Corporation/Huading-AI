from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.dialects import postgresql

from app.db import models
from app.db.models import (
    AdminAuditLog,
    Asset,
    BrandVoice,
    EcomReplicateJob,
    EcomReplicateOutput,
    Plan,
    ProviderConfig,
    ReversePromptJob,
    Subscription,
    Tenant,
    UsageRecord,
    User,
    VideoTask,
)
from app.main import app


@pytest.fixture
def no_platform_tenants(monkeypatch) -> set[str]:
    from app.services import plan_access

    slugs: set[str] = set()
    monkeypatch.setattr(
        plan_access,
        "settings",
        SimpleNamespace(engine_platform_tenant_slugs=slugs),
        raising=False,
    )
    return slugs


@pytest.fixture
def platform_acme(monkeypatch) -> set[str]:
    from app.services import plan_access

    slugs = {"acme"}
    monkeypatch.setattr(
        plan_access,
        "settings",
        SimpleNamespace(engine_platform_tenant_slugs=slugs),
        raising=False,
    )
    return slugs


_CONSOLE_REQUESTS = (
    ("GET", "/api/v1/admin/console/tenants", None),
    ("GET", "/api/v1/admin/console/tenants/missing", None),
    (
        "POST",
        "/api/v1/admin/console/tenants/missing/credits",
        {"delta": 1, "reason": "gate probe"},
    ),
    (
        "PATCH",
        "/api/v1/admin/console/tenants/missing/plan",
        {"plan_code": "free"},
    ),
    (
        "PATCH",
        "/api/v1/admin/console/tenants/missing/status",
        {"active": True},
    ),
    ("GET", "/api/v1/admin/console/voice-slots", None),
    (
        "POST",
        "/api/v1/admin/console/tenants/missing/voice-slots",
        {"speaker_id": "S_gate_probe"},
    ),
    ("GET", "/api/v1/admin/console/usage", None),
    ("GET", "/api/v1/admin/console/usage/export", None),
    ("GET", "/api/v1/admin/console/tasks", None),
    ("POST", "/api/v1/admin/console/tasks/missing/retry", None),
    ("GET", "/api/v1/admin/console/audit-logs", None),
)


@pytest.mark.parametrize(("method", "path", "payload"), _CONSOLE_REQUESTS)
def test_every_admin_console_endpoint_rejects_non_platform_tenant_admin(
    auth_context,
    no_platform_tenants,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    assert no_platform_tenants == set()

    response = TestClient(app).request(
        method,
        path,
        headers=auth_context["headers"],
        json=payload,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PLATFORM_ADMIN_REQUIRED"


def test_admin_console_entitlement_is_derived_only_for_platform_tenant(
    auth_context,
    platform_acme,
) -> None:
    assert platform_acme == {"acme"}

    response = TestClient(app).get(
        "/api/v1/auth/me",
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assert "admin_console" in response.json()["data"]["permissions"]


def test_admin_audit_log_model_and_migration_are_declared() -> None:
    audit_model = getattr(models, "AdminAuditLog", None)
    assert audit_model is not None
    assert audit_model.__table__.name == "admin_audit_logs"
    assert {column.name for column in audit_model.__table__.columns} == {
        "id",
        "actor_user_id",
        "actor_tenant_id",
        "action",
        "target_tenant_id",
        "target_id",
        "before",
        "after",
        "reason",
        "created_at",
    }

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260713_0026_admin_audit_logs.py"
    )
    assert migration_path.exists()
    spec = importlib.util.spec_from_file_location("admin_console_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260713_0026"
    assert migration.down_revision == "20260712_0025"

    source = migration_path.read_text(encoding="utf-8")
    for action in (
        "credits_adjust",
        "plan_change",
        "status_change",
        "voice_slot_assign",
        "task_retry",
    ):
        assert action in source
    assert "ix_admin_audit_logs_created_at" in source
    assert "ix_admin_audit_logs_target_tenant_created_at" in source


def test_suspended_tenant_cannot_login_or_use_existing_token(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.id == auth_context["tenant_id"]))
        assert tenant is not None
        tenant.status = "suspended"
        db.commit()

    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": "acme",
            "email": "owner@example.com",
            "password": "secret-pass",
        },
    )
    assert login.status_code == 401
    assert login.json()["error"]["code"] == "INVALID_CREDENTIALS"

    me = client.get("/api/v1/auth/me", headers=auth_context["headers"])
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "TENANT_INACTIVE"


def _seed_console_read_fixture(auth_db, auth_context) -> dict[str, str]:
    now = datetime.now(UTC)
    with auth_db() as db:
        huading = Plan(
            code="huading",
            name="Huading Plan",
            price_cents=0,
            period="monthly",
            quota_credits=0,
            is_active=True,
        )
        tenant = Tenant(slug="beta-shop", name="测试商店")
        db.add_all([huading, tenant])
        db.flush()
        owner = User(
            tenant_id=tenant.id,
            email="owner@beta.example",
            password_hash="not-used",
            role="admin",
        )
        subscription = Subscription(
            tenant_id=tenant.id,
            plan_id=huading.id,
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            quota_credits_total=1000,
            quota_credits_used=120,
            quota_credits_reserved=30,
        )
        task = VideoTask(
            tenant_id=tenant.id,
            created_by_user_id=owner.id,
            status="failed",
            topic="fixture prompt",
            mode="photo",
            video_mode="photo",
            progress=40,
            error_code="VIDEO_GEN_FAILED",
            error_message="provider failed",
            params={"aspect_ratio": "1:1"},
            created_at=now - timedelta(hours=2),
            started_at=now - timedelta(hours=1, minutes=59),
            finished_at=now - timedelta(hours=1, minutes=58),
        )
        db.add_all([owner, subscription, task])
        db.flush()
        usage = UsageRecord(
            tenant_id=tenant.id,
            subscription_id=subscription.id,
            video_task_id=task.id,
            capability="image",
            provider="apimart",
            model="gpt-image-2",
            unit="image",
            quantity=Decimal("1"),
            credits=Decimal("10"),
            cost_cents=4,
            status="settled",
            created_at=now - timedelta(hours=1),
            settled_at=now - timedelta(hours=1),
        )
        config = ProviderConfig(
            tenant_id=tenant.id,
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={"speaker_ids": ["S_beta_slot"]},
            is_active=True,
        )
        voice = BrandVoice(
            tenant_id=tenant.id,
            name="Beta Voice",
            provider="doubao-voice-clone",
            speaker_id="S_beta_slot",
            status="ready",
            consent_confirmed=True,
        )
        audit = AdminAuditLog(
            actor_user_id=auth_context["user_id"],
            actor_tenant_id=auth_context["tenant_id"],
            action="plan_change",
            target_tenant_id=tenant.id,
            target_id=subscription.id,
            before={"plan_code": "basic"},
            after={"plan_code": "huading"},
            reason="fixture",
            created_at=now,
        )
        db.add_all([usage, config, voice, audit])
        db.commit()
        return {
            "tenant_id": tenant.id,
            "owner_id": owner.id,
            "subscription_id": subscription.id,
            "task_id": task.id,
            "usage_id": usage.id,
            "audit_id": audit.id,
        }


def _seed_non_video_task_families(auth_db, fixture: dict[str, str]) -> dict[str, str]:
    with auth_db() as db:
        source = Asset(
            tenant_id=fixture["tenant_id"],
            type="video",
            source="upload",
            storage_key=f"tenants/{fixture['tenant_id']}/uploads/reverse-source.mp4",
            mime_type="video/mp4",
            duration_ms=8_000,
            status="ready",
            metadata_={"purpose": "reverse_prompt"},
        )
        db.add(source)
        db.flush()
        reverse_job = ReversePromptJob(
            tenant_id=fixture["tenant_id"],
            created_by_user_id=fixture["owner_id"],
            source_kind="video",
            source_asset_id=source.id,
            source_storage_key=source.storage_key,
            target_format="seedance_2_0",
            status="failed",
            error_code="REVERSE_PROMPT_FAILED",
            error_message="fixture reverse failure",
        )
        image_reverse_job = ReversePromptJob(
            tenant_id=fixture["tenant_id"],
            created_by_user_id=fixture["owner_id"],
            source_kind="image",
            target_format="seedance_2_0",
            status="failed",
            error_code="REVERSE_PROMPT_FAILED",
            error_message="fixture image reverse failure",
        )
        replicate_job = EcomReplicateJob(
            tenant_id=fixture["tenant_id"],
            created_by_user_id=fixture["owner_id"],
            status="partial_failed",
            output_mode="main",
            requested_size="1024x1024",
            requested_aspect="1:1",
            output_count=2,
            total_credits=Decimal("30"),
            credit_rate=Decimal("15"),
            error_code="ECOM_REPLICATE_PARTIAL_FAILED",
            error_message="one or more outputs failed",
        )
        db.add_all([reverse_job, image_reverse_job, replicate_job])
        db.flush()
        failed_outputs = [
            EcomReplicateOutput(
                job_id=replicate_job.id,
                tenant_id=fixture["tenant_id"],
                index=index,
                theme=f"fixture-{index}",
                status="failed",
                requested_size="1024x1024",
                requested_aspect="1:1",
                error_code="ECOM_REPLICATE_RENDER_FAILED",
                error_message="fixture render failure",
            )
            for index in (0, 1)
        ]
        db.add_all(failed_outputs)
        db.commit()
        return {
            "reverse_job_id": reverse_job.id,
            "image_reverse_job_id": image_reverse_job.id,
            "replicate_job_id": replicate_job.id,
        }


def test_admin_console_read_endpoints_return_cross_tenant_operational_data(
    auth_context,
    auth_db,
    platform_acme,
) -> None:
    fixture = _seed_console_read_fixture(auth_db, auth_context)
    client = TestClient(app)
    headers = auth_context["headers"]

    tenants = client.get(
        "/api/v1/admin/console/tenants",
        params={"q": "owner@beta", "sort": "balance", "order": "asc"},
        headers=headers,
    )
    assert tenants.status_code == 200
    tenant_page = tenants.json()["data"]
    assert tenant_page["total"] == 1
    assert tenant_page["items"][0] == {
        "tenant_id": fixture["tenant_id"],
        "slug": "beta-shop",
        "name": "测试商店",
        "status": "active",
        "created_at": tenant_page["items"][0]["created_at"],
        "owner_email": "owner@beta.example",
        "plan_code": "huading",
        "subscription": {
            "id": tenant_page["items"][0]["subscription"]["id"],
            "total": 1000,
            "used": 120,
            "reserved": 30,
            "remaining": 850,
        },
        "task_count": 1,
    }

    detail = client.get(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["tenant"]["slug"] == "beta-shop"
    assert detail_data["recent_tasks"][0]["id"] == fixture["task_id"]
    assert detail_data["recent_usage"][0]["id"] == fixture["usage_id"]
    assert detail_data["voice_slots"][0]["speaker_id"] == "S_beta_slot"

    usage = client.get(
        "/api/v1/admin/console/usage",
        params={"tenant_id": fixture["tenant_id"], "provider": "apimart"},
        headers=headers,
    )
    assert usage.status_code == 200
    usage_item = usage.json()["data"]["items"][0]
    assert usage_item["id"] == fixture["usage_id"]
    assert usage_item["tenant_slug"] == "beta-shop"
    assert usage_item["credits"] == 10.0
    assert usage_item["cost_cents"] == 4

    exported = client.get(
        "/api/v1/admin/console/usage/export",
        params={"tenant_id": fixture["tenant_id"]},
        headers=headers,
    )
    assert exported.status_code == 200
    assert exported.content.startswith(b"\xef\xbb\xbf")
    assert "beta-shop" in exported.content.decode("utf-8-sig")

    tasks = client.get(
        "/api/v1/admin/console/tasks",
        params={"tenant_id": fixture["tenant_id"], "status": "failed"},
        headers=headers,
    )
    assert tasks.status_code == 200
    task_item = tasks.json()["data"]["items"][0]
    assert task_item["id"] == fixture["task_id"]
    assert task_item["error_code"] == "VIDEO_GEN_FAILED"

    slots = client.get("/api/v1/admin/console/voice-slots", headers=headers)
    assert slots.status_code == 200
    slot = next(
        item for item in slots.json()["data"]["items"] if item["speaker_id"] == "S_beta_slot"
    )
    assert slot["tenant_id"] == fixture["tenant_id"]
    assert slot["occupied"] is True
    assert slot["brand_voice_name"] == "Beta Voice"

    audits = client.get(
        "/api/v1/admin/console/audit-logs",
        params={"target_tenant_id": fixture["tenant_id"], "action": "plan_change"},
        headers=headers,
    )
    assert audits.status_code == 200
    assert audits.json()["data"]["items"][0]["id"] == fixture["audit_id"]


def test_admin_task_monitor_normalizes_all_task_families_and_filters_them(
    auth_context,
    auth_db,
    platform_acme,
) -> None:
    fixture = _seed_console_read_fixture(auth_db, auth_context)
    jobs = _seed_non_video_task_families(auth_db, fixture)
    with auth_db() as db:
        failed_video = db.get(VideoTask, fixture["task_id"])
        failed_video.mode = "generate"
        completed_video = VideoTask(
            tenant_id=fixture["tenant_id"],
            status="done",
            mode="static_template",
            video_mode="avatar_talk",
            topic="completed fixture",
            progress=100,
        )
        db.add(completed_video)
        db.commit()
        completed_video_id = completed_video.id
    client = TestClient(app)

    response = client.get(
        "/api/v1/admin/console/tasks",
        params={"tenant_id": fixture["tenant_id"], "page_size": 20},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()["data"]["items"]}
    assert {item["task_family"] for item in items.values()} == {
        "video",
        "reverse_prompt",
        "ecom_replicate",
    }
    assert items[fixture["task_id"]]["status"] == "failed"
    assert items[fixture["task_id"]]["retryable"] is True
    assert items[fixture["task_id"]]["mode"] == "generate"
    assert items[fixture["task_id"]]["video_mode"] == "photo"
    assert items[completed_video_id]["status"] == "succeeded"
    reverse_item = items[jobs["reverse_job_id"]]
    assert {key: reverse_item[key] for key in (
        "task_family",
        "mode",
        "label",
        "status",
        "progress",
        "retryable",
    )} == {
        "task_family": "reverse_prompt",
        "mode": "video",
        "label": "seedance_2_0",
        "status": "failed",
        "progress": 100,
        "retryable": True,
    }
    assert items[jobs["image_reverse_job_id"]]["retryable"] is False
    assert items[jobs["replicate_job_id"]]["status"] == "failed"
    assert items[jobs["replicate_job_id"]]["retryable"] is True

    filtered = client.get(
        "/api/v1/admin/console/tasks",
        params={
            "tenant_id": fixture["tenant_id"],
            "task_family": "reverse_prompt",
        },
        headers=auth_context["headers"],
    )

    assert filtered.status_code == 200
    assert filtered.json()["data"]["total"] == 2
    assert {
        item["task_family"] for item in filtered.json()["data"]["items"]
    } == {"reverse_prompt"}

    legacy_done_filter = client.get(
        "/api/v1/admin/console/tasks",
        params={"tenant_id": fixture["tenant_id"], "status": "done"},
        headers=auth_context["headers"],
    )
    assert legacy_done_filter.status_code == 200
    assert [
        item["id"] for item in legacy_done_filter.json()["data"]["items"]
    ] == [completed_video_id]


def test_admin_console_tenant_mutations_are_transactional_and_audited(
    auth_context,
    auth_db,
    platform_acme,
) -> None:
    fixture = _seed_console_read_fixture(auth_db, auth_context)
    client = TestClient(app)
    headers = auth_context["headers"]

    credits = client.post(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}/credits",
        json={"delta": 100, "reason": "customer top-up"},
        headers=headers,
    )
    assert credits.status_code == 200
    assert credits.json()["data"]["subscription"] == {
        "id": fixture["subscription_id"],
        "total": 1100,
        "used": 120,
        "reserved": 30,
        "remaining": 950,
    }

    rejected = client.post(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}/credits",
        json={"delta": -1000, "reason": "invalid deduction"},
        headers=headers,
    )
    assert rejected.status_code == 422
    assert "已用+预留" in rejected.json()["error"]["message"]

    plan = client.patch(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}/plan",
        json={"plan_code": "basic", "reason": "contract changed"},
        headers=headers,
    )
    assert plan.status_code == 200
    assert plan.json()["data"]["plan_code"] == "basic"
    assert plan.json()["data"]["subscription"]["total"] == 1100
    assert plan.json()["data"]["subscription"]["used"] == 120
    assert plan.json()["data"]["subscription"]["reserved"] == 30

    slot = client.post(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}/voice-slots",
        json={"speaker_id": "S_admin_api_slot", "reason": "purchased slot"},
        headers=headers,
    )
    assert slot.status_code == 200
    assert slot.json()["data"]["changed"] is True
    repeated_slot = client.post(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}/voice-slots",
        json={"speaker_id": "S_admin_api_slot", "reason": "idempotency probe"},
        headers=headers,
    )
    assert repeated_slot.status_code == 200
    assert repeated_slot.json()["data"]["changed"] is False

    status_response = client.patch(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}/status",
        json={"active": False, "reason": "account requested suspension"},
        headers=headers,
    )
    assert status_response.status_code == 200
    assert status_response.json()["data"]["status"] == "suspended"

    self_suspend = client.patch(
        f"/api/v1/admin/console/tenants/{auth_context['tenant_id']}/status",
        json={"active": False, "reason": "must be rejected"},
        headers=headers,
    )
    assert self_suspend.status_code == 422
    assert self_suspend.json()["error"]["code"] == "CANNOT_SUSPEND_PLATFORM_TENANT"

    with auth_db() as db:
        subscription = db.get(Subscription, fixture["subscription_id"])
        target = db.get(Tenant, fixture["tenant_id"])
        actions = list(
            db.scalars(
                select(AdminAuditLog.action)
                .where(AdminAuditLog.target_tenant_id == fixture["tenant_id"])
                .order_by(AdminAuditLog.created_at.asc())
            )
        )
        assert subscription.quota_credits_total == 1100
        assert db.get(Plan, subscription.plan_id).code == "basic"
        assert target.status == "suspended"
        assert actions.count("credits_adjust") == 1
        assert actions.count("plan_change") == 2  # fixture + API change
        assert actions.count("voice_slot_assign") == 2
        assert actions.count("status_change") == 1
        assert len(actions) == 6


def test_credit_adjustment_uses_for_update_and_independent_commits_do_not_lose_updates(
    auth_context,
    auth_db,
) -> None:
    from app.services.admin_console import adjust_credits

    fixture = _seed_console_read_fixture(auth_db, auth_context)
    statements: list[str] = []
    with auth_db() as db:
        @event.listens_for(db, "do_orm_execute")
        def capture_statement(orm_execute_state) -> None:
            if orm_execute_state.is_select:
                statements.append(
                    str(orm_execute_state.statement.compile(dialect=postgresql.dialect()))
                )

        actor = db.get(User, auth_context["user_id"])
        adjust_credits(
            db,
            actor=actor,
            tenant_id=fixture["tenant_id"],
            delta=100,
            reason="first independent transaction",
        )
        db.commit()

    with auth_db() as db:
        actor = db.get(User, auth_context["user_id"])
        adjust_credits(
            db,
            actor=actor,
            tenant_id=fixture["tenant_id"],
            delta=100,
            reason="second independent transaction",
        )
        db.commit()

    subscription_queries = [sql for sql in statements if "FROM subscriptions" in sql]
    assert any("FOR UPDATE" in sql for sql in subscription_queries)
    with auth_db() as db:
        subscription = db.get(Subscription, fixture["subscription_id"])
        audits = db.scalar(
            select(func.count(AdminAuditLog.id)).where(
                AdminAuditLog.target_tenant_id == fixture["tenant_id"],
                AdminAuditLog.action == "credits_adjust",
            )
        )
        assert subscription.quota_credits_total == 1200
        assert audits == 2


def test_failed_admin_mutation_rolls_back_value_and_audit(
    auth_context,
    auth_db,
    platform_acme,
    monkeypatch,
) -> None:
    from app.services import admin_console

    fixture = _seed_console_read_fixture(auth_db, auth_context)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(admin_console, "record_audit", fail_audit)
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}/credits",
        json={"delta": 100, "reason": "must roll back"},
        headers=auth_context["headers"],
    )
    assert response.status_code == 500

    with auth_db() as db:
        subscription = db.get(Subscription, fixture["subscription_id"])
        audits = db.scalar(
            select(func.count(AdminAuditLog.id)).where(
                AdminAuditLog.target_tenant_id == fixture["tenant_id"],
                AdminAuditLog.action == "credits_adjust",
            )
        )
        assert subscription.quota_credits_total == 1000
        assert audits == 0


def test_failed_task_retry_requeues_without_new_usage_record(
    auth_context,
    auth_db,
    platform_acme,
    monkeypatch,
) -> None:
    from app.api.v1.routes import admin_console as route

    fixture = _seed_console_read_fixture(auth_db, auth_context)
    enqueued: dict[str, object] = {}

    def fake_apply_async(*, args, task_id, queue):
        enqueued.update({"args": args, "task_id": task_id, "queue": queue})

    monkeypatch.setattr(route.generate_image_task, "apply_async", fake_apply_async)
    with auth_db() as db:
        usage_before = db.scalar(select(func.count(UsageRecord.id)))

    response = TestClient(app).post(
        f"/api/v1/admin/console/tasks/{fixture['task_id']}/retry",
        headers=auth_context["headers"],
    )
    assert response.status_code == 202
    assert response.json()["data"]["status"] == "queued"
    assert enqueued["task_id"] == fixture["task_id"]
    assert enqueued["queue"] == "image"
    assert enqueued["args"][0]["topic"] == "fixture prompt"

    with auth_db() as db:
        task = db.get(VideoTask, fixture["task_id"])
        usage_after = db.scalar(select(func.count(UsageRecord.id)))
        audit = db.scalar(
            select(AdminAuditLog).where(
                AdminAuditLog.action == "task_retry",
                AdminAuditLog.target_id == fixture["task_id"],
            )
        )
        assert task.status == "queued"
        assert task.progress == 0
        assert task.error_code is None
        assert task.error_message is None
        assert usage_after == usage_before
        assert audit is not None

    second = TestClient(app).post(
        f"/api/v1/admin/console/tasks/{fixture['task_id']}/retry",
        headers=auth_context["headers"],
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "TASK_NOT_RETRYABLE"


def test_failed_task_retry_dispatch_failure_is_compensated_and_can_retry(
    auth_context,
    auth_db,
    platform_acme,
    monkeypatch,
) -> None:
    from app.api.v1.routes import admin_console as route

    fixture = _seed_console_read_fixture(auth_db, auth_context)
    attempts = 0

    def flaky_apply_async(*, args, task_id, queue):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(route.generate_image_task, "apply_async", flaky_apply_async)
    client = TestClient(app, raise_server_exceptions=False)

    failed_dispatch = client.post(
        f"/api/v1/admin/console/tasks/{fixture['task_id']}/retry",
        headers=auth_context["headers"],
    )

    assert failed_dispatch.status_code == 503
    assert failed_dispatch.json()["error"]["code"] == "TASK_RETRY_ENQUEUE_FAILED"
    with auth_db() as db:
        task = db.get(VideoTask, fixture["task_id"])
        audits = list(
            db.scalars(
                select(AdminAuditLog)
                .where(
                    AdminAuditLog.action == "task_retry",
                    AdminAuditLog.target_id == fixture["task_id"],
                )
                .order_by(AdminAuditLog.created_at.asc(), AdminAuditLog.id.asc())
            )
        )
        assert task.status == "failed"
        assert task.error_code == "TASK_RETRY_ENQUEUE_FAILED"
        assert len(audits) == 2
        assert audits[-1].before == {"status": "queued", "progress": 0}
        assert audits[-1].after == {
            "status": "failed",
            "progress": 0,
            "error_code": "TASK_RETRY_ENQUEUE_FAILED",
        }

    retried = client.post(
        f"/api/v1/admin/console/tasks/{fixture['task_id']}/retry",
        headers=auth_context["headers"],
    )

    assert retried.status_code == 202
    assert retried.json()["data"]["status"] == "queued"
    assert attempts == 2


def test_admin_task_retry_reuses_reverse_video_and_replicate_output_paths(
    auth_context,
    auth_db,
    platform_acme,
    monkeypatch,
) -> None:
    from app.api.v1.routes import admin_console as route

    fixture = _seed_console_read_fixture(auth_db, auth_context)
    jobs = _seed_non_video_task_families(auth_db, fixture)
    reverse_calls: list[dict[str, object]] = []
    replicate_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        route.generate_reverse_prompt_video_task,
        "apply_async",
        lambda **kwargs: reverse_calls.append(kwargs),
    )
    monkeypatch.setattr(
        route.generate_ecom_replicate_task,
        "apply_async",
        lambda **kwargs: replicate_calls.append(kwargs),
    )
    with auth_db() as db:
        usage_before = db.scalar(select(func.count(UsageRecord.id)))
    client = TestClient(app)

    reverse_response = client.post(
        f"/api/v1/admin/console/tasks/{jobs['reverse_job_id']}/retry",
        params={"task_family": "reverse_prompt"},
        headers=auth_context["headers"],
    )
    replicate_response = client.post(
        f"/api/v1/admin/console/tasks/{jobs['replicate_job_id']}/retry",
        params={"task_family": "ecom_replicate"},
        headers=auth_context["headers"],
    )
    image_response = client.post(
        f"/api/v1/admin/console/tasks/{jobs['image_reverse_job_id']}/retry",
        params={"task_family": "reverse_prompt"},
        headers=auth_context["headers"],
    )

    assert reverse_response.status_code == 202
    assert reverse_response.json()["data"]["task_family"] == "reverse_prompt"
    assert reverse_calls == [
        {"args": [jobs["reverse_job_id"]], "task_id": jobs["reverse_job_id"], "queue": "image"}
    ]
    assert replicate_response.status_code == 202
    assert replicate_response.json()["data"]["task_family"] == "ecom_replicate"
    assert replicate_calls == [
        {
            "args": [jobs["replicate_job_id"]],
            "task_id": jobs["replicate_job_id"],
            "queue": "image",
        }
    ]
    assert image_response.status_code == 409
    assert image_response.json()["error"]["code"] == "TASK_NOT_RETRYABLE"

    with auth_db() as db:
        reverse_job = db.get(ReversePromptJob, jobs["reverse_job_id"])
        replicate_job = db.get(EcomReplicateJob, jobs["replicate_job_id"])
        outputs = list(
            db.scalars(
                select(EcomReplicateOutput)
                .where(EcomReplicateOutput.job_id == jobs["replicate_job_id"])
                .order_by(EcomReplicateOutput.index)
            )
        )
        usage_after = db.scalar(select(func.count(UsageRecord.id)))
        reverse_reservations = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.reverse_prompt_job_id == jobs["reverse_job_id"],
                    UsageRecord.status == "reserved",
                )
            )
        )
        retry_audits = list(
            db.scalars(
                select(AdminAuditLog).where(
                    AdminAuditLog.action == "task_retry",
                    AdminAuditLog.target_id.in_(
                        (jobs["reverse_job_id"], jobs["replicate_job_id"])
                    ),
                )
            )
        )
        assert reverse_job.status == "queued"
        assert len(reverse_reservations) == 1
        assert replicate_job.status == "generating"
        assert [output.status for output in outputs] == ["planned", "planned"]
        assert usage_after == usage_before + 1  # Reverse video reservation only.
        assert len(retry_audits) == 2


def test_non_video_admin_retry_dispatch_failures_are_compensated(
    auth_context,
    auth_db,
    platform_acme,
    monkeypatch,
) -> None:
    from app.api.v1.routes import admin_console as route

    fixture = _seed_console_read_fixture(auth_db, auth_context)
    jobs = _seed_non_video_task_families(auth_db, fixture)
    reverse_attempts = 0
    replicate_attempts = 0

    def flaky_reverse(**kwargs):
        nonlocal reverse_attempts
        reverse_attempts += 1
        if reverse_attempts == 1:
            raise RuntimeError("reverse broker unavailable")

    def flaky_replicate(**kwargs):
        nonlocal replicate_attempts
        replicate_attempts += 1
        if replicate_attempts == 1:
            raise RuntimeError("replicate broker unavailable")

    monkeypatch.setattr(route.generate_reverse_prompt_video_task, "apply_async", flaky_reverse)
    monkeypatch.setattr(route.generate_ecom_replicate_task, "apply_async", flaky_replicate)
    client = TestClient(app, raise_server_exceptions=False)

    reverse_failed = client.post(
        f"/api/v1/admin/console/tasks/{jobs['reverse_job_id']}/retry",
        params={"task_family": "reverse_prompt"},
        headers=auth_context["headers"],
    )
    replicate_failed = client.post(
        f"/api/v1/admin/console/tasks/{jobs['replicate_job_id']}/retry",
        params={"task_family": "ecom_replicate"},
        headers=auth_context["headers"],
    )

    assert reverse_failed.status_code == 503
    assert replicate_failed.status_code == 503
    with auth_db() as db:
        reverse_job = db.get(ReversePromptJob, jobs["reverse_job_id"])
        replicate_job = db.get(EcomReplicateJob, jobs["replicate_job_id"])
        outputs = list(
            db.scalars(
                select(EcomReplicateOutput).where(
                    EcomReplicateOutput.job_id == jobs["replicate_job_id"]
                )
            )
        )
        reserved = db.scalar(
            select(func.count(UsageRecord.id)).where(
                UsageRecord.reverse_prompt_job_id == jobs["reverse_job_id"],
                UsageRecord.status == "reserved",
            )
        )
        assert reverse_job.status == "failed"
        assert reverse_job.error_code == "TASK_RETRY_ENQUEUE_FAILED"
        assert reserved == 0
        assert replicate_job.status == "failed"
        assert {output.status for output in outputs} == {"failed"}

    reverse_retried = client.post(
        f"/api/v1/admin/console/tasks/{jobs['reverse_job_id']}/retry",
        params={"task_family": "reverse_prompt"},
        headers=auth_context["headers"],
    )
    replicate_retried = client.post(
        f"/api/v1/admin/console/tasks/{jobs['replicate_job_id']}/retry",
        params={"task_family": "ecom_replicate"},
        headers=auth_context["headers"],
    )
    assert reverse_retried.status_code == 202
    assert replicate_retried.status_code == 202
    assert reverse_attempts == 2
    assert replicate_attempts == 2


def test_admin_retry_runs_legacy_worker_without_charging_tenant_credits_again(
    auth_context,
    auth_db,
    platform_acme,
    monkeypatch,
    tmp_path,
) -> None:
    from app.api.v1.routes import admin_console as route
    from app.services import admin_console as admin_console_service
    from app.services.provider_costs import DeepSeekUsageCost
    from app.workers import video_tasks

    fixture = _seed_console_read_fixture(auth_db, auth_context)
    video_path = tmp_path / "legacy-worker-output.mp4"
    video_path.write_bytes(b"fixture-mp4")

    class FakeProgressStore:
        def update(self, *args, **kwargs) -> None:
            return None

    class FakeStorage:
        bucket = "test-bucket"

        def put_bytes(self, key, data, *, content_type) -> None:
            return None

    async def fake_generate(params, progress_cb):
        return {
            "video_path": str(video_path),
            "duration": 3.0,
            "file_size": video_path.stat().st_size,
        }

    with auth_db() as db:
        task = db.get(VideoTask, fixture["task_id"])
        task.video_mode = "static_template"
        task.mode = "static_template"
        subscription = db.get(Subscription, fixture["subscription_id"])
        used_before = subscription.quota_credits_used
        positive_usage_before = db.scalar(
            select(func.count(UsageRecord.id)).where(UsageRecord.credits > 0)
        )
        db.commit()

    monkeypatch.setattr(route.generate_video_task, "apply_async", lambda **kwargs: None)
    response = TestClient(app).post(
        f"/api/v1/admin/console/tasks/{fixture['task_id']}/retry",
        headers=auth_context["headers"],
    )
    assert response.status_code == 202

    with auth_db() as db:
        task = db.get(VideoTask, fixture["task_id"])
        payload = admin_console_service.task_retry_payload(task)

    monkeypatch.setattr(video_tasks, "SessionLocal", auth_db)
    monkeypatch.setattr(video_tasks, "build_progress_store", lambda url: FakeProgressStore())
    monkeypatch.setattr(video_tasks, "_generate_with_engine", fake_generate)
    monkeypatch.setattr(video_tasks, "create_object_storage", lambda settings: FakeStorage())
    monkeypatch.setattr(
        video_tasks,
        "_apply_synthetic_video_label",
        lambda **kwargs: kwargs["video_bytes"],
    )
    monkeypatch.setattr(video_tasks.provider_costs, "begin_deepseek_usage_capture", object)
    monkeypatch.setattr(
        video_tasks.provider_costs,
        "finish_deepseek_usage_capture",
        lambda token: DeepSeekUsageCost(
            provider="deepseek",
            model="deepseek-chat",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_cents=1,
        ),
    )

    result = video_tasks.generate_video_task.run(payload)

    assert result["status"] == "SUCCESS"
    with auth_db() as db:
        task = db.get(VideoTask, fixture["task_id"])
        subscription = db.get(Subscription, fixture["subscription_id"])
        positive_usage_after = db.scalar(
            select(func.count(UsageRecord.id)).where(UsageRecord.credits > 0)
        )
        cost_records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.video_task_id == fixture["task_id"],
                    UsageRecord.capability == "llm",
                )
            )
        )
        assert task.status == "done"
        assert subscription.quota_credits_used == used_before
        assert positive_usage_after == positive_usage_before
        assert len(cost_records) == 1
        assert cost_records[0].credits == 0
        assert cost_records[0].cost_cents == 1


def test_plan_change_updates_target_auth_entitlements_immediately(
    auth_context,
    auth_db,
    platform_acme,
) -> None:
    from app.core.security import create_access_token

    fixture = _seed_console_read_fixture(auth_db, auth_context)
    target_token = create_access_token(
        user_id=fixture["owner_id"],
        tenant_id=fixture["tenant_id"],
        role="admin",
    )
    target_headers = {"Authorization": f"Bearer {target_token}"}
    client = TestClient(app)

    before = client.get("/api/v1/auth/me", headers=target_headers)
    assert before.status_code == 200
    assert {"voice_clone_vip", "analytics_view"}.issubset(
        set(before.json()["data"]["permissions"])
    )

    changed = client.patch(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}/plan",
        json={"plan_code": "basic", "reason": "downgrade probe"},
        headers=auth_context["headers"],
    )
    assert changed.status_code == 200

    after = client.get("/api/v1/auth/me", headers=target_headers)
    assert after.status_code == 200
    assert {"voice_clone_vip", "analytics_view", "analytics_platform", "admin_console"}.isdisjoint(
        set(after.json()["data"]["permissions"])
    )


@pytest.mark.parametrize(
    ("video_mode", "worker_name", "queue"),
    (
        ("photo", "image", "image"),
        ("seedance_i2v", "seedance", "video"),
        ("video_gen", "video_gen", "video"),
        ("avatar_talk", "avatar", "avatar"),
        ("static_template", "default", "default"),
    ),
)
def test_task_retry_dispatches_to_existing_mode_queue(
    monkeypatch,
    video_mode: str,
    worker_name: str,
    queue: str,
) -> None:
    from app.api.v1.routes import admin_console as route

    calls: list[tuple[str, str, str]] = []

    def patch_worker(worker, name: str) -> None:
        monkeypatch.setattr(
            worker,
            "apply_async",
            lambda *, args, task_id, queue: calls.append((name, task_id, queue)),
        )

    patch_worker(route.generate_image_task, "image")
    patch_worker(route.generate_seedance_i2v_task, "seedance")
    patch_worker(route.generate_video_gen_task, "video_gen")
    patch_worker(route.generate_avatar_talk_task, "avatar")
    patch_worker(route.generate_video_task, "default")
    task = SimpleNamespace(
        id="retry-dispatch-id",
        tenant_id="retry-dispatch-tenant",
        video_mode=video_mode,
        topic="prompt",
        script=None,
        aspect_ratio="9:16",
        subtitle_enabled=True,
        speed=Decimal("1.0"),
        voice_id=None,
        brand_voice_id=None,
        params={},
    )

    route._enqueue_task_retry(task)

    assert calls == [(worker_name, "retry-dispatch-id", queue)]


def test_admin_console_validation_and_date_range_errors_are_friendly(
    auth_context,
    auth_db,
    platform_acme,
) -> None:
    fixture = _seed_console_read_fixture(auth_db, auth_context)
    client = TestClient(app)

    zero = client.post(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}/credits",
        json={"delta": 0, "reason": "probe"},
        headers=auth_context["headers"],
    )
    assert zero.status_code == 422
    assert zero.json()["error"]["message"] == "额度调整值不能为 0"

    blank_reason = client.post(
        f"/api/v1/admin/console/tenants/{fixture['tenant_id']}/credits",
        json={"delta": 1, "reason": "   "},
        headers=auth_context["headers"],
    )
    assert blank_reason.status_code == 422
    assert blank_reason.json()["error"]["message"] == "请填写额度调整理由"

    invalid_dates = client.get(
        "/api/v1/admin/console/usage",
        params={"from": "2026-07-13", "to": "2026-07-01"},
        headers=auth_context["headers"],
    )
    assert invalid_dates.status_code == 422
    assert invalid_dates.json()["error"]["code"] == "INVALID_DATE_RANGE"


def test_usage_export_rejects_more_than_fifty_thousand_rows() -> None:
    from app.core.exceptions import AppError
    from app.services.admin_console import export_usage_rows

    class CountingSession:
        @staticmethod
        def scalar(_statement):
            return 50_001

    with pytest.raises(AppError) as exc_info:
        export_usage_rows(
            CountingSession(),
            tenant_id=None,
            from_=None,
            to=None,
            capability=None,
            provider=None,
            status=None,
        )
    assert exc_info.value.code == "USAGE_EXPORT_TOO_LARGE"
    assert exc_info.value.status_code == 422


def test_admin_audit_migration_upgrades_and_downgrades_on_sqlite() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260713_0026_admin_audit_logs.py"
    )
    spec = importlib.util.spec_from_file_location("admin_console_migration_runtime", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        names = set(
            connection.scalars(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE name LIKE '%admin_audit_logs%'"
                )
            )
        )
        assert {
            "admin_audit_logs",
            "ix_admin_audit_logs_created_at",
            "ix_admin_audit_logs_target_tenant_created_at",
        }.issubset(names)
        migration.downgrade()
        assert connection.scalar(
            text(
                "SELECT count(*) FROM sqlite_master "
                "WHERE type='table' AND name='admin_audit_logs'"
            )
        ) == 0
