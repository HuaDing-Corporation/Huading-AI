"""Recoverable avatar execution, separate from the historical OmniHuman pipeline."""

import asyncio
import math
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from threading import Event, Thread

from sqlalchemy import event, or_, select, update

from app.db.models import AvatarProviderRun, UsageRecord, VideoTask
from app.providers.avatar.heygen import (
    HeyGenError,
    HeyGenPending,
    HeyGenReviewRequired,
    HeyGenTerminalFailure,
    heygen_factory,
    request_fingerprint,
)
from app.services.avatar_runs import (
    AvatarLeaseLost,
    claim_avatar_run,
    fenced_avatar_run,
    update_avatar_run,
)
from app.services.heygen_config import configured_cost_snapshot
from app.services.storage.keys import validate_catalog_storage_key, validate_tenant_storage_key
from app.workers import avatar_talk as legacy


def _cause(exc, kind):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, kind):
            return exc
        exc = exc.__cause__
    return None


@contextmanager
def _lease_heartbeat(tenant_id, task_id, owner):
    stop = Event()

    def renew():
        while not stop.wait(30):
            try:
                with legacy.SessionLocal() as db:
                    if db.get_bind().dialect.name == "postgresql":
                        from sqlalchemy import text

                        db.execute(text("SET LOCAL lock_timeout = '2s'"))
                    update_avatar_run(db, tenant_id=tenant_id, task_id=task_id, owner=owner)
                    db.commit()
            except AvatarLeaseLost:
                return
            except Exception:
                # A failed renewal cannot authorize writes; every flush is fenced below.
                continue

    thread = Thread(target=renew, name="heygen-lease", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=3)


def _install_write_fence(db, *, tenant_id, task_id, owner):
    def before_flush(session, _context, _instances):
        fenced_avatar_run(session, tenant_id=tenant_id, task_id=task_id, owner=owner)

    event.listen(db, "before_flush", before_flush)
    return before_flush


def _progress(ctx, *, step, progress, **fields):
    task = legacy._task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    ctx.store.update(
        legacy._scoped_id(ctx.tenant_id, ctx.task_id),
        status=task.status,
        progress=max(task.progress or 0, progress),
        step=step,
        avatar_provider="heygen",
        avatar_model=(task.params or {}).get("avatar_model"),
        error_code=task.error_code or "",
        error_message=task.error_message or "",
        **fields,
    )


def _checkpoint(ctx, owner, **values):
    run = fenced_avatar_run(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id, owner=owner)
    checkpoint = {**run.checkpoint, **values}
    update_avatar_run(
        ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id, owner=owner, checkpoint=checkpoint
    )
    ctx.db.commit()
    return checkpoint


def _validate_source_before_tts(ctx, model):
    asset = legacy._input_avatar_asset(ctx)
    if asset.tenant_id not in {None, ctx.tenant_id}:
        raise HeyGenError("Avatar source belongs to another tenant.")
    if model == "lipsync_precision":
        if asset.type != "video" or asset.tenant_id != ctx.tenant_id:
            raise HeyGenError("Invalid video source snapshot.")
    elif asset.type not in {"image", "avatar_image"}:
        raise HeyGenError("Invalid photo source snapshot.")
    if asset.tenant_id is None:
        validate_catalog_storage_key(asset.storage_key)
    else:
        validate_tenant_storage_key(ctx.tenant_id, asset.storage_key)


def _align_source(ctx, source_bytes):
    """Loop moving source locally, once, before a single remote paid submission."""
    duration = float(ctx.duration_sec or 0)
    if not math.isfinite(duration) or not 1 <= duration <= 150:
        raise HeyGenError("HeyGen audio duration is outside the supported local limit.")
    work = ctx.work_dir or legacy._work_dir(ctx.task_id)
    work.mkdir(parents=True, exist_ok=True)
    source, output = work / "heygen-source.mp4", work / "heygen-aligned.mp4"
    source.write_bytes(source_bytes)
    subprocess.run(
        [
            legacy._ffmpeg_binary(),
            "-nostdin",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(source),
            "-t",
            str(duration),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    measured = legacy._video_duration_sec(output)
    if measured <= 0 or abs(measured - duration) > 0.25:
        raise HeyGenError("Local avatar source alignment failed.")
    return output.read_bytes()


def heygen_step(ctx, *, owner, provider):
    task = legacy._task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    legacy._enforce_billed_avatar_duration_quote(ctx, task=task)
    ctx.db.commit()
    run = ctx.db.get(AvatarProviderRun, ctx.task_id)
    if not run.request_body and not run.provider_job_id:
        avatar = legacy._input_avatar_asset(ctx)
        if avatar.tenant_id not in {None, ctx.tenant_id}:
            raise HeyGenError("Avatar source belongs to another tenant.")
        source_key = avatar.storage_key
        if run.model == "lipsync_precision":
            if avatar.type != "video" or avatar.tenant_id != ctx.tenant_id:
                raise HeyGenError("Invalid video source snapshot.")
            source_bytes = legacy.get_tenant_storage_bytes(
                ctx.storage,
                tenant_id=ctx.tenant_id,
                storage_key=source_key,
            )
            source_key = legacy._artifact_key(ctx, "aligned.mp4")
            legacy.put_tenant_storage_bytes(
                ctx.storage,
                tenant_id=ctx.tenant_id,
                storage_key=source_key,
                content=_align_source(ctx, source_bytes),
                content_type="video/mp4",
            )
        elif avatar.type not in {"image", "avatar_image"}:
            raise HeyGenError("Invalid photo source snapshot.")
        now = datetime.now(UTC)
        ttl = max(3600, int(legacy.settings.engine_s3_presign_ttl))
        payload = {
            "audio_url": legacy.presign_tenant_storage_key(
                ctx.storage,
                tenant_id=ctx.tenant_id,
                storage_key=ctx.audio_key,
                expires_in=ttl,
            ),
            "video_url"
            if run.model == "lipsync_precision"
            else "image_url": legacy.presign_owned_storage_key(
                ctx.storage,
                tenant_id=ctx.tenant_id,
                owner_tenant_id=avatar.tenant_id,
                storage_key=source_key,
                expires_in=ttl,
            ),
            "aspect_ratio": task.aspect_ratio,
        }
        body = provider.build_request(payload, model=run.model)
        update_avatar_run(
            ctx.db,
            tenant_id=ctx.tenant_id,
            task_id=ctx.task_id,
            owner=owner,
            request_body=body,
            request_fingerprint=request_fingerprint(body),
            submitted_at=now,
            input_expires_at=now + timedelta(seconds=ttl),
        )
        ctx.db.commit()
    run = ctx.db.get(AvatarProviderRun, ctx.task_id)
    post_attempted_before = bool(run.checkpoint.get("post_attempted"))
    if not run.provider_job_id:
        _checkpoint(ctx, owner, post_attempted=True)
    run = ctx.db.get(AvatarProviderRun, ctx.task_id)

    def renew():
        update_avatar_run(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id, owner=owner)
        ctx.db.commit()

    def submitted(job_id):
        update_avatar_run(
            ctx.db,
            tenant_id=ctx.tenant_id,
            task_id=ctx.task_id,
            owner=owner,
            provider_job_id=job_id,
        )
        ctx.db.commit()

    def progress(value):
        count = int(value.get("poll_count") or 1)
        # Never hit 85 while waiting. Across recoveries the store retains the maximum.
        current = ctx.store.read(legacy._scoped_id(ctx.tenant_id, ctx.task_id)) or {}
        previous = max(25.0, float(current.get("progress") or 25))
        _progress(
            ctx,
            step="avatar",
            progress=previous + (85 - previous) * 0.001,
            stage="heygen_generating",
            poll_count=count,
        )

    def iso(value):
        if value is None:
            return None
        return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).isoformat()

    payload = {
        "request_body": run.request_body,
        "request_fingerprint": run.request_fingerprint,
        "idempotency_key": run.idempotency_key,
        "provider_job_id": run.provider_job_id,
        "submitted_at": iso(run.submitted_at),
        "input_expires_at": iso(run.input_expires_at),
        "post_attempted_before": post_attempted_before,
        "before_request": renew,
        "on_submitted": submitted,
        "progress_callback": progress,
    }
    method = provider.generate_avatar if run.model == "avatar_iv" else provider.generate_change_lips
    result = asyncio.run(method(payload))
    _checkpoint(ctx, owner, remote_completed=True)
    _record_provider_cost(ctx, owner)
    data = asyncio.run(provider.download_video(result["video_url"]))
    work = ctx.work_dir or legacy._work_dir(ctx.task_id)
    work.mkdir(parents=True, exist_ok=True)
    path = work / "heygen-result.mp4"
    path.write_bytes(data)
    duration = legacy._video_duration_sec(path)
    if duration <= 0 or duration + 0.25 < float(ctx.duration_sec):
        raise HeyGenReviewRequired("HeyGen output is shorter than external speech or invalid.")
    base_key = legacy._artifact_key(ctx, "heygen-base.mp4")
    legacy.put_tenant_storage_bytes(
        ctx.storage,
        tenant_id=ctx.tenant_id,
        storage_key=base_key,
        content=data,
        content_type="video/mp4",
    )
    _checkpoint(ctx, owner, base_key=base_key)
    ctx.base_video_bytes = data
    return ctx


def _record_provider_cost(ctx, owner):
    run = fenced_avatar_run(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id, owner=owner)
    seconds = legacy._precise_billable_seconds(ctx.duration_sec)
    estimate = run.checkpoint["cost_snapshot"]
    cost = int(
        (Decimal(estimate["cny_per_second"]) * seconds * 100).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    for usage in ctx.db.scalars(
        select(UsageRecord).where(
            UsageRecord.video_task_id == ctx.task_id,
            UsageRecord.capability == "avatar",
        )
    ):
        usage.provider, usage.model, usage.cost_cents = "heygen", run.model, cost
        usage.provider_usage = {
            **estimate,
            "seconds": str(seconds),
            "provider_job_id": run.provider_job_id,
        }
        usage.provider_cost_usd = None
    ctx.db.commit()


def _complete(ctx, owner):
    run = fenced_avatar_run(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id, owner=owner)
    task = legacy._task_or_raise(ctx.db, tenant_id=ctx.tenant_id, task_id=ctx.task_id)
    seconds = legacy._precise_billable_seconds(ctx.duration_sec)
    estimate = run.checkpoint["cost_snapshot"]
    cost = int(
        (Decimal(estimate["cny_per_second"]) * seconds * 100).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    task.status, task.progress = "done", 100
    task.storage_key, task.size_bytes, task.duration_sec = (
        ctx.storage_key,
        ctx.size_bytes,
        float(seconds),
    )
    task.finished_at = task.updated_at = datetime.now(UTC)
    if legacy._billing_operation_id(task):
        legacy._persist_billing_actual_seconds(task=task, actual_seconds=seconds)
        legacy._complete_billing_quote_video(
            ctx.db,
            task=task,
            actual_seconds=seconds,
            base_cost_cents=cost,
            base_provider="heygen",
            base_model=run.model,
        )
    else:
        legacy.settle_reserved_quota(
            ctx.db,
            tenant_id=ctx.tenant_id,
            video_task_id=ctx.task_id,
            actual_seconds=max(1, int(round(float(seconds)))),
            cost_cents=cost,
            provider="heygen",
            model=run.model,
        )
    for usage in ctx.db.scalars(
        select(UsageRecord).where(
            UsageRecord.video_task_id == ctx.task_id,
            UsageRecord.capability == "avatar",
        )
    ):
        usage.provider_usage = {
            **estimate,
            "seconds": str(seconds),
            "provider_job_id": run.provider_job_id,
        }
        usage.provider_cost_usd = None
    legacy.refresh_batch_job(ctx.db, batch_id=task.batch_id)
    # Flush financial changes under the live fence before making run terminal.
    ctx.db.flush()
    ctx.db.execute(
        update(AvatarProviderRun)
        .where(
            AvatarProviderRun.task_id == ctx.task_id,
            AvatarProviderRun.owner == owner,
        )
        .values(state="completed", lease_until=None)
    )
    ctx.db.commit()
    _progress(ctx, step="done", progress=100)


def run_heygen_avatar_pipeline(*, tenant_id, task_id):
    owner = claim_avatar_run(legacy.SessionLocal, tenant_id=tenant_id, task_id=task_id)
    if owner is None:
        with legacy.SessionLocal() as db:
            task = legacy._task_or_raise(db, tenant_id=tenant_id, task_id=task_id)
            return {"task_id": task_id, "status": task.status}
    store = legacy.build_progress_store(legacy.settings.redis_url)
    storage = legacy.create_object_storage(legacy.settings)
    with legacy.SessionLocal() as db, _lease_heartbeat(tenant_id, task_id, owner):
        fence = _install_write_fence(db, tenant_id=tenant_id, task_id=task_id, owner=owner)
        ctx = legacy.AvatarTalkContext(
            task_id=task_id,
            tenant_id=tenant_id,
            db=db,
            store=store,
            storage=storage,
            work_dir=legacy._work_dir(task_id) / owner,
            artifact_prefix=f"tenants/{tenant_id}/videos/{task_id}/{owner}",
        )
        try:
            run = db.get(AvatarProviderRun, task_id)
            snapshot = run.checkpoint
            if snapshot.get("tts_started") and not snapshot.get("audio_key"):
                raise HeyGenReviewRequired("TTS outcome requires reconciliation; synthesis held.")
            if "cost_snapshot" not in snapshot:
                snapshot = _checkpoint(
                    ctx,
                    owner,
                    cost_snapshot=configured_cost_snapshot(legacy.settings, model=run.model),
                )
            provider = heygen_factory()
            task = legacy._task_or_raise(db, tenant_id=tenant_id, task_id=task_id)
            task.error_code = task.error_message = task.error = None
            task.started_at = task.started_at or datetime.now(UTC)
            db.commit()
            if not snapshot.get("audio_key"):
                _validate_source_before_tts(ctx, run.model)
                legacy.script_step(ctx)
                db.commit()
                _checkpoint(ctx, owner, tts_started=True)
                legacy.tts_step(ctx)
                snapshot = _checkpoint(
                    ctx,
                    owner,
                    audio_key=ctx.audio_key,
                    duration_sec=ctx.duration_sec,
                    timeline=ctx.timeline,
                )
            else:
                ctx.audio_key = snapshot["audio_key"]
                ctx.duration_sec = snapshot["duration_sec"]
                ctx.timeline = snapshot["timeline"]
                # Missing durable audio is a review hold, never another TTS request.
                legacy.get_tenant_storage_bytes(
                    storage, tenant_id=tenant_id, storage_key=ctx.audio_key
                )
            ctx.use_tts_audio = True
            _progress(ctx, step="tts", progress=25)
            if snapshot.get("base_key"):
                ctx.base_video_bytes = legacy.get_tenant_storage_bytes(
                    storage, tenant_id=tenant_id, storage_key=snapshot["base_key"]
                )
            else:
                heygen_step(ctx, owner=owner, provider=provider)
            _progress(ctx, step="avatar", progress=85)
            snapshot = db.get(AvatarProviderRun, task_id).checkpoint
            if snapshot.get("final_key"):
                ctx.storage_key, ctx.size_bytes = snapshot["final_key"], snapshot["size_bytes"]
                legacy.get_tenant_storage_bytes(
                    storage, tenant_id=tenant_id, storage_key=ctx.storage_key
                )
            else:
                legacy.subtitle_step(ctx)
                db.commit()
                _progress(ctx, step="subtitle", progress=90)
                legacy.compose_step(ctx)
                _progress(ctx, step="compose", progress=95)
                legacy.upload_step(ctx)
                _checkpoint(ctx, owner, final_key=ctx.storage_key, size_bytes=ctx.size_bytes)
            _complete(ctx, owner)
            event.remove(db, "before_flush", fence)
            fence = None
            legacy.prune_video_history_best_effort(
                db,
                tenant_id=tenant_id,
                mode="avatar_talk",
                storage=storage,
                keep=20,
            )
            return {"task_id": task_id, "status": "done"}
        except AvatarLeaseLost:
            db.rollback()
            return {"task_id": task_id, "status": "running"}
        except Exception as exc:
            db.rollback()
            run = fenced_avatar_run(db, tenant_id=tenant_id, task_id=task_id, owner=owner)
            pending = _cause(exc, HeyGenPending)
            error = _cause(exc, HeyGenError)
            app_error = _cause(exc, legacy.AppError)
            if app_error is not None and app_error.code == "BILLING_QUOTE_EXCEEDED":
                error = HeyGenTerminalFailure("Generated video exceeded the accepted quote.")
            # Once paid I/O could have happened, unfamiliar local failures cannot refund.
            if (
                pending is None
                and run.checkpoint.get("tts_started")
                and not isinstance(error, HeyGenTerminalFailure)
            ):
                pending = HeyGenReviewRequired("Avatar checkpoint requires manual review.")
            task = legacy._task_or_raise(db, tenant_id=tenant_id, task_id=task_id)
            task.error_code = (pending or error).code if pending or error else "HEYGEN_FAILED"
            if app_error is not None and app_error.code == "BILLING_QUOTE_EXCEEDED":
                task.error_code = app_error.code
            task.error_message = (
                str(pending or error) if pending or error else "Avatar generation failed."
            )
            task.error = task.error_message
            task.updated_at = datetime.now(UTC)
            if pending:
                task.status = "running"
                state = "review" if isinstance(pending, HeyGenReviewRequired) else "pending"
            else:
                task.status = "failed"
                task.finished_at = datetime.now(UTC)
                if not legacy._fail_billing_quote_video(
                    db, task=task, code=task.error_code, http_status=502
                ):
                    legacy.release_reserved_quota(db, tenant_id=tenant_id, video_task_id=task_id)
                state = "failed"
            db.flush()
            db.execute(
                update(AvatarProviderRun)
                .where(
                    AvatarProviderRun.task_id == task_id,
                    AvatarProviderRun.owner == owner,
                )
                .values(
                    state=state,
                    lease_until=None,
                    next_check_at=datetime.now(UTC) + timedelta(seconds=30),
                )
            )
            db.commit()
            _progress(
                ctx,
                step="provider_review_required"
                if state == "review"
                else "provider_reconciling"
                if pending
                else "failed",
                progress=task.progress or 1,
            )
            return {"task_id": task_id, "status": task.status}
        finally:
            if fence is not None:
                event.remove(db, "before_flush", fence)


def enqueue_due_avatar_runs(session_factory, *, now=None):
    now = now or datetime.now(UTC)
    with session_factory() as db:
        identities = list(
            db.execute(
                select(AvatarProviderRun.tenant_id, AvatarProviderRun.task_id)
                .join(VideoTask, VideoTask.id == AvatarProviderRun.task_id)
                .where(
                    AvatarProviderRun.state.in_(("ready", "active", "pending")),
                    AvatarProviderRun.next_check_at <= now,
                    or_(
                        AvatarProviderRun.lease_until.is_(None),
                        AvatarProviderRun.lease_until <= now,
                    ),
                    VideoTask.status.in_(("queued", "running")),
                    VideoTask.deleted_at.is_(None),
                )
                .limit(100)
            )
        )
    for tenant_id, task_id in identities:
        legacy.generate_avatar_talk_task.apply_async(
            args=[{"tenant_id": tenant_id, "video_task_id": task_id}],
            task_id=task_id,
            queue="avatar",
        )
    return len(identities)
