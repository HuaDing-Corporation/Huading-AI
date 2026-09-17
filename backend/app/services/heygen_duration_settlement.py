"""A fenced local rejection, never a generic failure-to-charge adapter."""

import hashlib
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select, update

from app.db.models import AvatarProviderRun, UsageRecord, VideoTask
from app.services import avatar_duration_policy as policy
from app.services import billing_operations as billing
from app.services import quota
from app.services.avatar_runs import fenced_avatar_run
from app.services.storage.keys import get_tenant_storage_bytes


def settle_oversize_audio(db, *, tenant_id, task_id, owner, storage) -> bool:
    """Caller commits task, run and financial writes together, under its flush fence."""
    run = fenced_avatar_run(db, tenant_id=tenant_id, task_id=task_id, owner=owner)
    task = db.get(VideoTask, task_id)
    if task is None or task.tenant_id != tenant_id or task.video_mode != "avatar_talk":
        raise billing.BillingInvariantError("duration rejection has no tenant-scoped avatar task")
    receipt = (task.params or {}).get("avatar_duration_acceptance")
    if receipt is None:
        return False  # Historical orders retain their original recovery/refund semantics.
    accepted = policy.validate_receipt(
        receipt, tenant_id=tenant_id, user_id=task.created_by_user_id
    )
    if receipt["model"] != run.model:
        raise billing.BillingInvariantError("accepted avatar model changed")
    checkpoint = run.checkpoint
    if (
        run.provider_job_id
        or run.request_body is not None
        or run.request_fingerprint
        or run.submitted_at
        or run.input_expires_at
        or any(
            checkpoint.get(k)
            for k in ("post_attempted", "remote_completed", "base_key", "final_key")
        )
    ):
        return False
    meter = checkpoint.get("audio_measurement")
    if (
        not isinstance(meter, dict)
        or meter.get("source") != "ffprobe"
        or meter.get("complete") is not True
        or not checkpoint.get("tts_started")
        or not checkpoint.get("audio_key")
        or meter.get("audio_key") != checkpoint["audio_key"]
    ):
        return False
    seconds = policy.measured_duration(meter.get("seconds"))
    if seconds <= policy.MAX_SECONDS:
        return False
    audio = get_tenant_storage_bytes(
        storage,
        tenant_id=tenant_id,
        storage_key=checkpoint["audio_key"],
    )
    if not audio or hashlib.sha256(audio).hexdigest() != meter.get("sha256"):
        return False
    tts_usages = list(
        db.scalars(
            select(UsageRecord).where(
                UsageRecord.tenant_id == tenant_id,
                UsageRecord.video_task_id == task_id,
                UsageRecord.capability == "tts",
                UsageRecord.provider.in_(("doubao-seed-tts", "cosyvoice-tts")),
            )
        )
    )
    if not tts_usages or any(u.quantity <= 0 or u.cost_cents < 0 for u in tts_usages):
        raise billing.BillingInvariantError("completed TTS supplier cost evidence is missing")

    operation_id = (task.params or {}).get("billing_operation_id")
    if operation_id:
        subscription_ids = billing._subscription_ids_for_operation(db, operation_id)
        operation = billing._operation_for_update(db, operation_id)
        if (
            operation.status != "in_progress"
            or operation.operation != "video_create"
            or operation.tenant_id != tenant_id
            or operation.user_id != task.created_by_user_id
            or operation.result_id != task_id
            or operation.request_hash != receipt["request_hash"]
            or operation.quote_hash != receipt["quote_hash"]
            or operation.duration_policy_version != policy.VERSION
            or operation.requested_credits != accepted
        ):
            raise billing.BillingInvariantError(
                "duration acceptance is not bound to the reservation"
            )
        snapshot = billing._snapshot_from_operation(operation)
        if (
            not snapshot.pricing_lines
            or snapshot.pricing_lines[0].capability != "avatar"
            or len(snapshot.pricing_lines) > 2
            or any(line.capability not in {"avatar", "tts"} for line in snapshot.pricing_lines)
            or (len(snapshot.pricing_lines) == 2 and snapshot.pricing_lines[1].capability != "tts")
        ):
            raise billing.BillingInvariantError(
                "duration rejection contains unrelated pricing lines"
            )
        subscriptions = quota.lock_subscriptions_for_billing(db, subscription_ids=subscription_ids)
        usages = billing._usage_for_update(db, operation_id)
        billing._validate_stored_allocations(snapshot, usages, require_reserved=True)
        subscription = billing._operation_subscription(subscriptions, usages)
    else:
        if receipt["pricing_contract"] != "legacy_estimate":
            raise billing.BillingInvariantError("quoted duration acceptance lost its operation")
        record = quota._reserved_record(db, tenant_id=tenant_id, video_task_id=task_id)
        if (
            record is None
            or record.capability != "avatar"
            or record.provider != "heygen"
            or record.billing_operation_id is not None
            or record.subscription_id is None
            or int(Decimal(record.credits).to_integral_value(rounding=ROUND_CEILING)) != accepted
        ):
            raise billing.BillingInvariantError(
                "legacy duration reservation differs from acceptance"
            )
        subscription = quota._subscription_for_update(db, record.subscription_id)
        usages = [record]
        operation = None
    if any(u.video_task_id != task_id or u.tenant_id != tenant_id for u in usages):
        raise billing.BillingInvariantError("duration usage does not belong to task")
    quota.settle_locked_subscription_credits(
        subscription,
        requested_credits=accepted,
        settled_credits=accepted,
    )
    now = datetime.now(UTC)
    for usage in usages:
        usage.status, usage.settled_at = "settled", now
        if usage.capability == "avatar":
            if usage.cost_cents != 0:
                raise billing.BillingInvariantError(
                    "unsubmitted HeyGen task contains supplier cost"
                )
            usage.provider, usage.model = "heygen", run.model
            usage.provider_cost_usd = None
            usage.duration_failure_evidence = {
                "acceptance": receipt,
                "original_task_id": task_id,
                "audio_measurement": meter,
                "error_code": policy.ERROR_CODE,
                "settled_credits": accepted,
                "completion_kind": "failed_charged",
            }
            usage.provider_usage = {
                "submitted": False,
                "cost_cents": 0,
                "measured_audio_seconds": str(seconds),
                "charge_basis": "accepted_quote_duration_policy",
            }
    if operation is not None:
        operation.status, operation.completion_kind = "completed", "failed_charged"
        operation.completed_at = operation.updated_at = now
        operation.settled_credits, operation.released_credits = Decimal(accepted), Decimal(0)
        operation.error_code, operation.error_http_status = policy.ERROR_CODE, 422
        operation.error_payload = {"detail": None}
        operation.result_payload = {"task_id": task_id, "status": "failed"}
    task.status, task.error_code = "failed", policy.ERROR_CODE
    task.error = task.error_message = policy.FAILURE_MESSAGE
    task.finished_at = task.updated_at = now
    task.params = {
        **task.params,
        "billing_outcome": {
            "completion_kind": "failed_charged",
            "status": "settled",
            "policy_version": policy.VERSION,
            "requested_credits": accepted,
            "settled_credits": accepted,
            "released_credits": 0,
        },
    }
    db.flush()
    # The flush is fenced while the run is still active; terminal state ends the lease.
    db.execute(
        update(AvatarProviderRun)
        .where(
            AvatarProviderRun.task_id == task_id,
            AvatarProviderRun.owner == owner,
        )
        .values(state="failed", lease_until=None, updated_at=now)
    )
    return True
