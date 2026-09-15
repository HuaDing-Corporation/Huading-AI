"""Avatar-only durable ownership. Caller owns checkpoint/financial transactions."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update

from app.db.models import AvatarProviderRun, Tenant, VideoTask

LEASE_SECONDS = 180
_RUNNABLE = ("ready", "active", "pending")
_IMMUTABLE = (
    "model",
    "idempotency_key",
    "request_fingerprint",
    "request_body",
    "submitted_at",
    "input_expires_at",
    "provider_job_id",
)
_MUTABLE = {"state", "checkpoint", "next_check_at", "lease_until", *_IMMUTABLE}


class AvatarLeaseLost(RuntimeError):
    pass


class AvatarRunInvariant(RuntimeError):
    pass


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def create_avatar_run(db, *, task):
    params = task.params or {}
    model = params.get("avatar_model")
    if (
        task.video_mode != "avatar_talk"
        or params.get("avatar_provider") != "heygen"
        or model not in {"avatar_iv", "lipsync_precision"}
    ):
        raise AvatarRunInvariant("Invalid avatar provider snapshot.")
    db.flush()
    run = AvatarProviderRun(
        task_id=task.id,
        tenant_id=task.tenant_id,
        model=model,
        idempotency_key=f"avatar:{task.tenant_id}:{task.id}:{model}",
    )
    db.add(run)
    db.flush()
    return run


def claim_avatar_run(session_factory, *, tenant_id, task_id, now=None):
    now = now or datetime.now(UTC)
    owner = str(uuid4())
    with session_factory() as db:
        db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
        task_exists = (
            select(VideoTask.id)
            .where(
                VideoTask.id == task_id,
                VideoTask.tenant_id == tenant_id,
                VideoTask.status.in_(("queued", "running")),
                VideoTask.deleted_at.is_(None),
            )
            .exists()
        )
        result = db.execute(
            update(AvatarProviderRun)
            .where(
                AvatarProviderRun.task_id == task_id,
                AvatarProviderRun.tenant_id == tenant_id,
                AvatarProviderRun.state.in_(_RUNNABLE),
                task_exists,
                or_(AvatarProviderRun.lease_until.is_(None), AvatarProviderRun.lease_until <= now),
            )
            .values(
                owner=owner,
                lease_until=now + timedelta(seconds=LEASE_SECONDS),
                state="active",
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            db.rollback()
            return None
        db.execute(
            update(VideoTask)
            .where(
                VideoTask.id == task_id,
                VideoTask.tenant_id == tenant_id,
            )
            .values(status="running", updated_at=now)
        )
        db.commit()
    return owner


def fenced_avatar_run(db, *, tenant_id, task_id, owner, now=None):
    """Tenant is the common first lock, including financial recovery/finalization."""
    now = now or datetime.now(UTC)
    with db.no_autoflush:
        db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
        identity = db.execute(
            select(
                AvatarProviderRun.owner,
                AvatarProviderRun.lease_until,
                AvatarProviderRun.state,
            )
            .where(
                AvatarProviderRun.task_id == task_id,
                AvatarProviderRun.tenant_id == tenant_id,
            )
            .with_for_update()
        ).one_or_none()
    if (
        identity is None
        or identity.owner != owner
        or identity.lease_until is None
        or _utc(identity.lease_until) <= now
        or identity.state not in _RUNNABLE
    ):
        raise AvatarLeaseLost("Avatar run ownership expired or changed.")
    return db.get(AvatarProviderRun, task_id)


def update_avatar_run(db, *, tenant_id, task_id, owner, now=None, **fields):
    now = now or datetime.now(UTC)
    if set(fields) - _MUTABLE:
        raise AvatarRunInvariant("Unsupported run checkpoint field.")
    run = fenced_avatar_run(db, tenant_id=tenant_id, task_id=task_id, owner=owner, now=now)
    for key, value in fields.items():
        previous = getattr(run, key)
        if key in _IMMUTABLE and previous is not None and previous != value:
            raise AvatarRunInvariant("Avatar request identity is immutable.")
        setattr(run, key, value)
    run.updated_at = now
    run.lease_until = fields.get("lease_until", now + timedelta(seconds=LEASE_SECONDS))
    db.flush()
    return run
