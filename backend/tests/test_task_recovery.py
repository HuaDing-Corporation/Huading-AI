from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    Asset,
    BatchJob,
    BillingOperation,
    ChatConversation,
    ChatMessage,
    EcomReplicateJob,
    EcomReplicateOutput,
    ReasoningLedgerEntry,
    ReasoningWallet,
    ReversePromptJob,
    Subscription,
    TaskAsset,
    Tenant,
    UsageRecord,
    VideoTask,
)
from app.main import app
from app.services import aibrain


def _pricing_snapshot(*, operation: str, credits: str = "1.0000") -> dict[str, object]:
    capability, unit = (
        ("video", "second") if operation == "video_create" else ("image", "image")
    )
    return {
        "operation": operation,
        "pricing_shape": "simple",
        "pricing_lines": [
            {
                "operation": operation,
                "capability": capability,
                "unit": unit,
                "quantity": "1",
                "unit_credits": credits,
                "subtotal_credits": credits,
                "rate_scope": "tenant_overridable",
                "rate_source": "code_default",
                "rate_id": None,
                "effective_at": None,
                "policy_key": operation,
                "policy_version": 1,
                "label": operation,
            }
        ],
        "disclosures": [],
        "subtotal_credits": credits,
        "payable_credits": 1,
        "rounding": "ROUND_CEILING",
    }


def _manual_order_snapshot(operation: str) -> dict[str, object]:
    return {
        "operation": operation,
        "pricing_shape": "simple",
        "pricing_lines": [
            {
                "operation": operation,
                "capability": "voice_clone",
                "unit": "call",
                "quantity": "1",
                "unit_credits": "30000.0000",
                "subtotal_credits": "30000.0000",
                "rate_scope": "platform_fixed",
                "rate_source": "code_default",
                "rate_id": None,
                "effective_at": None,
                "policy_key": operation,
                "policy_version": 1,
                "label": operation,
            }
        ],
        "disclosures": [],
        "subtotal_credits": "30000.0000",
        "payable_credits": 30000,
        "rounding": "ROUND_CEILING",
    }


def _financial_snapshot(operation: BillingOperation) -> tuple[object, ...]:
    return (
        operation.status,
        operation.completion_kind,
        operation.settled_credits,
        operation.released_credits,
        operation.completed_at,
    )


def test_recovery_never_releases_waiting_manual_order(db_session) -> None:
    """Manual Doubao fulfillment is human-owned, never a stale worker failure."""
    from app.services.task_recovery import recover_stale_billing_operations

    created_at = datetime(2025, 7, 1, tzinfo=UTC)
    operation = BillingOperation(
        id="manual-order-stale",
        tenant_id="tenant-a",
        user_id="user-a",
        operation="doubao_brand_voice_order_create",
        idempotency_key="manual-order-stale",
        request_hash="a" * 64,
        quote_hash="b" * 64,
        pricing_snapshot=_manual_order_snapshot("doubao_brand_voice_order_create"),
        requested_credits=Decimal("30000"),
        settled_credits=Decimal("0"),
        released_credits=Decimal("0"),
        status="in_progress",
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(operation)
    db_session.commit()

    before = _financial_snapshot(operation)
    summary = recover_stale_billing_operations(
        db_session,
        now=created_at + timedelta(days=400),
    )

    assert operation.id in summary.exempt_manual_order_ids
    assert _financial_snapshot(operation) == before


def test_recovery_closes_each_operation_transaction_before_returning(db_session) -> None:
    """Recovery owns durable per-operation work, not a caller-owned outer transaction."""
    from app.services.task_recovery import recover_stale_billing_operations

    created_at = datetime(2025, 7, 1, tzinfo=UTC)
    operation = BillingOperation(
        id="manual-order-transaction-boundary",
        tenant_id="tenant-a",
        user_id="user-a",
        operation="doubao_brand_voice_order_renew",
        idempotency_key="manual-order-transaction-boundary",
        request_hash="q" * 64,
        quote_hash="r" * 64,
        pricing_snapshot=_manual_order_snapshot("doubao_brand_voice_order_renew"),
        requested_credits=Decimal("30000"),
        settled_credits=Decimal("0"),
        released_credits=Decimal("0"),
        status="in_progress",
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(operation)
    db_session.commit()

    summary = recover_stale_billing_operations(
        db_session,
        now=created_at + timedelta(days=400),
    )

    assert summary.exempt_manual_order_ids == ("manual-order-transaction-boundary",)
    assert not db_session.in_transaction()


def test_recovery_releases_stale_queued_video_operation_after_enqueue_failure(
    db_session,
) -> None:
    from app.services.task_recovery import recover_stale_billing_operations

    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    operation = BillingOperation(
        id="stale-enqueue-failure",
        tenant_id="tenant-a",
        user_id="user-a",
        operation="ecom_cutout",
        idempotency_key="stale-enqueue-failure",
        request_hash="c" * 64,
        quote_hash="d" * 64,
        pricing_snapshot=_pricing_snapshot(operation="ecom_cutout"),
        requested_credits=Decimal("1"),
        settled_credits=Decimal("0"),
        released_credits=Decimal("0"),
        status="in_progress",
        created_at=created_at,
        updated_at=created_at,
    )
    task = VideoTask(
        id="stale-enqueue-task",
        tenant_id="tenant-a",
        status="queued",
        mode="video",
        video_mode="video",
        params={"billing_operation_id": operation.id, "billing_item_index": 0},
        created_at=created_at,
        updated_at=created_at,
    )
    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_reserved = 1
    usage = UsageRecord(
        id="stale-enqueue-usage",
        tenant_id="tenant-a",
        subscription_id=subscription.id,
        video_task_id=task.id,
        billing_operation_id=operation.id,
        billing_item_index=0,
        billing_pricing_line_index=0,
        capability="image",
        provider="apimart",
        unit="image",
        quantity=Decimal("1"),
        credits=Decimal("1"),
        cost_cents=0,
        status="reserved",
    )
    db_session.add_all([operation, task])
    db_session.flush()
    db_session.add(usage)
    db_session.commit()

    summary = recover_stale_billing_operations(
        db_session,
        now=created_at + timedelta(hours=2),
    )

    assert operation.id in summary.released_operation_ids
    assert operation.status == "completed"
    assert operation.completion_kind == "failed"


def test_recovery_releases_video_operation_with_schema_invalid_result(db_session) -> None:
    from app.services.task_recovery import recover_stale_billing_operations

    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    operation = BillingOperation(
        id="invalid-video-result",
        tenant_id="tenant-a",
        user_id="user-a",
        operation="video_create",
        idempotency_key="invalid-video-result",
        request_hash="e" * 64,
        quote_hash="f" * 64,
        pricing_snapshot=_pricing_snapshot(operation="video_create"),
        requested_credits=Decimal("1"),
        settled_credits=Decimal("0"),
        released_credits=Decimal("0"),
        status="in_progress",
        result_type="video_task",
        result_id="invalid-video-result-task",
        result_payload={"task_id": "invalid-video-result-task", "status": "broken"},
        created_at=created_at,
        updated_at=created_at,
    )
    task = VideoTask(
        id="invalid-video-result-task",
        tenant_id="tenant-a",
        status="running",
        mode="video",
        video_mode="video",
        params={"billing_operation_id": operation.id},
        created_at=created_at,
        updated_at=created_at,
    )
    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_reserved = 1
    db_session.add_all([operation, task])
    db_session.flush()
    db_session.add(
        UsageRecord(
            id="invalid-video-result-usage",
            tenant_id="tenant-a",
            subscription_id=subscription.id,
            video_task_id=task.id,
            billing_operation_id=operation.id,
            billing_item_index=0,
            billing_pricing_line_index=0,
            capability="video",
            provider="seedance",
            unit="second",
            quantity=Decimal("1"),
            credits=Decimal("1"),
            cost_cents=0,
            status="reserved",
        )
    )
    db_session.commit()

    summary = recover_stale_billing_operations(db_session, now=created_at)

    assert summary.released_operation_ids == (operation.id,)
    assert operation.completion_kind == "failed"


def test_recovery_settles_persisted_ecom_output_after_other_item_times_out(
    db_session,
) -> None:
    from app.services.task_recovery import recover_stale_billing_operations

    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    snapshot = _pricing_snapshot(operation="ecom_cutout")
    snapshot["pricing_lines"][0]["quantity"] = "2"
    snapshot["pricing_lines"][0]["subtotal_credits"] = "2.0000"
    snapshot["subtotal_credits"] = "2.0000"
    snapshot["payable_credits"] = 2
    operation = BillingOperation(
        id="partial-ecom-recovery",
        tenant_id="tenant-a",
        user_id="user-a",
        operation="ecom_cutout",
        idempotency_key="partial-ecom-recovery",
        request_hash="g" * 64,
        quote_hash="h" * 64,
        pricing_snapshot=snapshot,
        requested_credits=Decimal("2"),
        settled_credits=Decimal("0"),
        released_credits=Decimal("0"),
        status="in_progress",
        result_type="ecom_image_batch",
        result_id="partial-ecom-batch",
        created_at=created_at,
        updated_at=created_at,
    )
    done_task = VideoTask(
        id="partial-ecom-done",
        tenant_id="tenant-a",
        status="done",
        mode="photo",
        video_mode="photo",
        params={
            "billing_operation_id": operation.id,
            "billing_item_index": 0,
            "source_asset_id": "source-done",
        },
        created_at=created_at,
        updated_at=created_at,
    )
    stale_task = VideoTask(
        id="partial-ecom-stale",
        tenant_id="tenant-a",
        status="running",
        mode="photo",
        video_mode="photo",
        params={
            "billing_operation_id": operation.id,
            "billing_item_index": 1,
            "source_asset_id": "source-stale",
        },
        created_at=created_at,
        updated_at=created_at,
    )
    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_reserved = 2
    db_session.add_all([operation, done_task, stale_task])
    db_session.flush()
    db_session.add_all(
        [
            UsageRecord(
                id=f"partial-ecom-usage-{index}",
                tenant_id="tenant-a",
                subscription_id=subscription.id,
                video_task_id=task.id,
                billing_operation_id=operation.id,
                billing_item_index=index,
                billing_pricing_line_index=0,
                capability="image",
                provider="apimart",
                unit="image",
                quantity=Decimal("1"),
                credits=Decimal("1"),
                cost_cents=0,
                status="reserved",
            )
            for index, task in enumerate((done_task, stale_task))
        ]
    )
    asset = Asset(
        id="partial-ecom-output",
        tenant_id="tenant-a",
        type="generated_image",
        source="generated",
        storage_key="tenants/tenant-a/photos/output.png",
        status="ready",
    )
    db_session.add(asset)
    db_session.flush()
    db_session.add(TaskAsset(video_task_id=done_task.id, asset_id=asset.id, role="output_image"))
    db_session.commit()

    from app.services.task_recovery import _billing_task_timeout_seconds, _task_is_stale

    assert _task_is_stale(
        stale_task,
        timeout_seconds=_billing_task_timeout_seconds(operation, stale_task),
        now=created_at + timedelta(days=2),
    )

    summary = recover_stale_billing_operations(
        db_session,
        now=created_at + timedelta(days=2),
    )

    assert summary.settled_operation_ids == (operation.id,), (
        summary,
        stale_task.status,
        operation.status,
        operation.completion_kind,
    )
    assert operation.completion_kind == "succeeded"
    assert operation.settled_credits == Decimal("1")
    assert operation.released_credits == Decimal("1")


class _ProgressStore:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, object]]] = []

    def update(self, task_id: str, **fields: object) -> None:
        self.updates.append((task_id, fields))


def test_stale_aibrain_reservation_is_released_once_without_touching_live_or_completed(
    auth_db,
    auth_context,
) -> None:
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    stale_at = now - timedelta(seconds=1901)
    fresh_at = now - timedelta(seconds=30)
    with auth_db() as db:
        conversation = ChatConversation(
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            title="Recovery",
        )
        db.add(conversation)
        db.flush()
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("200"),
            operation_key=f"seed-topup:{auth_context['tenant_id']}",
        )
        orphan = ChatMessage(
            tenant_id=auth_context["tenant_id"],
            conversation_id=conversation.id,
            role="user",
            content="orphan",
            attachments=[],
            tier="low",
            model="gpt-5.6-luna",
            status="pending",
            created_at=stale_at,
            updated_at=stale_at,
        )
        live = ChatMessage(
            tenant_id=auth_context["tenant_id"],
            conversation_id=conversation.id,
            role="user",
            content="live",
            attachments=[],
            tier="low",
            model="gpt-5.6-luna",
            status="pending",
            created_at=fresh_at,
            updated_at=fresh_at,
        )
        completed = ChatMessage(
            tenant_id=auth_context["tenant_id"],
            conversation_id=conversation.id,
            role="user",
            content="completed",
            attachments=[],
            tier="low",
            model="gpt-5.6-luna",
            status="completed",
            reserved_credits=Decimal("100"),
            created_at=stale_at,
            updated_at=stale_at,
        )
        db.add_all([orphan, live, completed])
        db.flush()
        for message in (orphan, live):
            reservation = aibrain._apply_reasoning_wallet_change(
                db,
                tenant_id=auth_context["tenant_id"],
                entry_type="reserve",
                amount_credits=Decimal("100"),
                chat_message_id=message.id,
                operation_key=f"reserve:{message.id}",
            )
            message.reserved_credits = reservation.ledger_entry.amount_credits
        orphan_id = orphan.id
        live_id = live.id
        completed_id = completed.id
        db.commit()

    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
        aibrain_stale_after_seconds=1800,
    )
    repeated = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
        aibrain_stale_after_seconds=1800,
    )

    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        releases = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"],
                    ReasoningLedgerEntry.entry_type == "release",
                )
            )
        )
        assert db.get(ChatMessage, orphan_id).status == "failed"
        assert db.get(ChatMessage, live_id).status == "pending"
        assert db.get(ChatMessage, completed_id).status == "completed"
        assert wallet.available_credits == Decimal("100")
        assert wallet.reserved_credits == Decimal("100")
        assert len(releases) == 1
        assert releases[0].operation_key == f"release:{orphan_id}"

    assert result.aibrain_reservations == 1
    assert repeated.aibrain_reservations == 0


def test_stale_copy_reservation_is_released_once_without_touching_live_record(
    auth_db,
    auth_context,
) -> None:
    from app.services import quota
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    stale_at = now - timedelta(seconds=1901)
    fresh_at = now - timedelta(seconds=30)
    with auth_db() as db:
        stale = quota.reserve_copy_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            provider="deepseek",
            model="deepseek-v4-flash",
        ).usage_record
        fresh = quota.reserve_copy_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            provider="deepseek",
            model="deepseek-v4-flash",
        ).usage_record
        stale.created_at = stale_at
        fresh.created_at = fresh_at
        stale_id = stale.id
        fresh_id = fresh.id
        db.commit()

    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
        aibrain_stale_after_seconds=1800,
    )
    repeated = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
        aibrain_stale_after_seconds=1800,
    )

    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        assert db.get(UsageRecord, stale_id).status == "released"
        assert db.get(UsageRecord, fresh_id).status == "reserved"
        assert subscription.quota_credits_reserved == 1

    assert result.copy_reservations == 1
    assert repeated.copy_reservations == 0


def test_stale_running_photo_becomes_failed_and_releases_reserved_quota(
    auth_db,
    auth_context,
) -> None:
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    task_id = "orphan-photo-001"
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        subscription.quota_credits_reserved = 20
        task = VideoTask(
            id=task_id,
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            status="running",
            mode="photo",
            video_mode="photo",
            progress=30,
            started_at=now - timedelta(seconds=1901),
            updated_at=now - timedelta(seconds=1901),
        )
        db.add(task)
        db.flush()
        db.add(
            UsageRecord(
                tenant_id=auth_context["tenant_id"],
                subscription_id=subscription.id,
                video_task_id=task.id,
                capability="image",
                provider="apimart",
                model="gpt-image-2",
                unit="image",
                quantity=Decimal("1"),
                credits=Decimal("20"),
                cost_cents=0,
                status="reserved",
            )
        )
        db.commit()

    progress_store = _ProgressStore()
    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
        progress_store=progress_store,
    )
    repeated = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
        progress_store=progress_store,
    )

    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        usage = db.scalar(
            select(UsageRecord).where(UsageRecord.video_task_id == task_id)
        )

    assert result.photo_tasks == 1
    assert repeated.photo_tasks == 0
    assert task.status == "failed"
    assert task.finished_at.replace(tzinfo=UTC) == now
    assert task.error_code == "IMAGE_GEN_FAILED"
    assert subscription.quota_credits_reserved == 0
    assert usage.status == "released"
    assert progress_store.updates == [
        (
            f'{auth_context["tenant_id"]}:{task_id}',
            {
                "status": "failed",
                "stage": "failed",
                "error": "Image generation worker stopped before completion.",
                "error_code": "IMAGE_GEN_FAILED",
                "error_message": "Image generation worker stopped before completion.",
            },
        )
    ]


def test_stale_running_video_reverse_prompt_fails_and_releases_reserved_quota(
    auth_db,
    auth_context,
) -> None:
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    job_id = "orphan-reverse-video-001"
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        subscription.quota_credits_reserved = 100
        job = ReversePromptJob(
            id=job_id,
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            source_kind="video",
            target_format="seedance_2_0",
            status="running",
            updated_at=now - timedelta(seconds=1901),
        )
        db.add(job)
        db.flush()
        db.add(
            UsageRecord(
                tenant_id=auth_context["tenant_id"],
                subscription_id=subscription.id,
                reverse_prompt_job_id=job.id,
                capability="reverse_prompt_video",
                provider="apimart",
                model="gemini-3.1-pro-preview",
                unit="call",
                quantity=Decimal("1"),
                credits=Decimal("100"),
                cost_cents=0,
                status="reserved",
            )
        )
        db.commit()

    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
    )

    with auth_db() as db:
        job = db.get(ReversePromptJob, job_id)
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.reverse_prompt_job_id == job_id
            )
        )

    assert result.reverse_prompt_jobs == 1
    assert job.status == "failed"
    assert job.error_code == "REVERSE_PROMPT_FAILED"
    assert job.error_message == "Video reverse-prompt worker stopped before completion."
    assert subscription.quota_credits_reserved == 0
    assert usage.status == "released"


def test_stale_generating_replicate_job_keeps_successes_and_fails_unfinished_outputs(
    auth_db,
    auth_context,
) -> None:
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    stale_at = now - timedelta(seconds=1901)
    job_id = "orphan-replicate-001"
    with auth_db() as db:
        job = EcomReplicateJob(
            id=job_id,
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            status="generating",
            output_mode="main",
            requested_size="1024x1024",
            requested_aspect="1:1",
            output_count=2,
            total_credits=Decimal("30"),
            started_at=stale_at,
            updated_at=stale_at,
        )
        db.add(job)
        db.flush()
        db.add_all(
            [
                EcomReplicateOutput(
                    job_id=job.id,
                    tenant_id=job.tenant_id,
                    index=0,
                    theme="kept",
                    status="succeeded",
                    requested_size="1024x1024",
                    requested_aspect="1:1",
                    updated_at=stale_at,
                ),
                EcomReplicateOutput(
                    job_id=job.id,
                    tenant_id=job.tenant_id,
                    index=1,
                    theme="interrupted",
                    status="generating",
                    requested_size="1024x1024",
                    requested_aspect="1:1",
                    updated_at=stale_at,
                ),
            ]
        )
        db.commit()

    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
    )

    with auth_db() as db:
        job = db.get(EcomReplicateJob, job_id)
        outputs = list(
            db.scalars(
                select(EcomReplicateOutput)
                .where(EcomReplicateOutput.job_id == job_id)
                .order_by(EcomReplicateOutput.index)
            )
        )

    assert result.ecom_replicate_jobs == 1
    assert job.status == "partial_failed"
    assert job.error_code == "ECOM_REPLICATE_PARTIAL_FAILED"
    assert job.finished_at.replace(tzinfo=UTC) == now
    assert outputs[0].status == "succeeded"
    assert outputs[1].status == "failed"
    assert outputs[1].error_code == "ECOM_REPLICATE_RENDER_FAILED"
    assert (
        outputs[1].error_message
        == "E-commerce replicate worker stopped before completion."
    )


def test_recovery_ignores_soft_deleted_ecom_replicate_job(
    auth_db,
    auth_context,
) -> None:
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    stale_at = now - timedelta(seconds=1901)
    job_id = "soft-deleted-orphan-replicate"
    output_id = "soft-deleted-orphan-output"
    with auth_db() as db:
        job = EcomReplicateJob(
            id=job_id,
            tenant_id=auth_context["tenant_id"],
            status="generating",
            output_mode="main",
            requested_size="1024x1024",
            requested_aspect="1:1",
            output_count=1,
            total_credits=Decimal("15"),
            started_at=stale_at,
            updated_at=stale_at,
            deleted_at=now,
        )
        db.add(job)
        db.flush()
        db.add(
            EcomReplicateOutput(
                id=output_id,
                job_id=job.id,
                tenant_id=job.tenant_id,
                index=0,
                theme="must-stay-generating",
                status="generating",
                requested_size="1024x1024",
                requested_aspect="1:1",
                updated_at=stale_at,
            )
        )
        db.commit()

    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
    )

    assert result.ecom_replicate_jobs == 0
    with auth_db() as db:
        job = db.get(EcomReplicateJob, job_id)
        output = db.get(EcomReplicateOutput, output_id)
        assert job.status == "generating"
        assert job.finished_at is None
        assert job.deleted_at is not None
        assert output.status == "generating"
        assert output.error_code is None


def test_stale_replicate_with_all_outputs_succeeded_finishes_completed(
    auth_db,
    auth_context,
) -> None:
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    stale_at = now - timedelta(seconds=1901)
    job_id = "orphan-replicate-finished-001"
    with auth_db() as db:
        job = EcomReplicateJob(
            id=job_id,
            tenant_id=auth_context["tenant_id"],
            status="generating",
            output_mode="main",
            requested_size="1024x1024",
            requested_aspect="1:1",
            output_count=1,
            total_credits=Decimal("15"),
            started_at=stale_at,
            updated_at=stale_at,
        )
        db.add(job)
        db.flush()
        db.add(
            EcomReplicateOutput(
                job_id=job.id,
                tenant_id=job.tenant_id,
                index=0,
                theme="already-rendered",
                status="succeeded",
                requested_size="1024x1024",
                requested_aspect="1:1",
                updated_at=stale_at,
            )
        )
        db.commit()

    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
    )

    with auth_db() as db:
        job = db.get(EcomReplicateJob, job_id)

    assert result.ecom_replicate_jobs == 1
    assert job.status == "completed"
    assert job.error_code is None
    assert job.error_message is None


def test_recovery_leaves_fresh_and_non_recoverable_work_untouched(
    auth_db,
    auth_context,
) -> None:
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    stale_at = now - timedelta(seconds=1901)
    fresh_at = now - timedelta(seconds=30)
    photo_id = "fresh-photo-001"
    video_gen_id = "fresh-video-gen-001"
    avatar_id = "stale-avatar-001"
    reverse_job_id = "fresh-reverse-video-001"
    replicate_job_id = "active-replicate-001"
    output_id = "active-replicate-output-001"
    with auth_db() as db:
        photo = VideoTask(
            id=photo_id,
            tenant_id=auth_context["tenant_id"],
            status="running",
            mode="photo",
            video_mode="photo",
            updated_at=fresh_at,
        )
        video_gen = VideoTask(
            id=video_gen_id,
            tenant_id=auth_context["tenant_id"],
            status="running",
            mode="video_gen",
            video_mode="video_gen",
            updated_at=fresh_at,
        )
        avatar = VideoTask(
            id=avatar_id,
            tenant_id=auth_context["tenant_id"],
            status="running",
            mode="avatar_talk",
            video_mode="avatar_talk",
            updated_at=stale_at,
        )
        reverse_job = ReversePromptJob(
            id=reverse_job_id,
            tenant_id=auth_context["tenant_id"],
            source_kind="video",
            target_format="seedance_2_0",
            status="running",
            updated_at=fresh_at,
        )
        replicate_job = EcomReplicateJob(
            id=replicate_job_id,
            tenant_id=auth_context["tenant_id"],
            status="generating",
            output_mode="main",
            requested_size="1024x1024",
            requested_aspect="1:1",
            output_count=1,
            total_credits=Decimal("15"),
            started_at=stale_at,
            updated_at=stale_at,
        )
        db.add_all([photo, video_gen, avatar, reverse_job, replicate_job])
        db.flush()
        output = EcomReplicateOutput(
            id=output_id,
            job_id=replicate_job.id,
            tenant_id=replicate_job.tenant_id,
            index=0,
            theme="active",
            status="generating",
            requested_size="1024x1024",
            requested_aspect="1:1",
            updated_at=fresh_at,
        )
        db.add(output)
        db.commit()

    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
    )

    with auth_db() as db:
        assert db.get(VideoTask, photo_id).status == "running"
        assert db.get(VideoTask, video_gen_id).status == "running"
        assert db.get(VideoTask, avatar_id).status == "running"
        assert db.get(ReversePromptJob, reverse_job_id).status == "running"
        assert db.get(EcomReplicateJob, replicate_job_id).status == "generating"
        assert db.get(EcomReplicateOutput, output_id).status == "generating"

    assert result.photo_tasks == 0
    assert result.video_gen_tasks == 0
    assert result.reverse_prompt_jobs == 0
    assert result.ecom_replicate_jobs == 0


def test_video_gen_recovery_does_not_refresh_another_tenants_batch(
    auth_db,
    auth_context,
) -> None:
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    now = datetime(2026, 7, 25, 8, 0, tzinfo=UTC)
    stale_at = now - timedelta(seconds=1901)
    batch_id = "tenant-boundary-batch"
    other_tenant_id = "recovery-other-tenant"
    task_id = "cross-tenant-video-gen"
    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug="recovery-other", name="Recovery Other"))
        db.add(
            BatchJob(
                id=batch_id,
                tenant_id=auth_context["tenant_id"],
                user_id=auth_context["user_id"],
                kind="prompt_set",
                status="running",
                total=1,
                succeeded=0,
                failed=0,
                common_params={},
            )
        )
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=other_tenant_id,
                batch_id=batch_id,
                status="running",
                mode="video_gen",
                video_mode="video_gen",
                updated_at=stale_at,
            )
        )
        db.commit()

    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
    )

    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        batch = db.get(BatchJob, batch_id)

    assert result.video_gen_tasks == 1
    assert task is not None
    assert task.status == "failed"
    assert batch is not None
    assert batch.status == "running"
    assert batch.succeeded == 0
    assert batch.failed == 0


def test_video_gen_recovery_keeps_single_task_without_batch_supported(
    auth_db,
    auth_context,
) -> None:
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    now = datetime(2026, 7, 25, 8, 0, tzinfo=UTC)
    task_id = "video-gen-without-batch"
    with auth_db() as db:
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=auth_context["tenant_id"],
                status="running",
                mode="video_gen",
                video_mode="video_gen",
                updated_at=now - timedelta(seconds=1901),
            )
        )
        db.commit()

    result = recover_orphaned_image_queue_tasks(
        session_factory=auth_db,
        now=now,
        stale_after_seconds=1800,
    )

    with auth_db() as db:
        task = db.get(VideoTask, task_id)

    assert result.video_gen_tasks == 1
    assert task is not None
    assert task.batch_id is None
    assert task.status == "failed"


def test_lifespan_periodically_runs_orphan_recovery(monkeypatch) -> None:
    from app import main as app_main

    called = Event()
    captured: dict[str, object] = {}

    def fake_recover(**kwargs):
        captured.update(kwargs)
        called.set()

    progress_store = object()
    monkeypatch.setattr(
        app_main.settings,
        "engine_orphan_recovery_interval_seconds",
        0.01,
    )
    monkeypatch.setattr(
        app_main,
        "recover_orphaned_image_queue_tasks",
        fake_recover,
        raising=False,
    )
    monkeypatch.setattr(
        app_main,
        "build_progress_store",
        lambda _redis_url: progress_store,
        raising=False,
    )

    with TestClient(app):
        assert called.wait(timeout=1)

    assert captured["session_factory"] is app_main.SessionLocal
    assert captured["progress_store"] is progress_store


def test_lifespan_recovery_still_runs_when_progress_store_is_unavailable(
    monkeypatch,
) -> None:
    from app import main as app_main

    called = Event()
    captured: dict[str, object] = {}

    def fake_recover(**kwargs):
        captured.update(kwargs)
        called.set()

    def unavailable_progress_store(_redis_url):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(
        app_main.settings,
        "engine_orphan_recovery_interval_seconds",
        0.01,
    )
    monkeypatch.setattr(app_main, "recover_orphaned_image_queue_tasks", fake_recover)
    monkeypatch.setattr(app_main, "build_progress_store", unavailable_progress_store)

    with TestClient(app):
        assert called.wait(timeout=1)

    assert captured["session_factory"] is app_main.SessionLocal
    assert captured["progress_store"] is None
