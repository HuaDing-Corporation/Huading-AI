from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import (
    AdminAuditLog,
    BrandVoice,
    Plan,
    ProviderConfig,
    Subscription,
    Tenant,
    UsageRecord,
    User,
    VideoTask,
)
from app.schemas.admin_console import (
    AdminAuditLogItem,
    AdminCreditsAdjustResponse,
    AdminPlanChangeResponse,
    AdminSubscriptionSnapshot,
    AdminTaskItem,
    AdminTenantItem,
    AdminTenantStatusResponse,
    AdminUsageItem,
    AdminVoiceSlotAssignResponse,
    AdminVoiceSlotItem,
    AdminVoiceSlotsResponse,
)
from app.services.plan_access import is_platform_tenant
from app.services.voice_slots import (
    DOUBAO_VOICE_CLONE_PROVIDER,
    assign_speaker_slot,
    speaker_ids,
)


def _page(items: list[object], *, total: int, page: int, page_size: int) -> dict[str, object]:
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _date_bounds(from_: date | None, to: date | None) -> tuple[datetime | None, datetime | None]:
    if from_ is not None and to is not None and from_ > to:
        raise AppError(
            "开始日期不能晚于结束日期。",
            code="INVALID_DATE_RANGE",
            status_code=422,
        )
    start = datetime.combine(from_, time.min, tzinfo=UTC) if from_ is not None else None
    end = (
        datetime.combine(to + timedelta(days=1), time.min, tzinfo=UTC)
        if to is not None
        else None
    )
    return start, end


def _active_subscription_id() -> object:
    now = datetime.now(UTC)
    return (
        select(Subscription.id)
        .where(
            Subscription.tenant_id == Tenant.id,
            Subscription.status == "active",
            Subscription.period_start <= now,
            Subscription.period_end >= now,
        )
        .order_by(Subscription.period_end.desc(), Subscription.created_at.desc())
        .limit(1)
        .correlate(Tenant)
        .scalar_subquery()
    )


def _tenant_statement():
    subscription_id = _active_subscription_id()
    owner_email = (
        select(User.email)
        .where(User.tenant_id == Tenant.id, User.deleted_at.is_(None))
        .order_by(
            case((User.role == "admin", 0), else_=1),
            User.created_at.asc(),
            User.id.asc(),
        )
        .limit(1)
        .correlate(Tenant)
        .scalar_subquery()
    )
    task_count = (
        select(func.count(VideoTask.id))
        .where(VideoTask.tenant_id == Tenant.id, VideoTask.deleted_at.is_(None))
        .correlate(Tenant)
        .scalar_subquery()
    )
    return (
        select(
            Tenant.id.label("tenant_id"),
            Tenant.slug,
            Tenant.name,
            Tenant.status,
            Tenant.created_at,
            owner_email.label("owner_email"),
            Plan.code.label("plan_code"),
            Subscription.id.label("subscription_id"),
            Subscription.quota_credits_total.label("total"),
            Subscription.quota_credits_used.label("used"),
            Subscription.quota_credits_reserved.label("reserved"),
            task_count.label("task_count"),
        )
        .outerjoin(Subscription, Subscription.id == subscription_id)
        .outerjoin(Plan, Plan.id == Subscription.plan_id)
        .where(Tenant.deleted_at.is_(None))
    )


def _tenant_item(row) -> AdminTenantItem:
    subscription = None
    if row.subscription_id is not None:
        total = int(row.total or 0)
        used = int(row.used or 0)
        reserved = int(row.reserved or 0)
        subscription = AdminSubscriptionSnapshot(
            id=row.subscription_id,
            total=total,
            used=used,
            reserved=reserved,
            remaining=total - used - reserved,
        )
    return AdminTenantItem(
        tenant_id=row.tenant_id,
        slug=row.slug,
        name=row.name,
        status=row.status,
        created_at=row.created_at,
        owner_email=row.owner_email,
        plan_code=row.plan_code,
        subscription=subscription,
        task_count=int(row.task_count or 0),
    )


def subscription_snapshot(subscription: Subscription) -> AdminSubscriptionSnapshot:
    total = int(subscription.quota_credits_total)
    used = int(subscription.quota_credits_used)
    reserved = int(subscription.quota_credits_reserved)
    return AdminSubscriptionSnapshot(
        id=subscription.id,
        total=total,
        used=used,
        reserved=reserved,
        remaining=total - used - reserved,
    )


def record_audit(
    db: Session,
    *,
    actor: User,
    action: str,
    target_tenant_id: str | None,
    target_id: str | None,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    reason: str | None,
) -> AdminAuditLog:
    audit = AdminAuditLog(
        actor_user_id=actor.id,
        actor_tenant_id=actor.tenant_id,
        action=action,
        target_tenant_id=target_tenant_id,
        target_id=target_id,
        before=before,
        after=after,
        reason=reason.strip() if reason else None,
    )
    db.add(audit)
    db.flush()
    return audit


def _active_subscription_for_update(db: Session, *, tenant_id: str) -> Subscription:
    now = datetime.now(UTC)
    subscription = db.scalar(
        select(Subscription)
        .where(
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
            Subscription.period_start <= now,
            Subscription.period_end >= now,
        )
        .order_by(Subscription.period_end.desc(), Subscription.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if subscription is None:
        raise AppError(
            "Tenant has no active subscription.",
            code="ACTIVE_SUBSCRIPTION_NOT_FOUND",
            status_code=404,
        )
    return subscription


def adjust_credits(
    db: Session,
    *,
    actor: User,
    tenant_id: str,
    delta: int,
    reason: str,
) -> AdminCreditsAdjustResponse:
    tenant_item_or_404(db, tenant_id=tenant_id)
    subscription = _active_subscription_for_update(db, tenant_id=tenant_id)
    before = subscription_snapshot(subscription)
    new_total = before.total + delta
    if new_total < before.used + before.reserved:
        raise AppError(
            "扣减后额度会低于已用+预留，无法执行。",
            code="CREDIT_TOTAL_BELOW_COMMITTED",
            status_code=422,
        )
    subscription.quota_credits_total = new_total
    subscription.updated_at = datetime.now(UTC)
    after = subscription_snapshot(subscription)
    record_audit(
        db,
        actor=actor,
        action="credits_adjust",
        target_tenant_id=tenant_id,
        target_id=subscription.id,
        before=before.model_dump(),
        after=after.model_dump(),
        reason=reason,
    )
    return AdminCreditsAdjustResponse(
        tenant_id=tenant_id,
        delta=delta,
        subscription=after,
    )


def change_plan(
    db: Session,
    *,
    actor: User,
    tenant_id: str,
    plan_code: str,
    reason: str | None,
) -> AdminPlanChangeResponse:
    tenant_item_or_404(db, tenant_id=tenant_id)
    subscription = _active_subscription_for_update(db, tenant_id=tenant_id)
    current_plan = db.get(Plan, subscription.plan_id)
    target_plan = db.scalar(
        select(Plan).where(Plan.code == plan_code, Plan.is_active.is_(True)).limit(1)
    )
    if target_plan is None:
        raise AppError("Plan not found.", code="PLAN_NOT_FOUND", status_code=404)
    if (
        tenant_id == actor.tenant_id
        and is_platform_tenant(db, tenant_id=tenant_id)
        and plan_code != "huading"
    ):
        raise AppError(
            "平台租户不能降级自身套餐。",
            code="CANNOT_DOWNGRADE_PLATFORM_TENANT",
            status_code=422,
        )
    before = {
        "plan_code": current_plan.code if current_plan is not None else None,
        "subscription": subscription_snapshot(subscription).model_dump(),
    }
    subscription.plan_id = target_plan.id
    subscription.updated_at = datetime.now(UTC)
    snapshot = subscription_snapshot(subscription)
    after = {"plan_code": target_plan.code, "subscription": snapshot.model_dump()}
    record_audit(
        db,
        actor=actor,
        action="plan_change",
        target_tenant_id=tenant_id,
        target_id=subscription.id,
        before=before,
        after=after,
        reason=reason,
    )
    return AdminPlanChangeResponse(
        tenant_id=tenant_id,
        plan_code=target_plan.code,
        subscription=snapshot,
    )


def change_tenant_status(
    db: Session,
    *,
    actor: User,
    tenant_id: str,
    active: bool,
    reason: str | None,
) -> AdminTenantStatusResponse:
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None or tenant.deleted_at is not None:
        raise AppError("Tenant not found.", code="TENANT_NOT_FOUND", status_code=404)
    if not active and is_platform_tenant(db, tenant_id=tenant.id):
        raise AppError(
            "平台租户不能停用自身账号。",
            code="CANNOT_SUSPEND_PLATFORM_TENANT",
            status_code=422,
        )
    before = {"status": tenant.status}
    tenant.status = "active" if active else "suspended"
    tenant.updated_at = datetime.now(UTC)
    after = {"status": tenant.status}
    record_audit(
        db,
        actor=actor,
        action="status_change",
        target_tenant_id=tenant.id,
        target_id=tenant.id,
        before=before,
        after=after,
        reason=reason,
    )
    return AdminTenantStatusResponse(tenant_id=tenant.id, status=tenant.status)


def assign_tenant_voice_slot(
    db: Session,
    *,
    actor: User,
    tenant_id: str,
    speaker_id: str,
    reason: str | None,
) -> AdminVoiceSlotAssignResponse:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at is not None:
        raise AppError("Tenant not found.", code="TENANT_NOT_FOUND", status_code=404)
    existing_config = db.scalar(
        select(ProviderConfig).where(
            ProviderConfig.tenant_id == tenant.id,
            ProviderConfig.capability == "voice_clone",
            ProviderConfig.provider == DOUBAO_VOICE_CLONE_PROVIDER,
        )
        .with_for_update()
    )
    before_ids = speaker_ids(
        (existing_config.config or {}).get("speaker_ids") if existing_config is not None else []
    )
    summary = assign_speaker_slot(
        db,
        tenant_slug=tenant.slug,
        speaker_id=speaker_id,
        apply=True,
    )
    record_audit(
        db,
        actor=actor,
        action="voice_slot_assign",
        target_tenant_id=tenant.id,
        target_id=tenant.id,
        before={"speaker_ids": before_ids},
        after={
            "speaker_ids": list(summary.speaker_ids),
            "speaker_id": summary.speaker_id,
            "changed": summary.changed,
        },
        reason=reason,
    )
    return AdminVoiceSlotAssignResponse(
        tenant_id=tenant.id,
        speaker_id=summary.speaker_id,
        changed=summary.changed,
        speaker_ids=list(summary.speaker_ids),
    )


def prepare_task_retry(
    db: Session,
    *,
    actor: User,
    task_id: str,
) -> VideoTask:
    task = db.scalar(select(VideoTask).where(VideoTask.id == task_id).with_for_update())
    if task is None or task.deleted_at is not None:
        raise AppError("Task not found.", code="TASK_NOT_FOUND", status_code=404)
    if task.status != "failed":
        raise AppError(
            "Only failed tasks can be retried.",
            code="TASK_NOT_RETRYABLE",
            status_code=409,
        )
    before = {
        "status": task.status,
        "progress": task.progress,
        "error_code": task.error_code,
        "error_message": task.error_message,
    }
    task.status = "queued"
    task.progress = 0
    task.error = None
    task.error_code = None
    task.error_message = None
    task.started_at = None
    task.finished_at = None
    task.updated_at = datetime.now(UTC)
    record_audit(
        db,
        actor=actor,
        action="task_retry",
        target_tenant_id=task.tenant_id,
        target_id=task.id,
        before=before,
        after={"status": "queued", "progress": 0},
        reason=None,
    )
    db.flush()
    return task


def task_retry_payload(task: VideoTask) -> dict[str, object]:
    payload = dict(task.params or {})
    payload.update(
        {
            "tenant_id": task.tenant_id,
            "video_task_id": task.id,
            "video_mode": task.video_mode,
        }
    )
    if task.topic:
        payload.setdefault("topic", task.topic)
    if task.script:
        payload.setdefault("script", task.script)
    payload.setdefault("aspect_ratio", task.aspect_ratio)
    payload.setdefault("subtitle_enabled", task.subtitle_enabled)
    payload.setdefault("speed", float(task.speed))
    if task.voice_id:
        payload.setdefault("voice_id", task.voice_id)
    if task.brand_voice_id:
        payload.setdefault("brand_voice_id", task.brand_voice_id)
    return payload


def list_tenants(
    db: Session,
    *,
    q: str | None,
    plan: str | None,
    status: str | None,
    sort: str,
    order: str,
    page: int,
    page_size: int,
) -> dict[str, object]:
    statement = _tenant_statement()
    if q and (term := q.strip().lower()):
        pattern = f"%{term}%"
        statement = statement.where(
            or_(
                func.lower(Tenant.slug).like(pattern),
                func.lower(Tenant.name).like(pattern),
                func.lower(statement.selected_columns.owner_email).like(pattern),
            )
        )
    if plan:
        statement = statement.where(Plan.code == plan)
    if status:
        statement = statement.where(Tenant.status == status)

    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    total_expr = func.coalesce(statement.selected_columns.total, 0)
    used_expr = func.coalesce(statement.selected_columns.used, 0)
    reserved_expr = func.coalesce(statement.selected_columns.reserved, 0)
    sort_expr = {
        "credits_used": used_expr,
        "balance": total_expr - used_expr - reserved_expr,
        "created_at": Tenant.created_at,
    }[sort]
    ordered = sort_expr.asc() if order == "asc" else sort_expr.desc()
    statement = (
        statement.order_by(ordered, Tenant.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [_tenant_item(row) for row in db.execute(statement)]
    return _page(items, total=total, page=page, page_size=page_size)


def tenant_item_or_404(db: Session, *, tenant_id: str) -> AdminTenantItem:
    row = db.execute(_tenant_statement().where(Tenant.id == tenant_id)).first()
    if row is None:
        raise AppError("Tenant not found.", code="TENANT_NOT_FOUND", status_code=404)
    return _tenant_item(row)


def _usage_statement(
    *,
    tenant_id: str | None,
    from_: date | None,
    to: date | None,
    capability: str | None,
    provider: str | None,
    status: str | None,
):
    start, end = _date_bounds(from_, to)
    statement = select(UsageRecord, Tenant.slug, Tenant.name).join(
        Tenant, Tenant.id == UsageRecord.tenant_id
    )
    if tenant_id:
        statement = statement.where(UsageRecord.tenant_id == tenant_id)
    if start:
        statement = statement.where(UsageRecord.created_at >= start)
    if end:
        statement = statement.where(UsageRecord.created_at < end)
    if capability:
        statement = statement.where(UsageRecord.capability == capability)
    if provider:
        statement = statement.where(UsageRecord.provider == provider)
    if status:
        statement = statement.where(UsageRecord.status == status)
    return statement


def _usage_item(row) -> AdminUsageItem:
    record: UsageRecord = row[0]
    return AdminUsageItem(
        id=record.id,
        created_at=record.created_at,
        tenant_id=record.tenant_id,
        tenant_slug=row[1],
        tenant_name=row[2],
        capability=record.capability,
        provider=record.provider,
        model=record.model,
        quantity=float(record.quantity),
        unit=record.unit,
        credits=float(record.credits),
        cost_cents=record.cost_cents,
        status=record.status,
        video_task_id=record.video_task_id,
    )


def list_usage(
    db: Session,
    *,
    tenant_id: str | None,
    from_: date | None,
    to: date | None,
    capability: str | None,
    provider: str | None,
    status: str | None,
    page: int,
    page_size: int,
) -> dict[str, object]:
    statement = _usage_statement(
        tenant_id=tenant_id,
        from_=from_,
        to=to,
        capability=capability,
        provider=provider,
        status=status,
    )
    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = db.execute(
        statement.order_by(UsageRecord.created_at.desc(), UsageRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return _page(
        [_usage_item(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def export_usage_rows(db: Session, **filters) -> list[AdminUsageItem]:
    statement = _usage_statement(**filters)
    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    if total > 50_000:
        raise AppError(
            "导出记录超过 50000 条，请缩小时间范围。",
            code="USAGE_EXPORT_TOO_LARGE",
            status_code=422,
        )
    statement = statement.order_by(UsageRecord.created_at.desc(), UsageRecord.id.desc())
    return [_usage_item(row) for row in db.execute(statement)]


def _task_item(row) -> AdminTaskItem:
    task: VideoTask = row[0]
    duration = None
    if task.started_at is not None and task.finished_at is not None:
        duration = max(0.0, (task.finished_at - task.started_at).total_seconds())
    return AdminTaskItem(
        id=task.id,
        tenant_id=task.tenant_id,
        tenant_slug=row[1],
        tenant_name=row[2],
        mode=task.mode,
        video_mode=task.video_mode,
        status=task.status,
        progress=task.progress,
        error_code=task.error_code,
        error_message=task.error_message or task.error,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        duration_seconds=duration,
    )


def list_tasks(
    db: Session,
    *,
    tenant_id: str | None,
    status: str | None,
    from_: date | None,
    to: date | None,
    page: int,
    page_size: int,
) -> dict[str, object]:
    start, end = _date_bounds(from_, to)
    statement = (
        select(VideoTask, Tenant.slug, Tenant.name)
        .join(Tenant, Tenant.id == VideoTask.tenant_id)
        .where(VideoTask.deleted_at.is_(None))
    )
    if tenant_id:
        statement = statement.where(VideoTask.tenant_id == tenant_id)
    if status:
        statement = statement.where(VideoTask.status == status)
    if start:
        statement = statement.where(VideoTask.created_at >= start)
    if end:
        statement = statement.where(VideoTask.created_at < end)
    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = db.execute(
        statement.order_by(VideoTask.created_at.desc(), VideoTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return _page(
        [_task_item(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def recent_tasks(db: Session, *, tenant_id: str, limit: int = 10) -> list[AdminTaskItem]:
    page = list_tasks(
        db,
        tenant_id=tenant_id,
        status=None,
        from_=None,
        to=None,
        page=1,
        page_size=limit,
    )
    return list(page["items"])


def recent_usage(db: Session, *, tenant_id: str, limit: int = 10) -> list[AdminUsageItem]:
    page = list_usage(
        db,
        tenant_id=tenant_id,
        from_=None,
        to=None,
        capability=None,
        provider=None,
        status=None,
        page=1,
        page_size=limit,
    )
    return list(page["items"])


def voice_slots(db: Session) -> AdminVoiceSlotsResponse:
    configs = list(
        db.scalars(
            select(ProviderConfig).where(
                ProviderConfig.capability == "voice_clone",
                ProviderConfig.provider == DOUBAO_VOICE_CLONE_PROVIDER,
                ProviderConfig.is_active.is_(True),
            )
        )
    )
    tenants = {tenant.id: tenant for tenant in db.scalars(select(Tenant))}
    voices = list(
        db.scalars(
            select(BrandVoice).where(
                BrandVoice.speaker_id.is_not(None),
                BrandVoice.deleted_at.is_(None),
            )
        )
    )
    voices_by_key = {(voice.tenant_id, str(voice.speaker_id)): voice for voice in voices}
    voices_by_speaker = {str(voice.speaker_id): voice for voice in voices}
    slots: dict[tuple[str, str | None, str], set[str]] = {}

    for speaker_id in speaker_ids(settings.engine_doubao_voice_clone_speaker_ids):
        slots.setdefault(("platform", None, speaker_id), set()).add("env")
    for config in configs:
        values = dict(config.config or {})
        configured_ids = speaker_ids(values.get("speaker_ids"))
        configured_ids.extend(speaker_ids(values.get("used_speaker_ids")))
        scope = "platform" if config.tenant_id is None else "tenant"
        source = "provider_config" if config.tenant_id is None else "tenant_config"
        for speaker_id in dict.fromkeys(configured_ids):
            slots.setdefault((scope, config.tenant_id, speaker_id), set()).add(source)

    items: list[AdminVoiceSlotItem] = []
    for (scope, tenant_id, speaker_id), sources in sorted(slots.items(), key=lambda item: item[0]):
        voice = (
            voices_by_key.get((tenant_id, speaker_id))
            if tenant_id is not None
            else voices_by_speaker.get(speaker_id)
        )
        tenant = tenants.get(tenant_id) if tenant_id is not None else None
        occupied_tenant = tenants.get(voice.tenant_id) if voice is not None else tenant
        items.append(
            AdminVoiceSlotItem(
                speaker_id=speaker_id,
                scope=scope,
                sources=sorted(sources),
                tenant_id=voice.tenant_id if voice is not None else tenant_id,
                tenant_slug=occupied_tenant.slug if occupied_tenant is not None else None,
                tenant_name=occupied_tenant.name if occupied_tenant is not None else None,
                occupied=voice is not None,
                brand_voice_id=voice.id if voice is not None else None,
                brand_voice_name=voice.name if voice is not None else None,
                brand_voice_status=voice.status if voice is not None else None,
            )
        )
    return AdminVoiceSlotsResponse(
        items=items,
        total=len(items),
        remaining=sum(not item.occupied for item in items),
    )


def list_audit_logs(
    db: Session,
    *,
    action: str | None,
    target_tenant_id: str | None,
    page: int,
    page_size: int,
) -> dict[str, object]:
    target_tenant = aliased(Tenant)
    actor = aliased(User)
    statement = (
        select(AdminAuditLog, actor.email, target_tenant.slug)
        .outerjoin(actor, actor.id == AdminAuditLog.actor_user_id)
        .outerjoin(target_tenant, target_tenant.id == AdminAuditLog.target_tenant_id)
    )
    if action:
        statement = statement.where(AdminAuditLog.action == action)
    if target_tenant_id:
        statement = statement.where(AdminAuditLog.target_tenant_id == target_tenant_id)
    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = db.execute(
        statement.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        AdminAuditLogItem(
            id=row[0].id,
            actor_user_id=row[0].actor_user_id,
            actor_email=row[1],
            actor_tenant_id=row[0].actor_tenant_id,
            action=row[0].action,
            target_tenant_id=row[0].target_tenant_id,
            target_tenant_slug=row[2],
            target_id=row[0].target_id,
            before=row[0].before,
            after=row[0].after,
            reason=row[0].reason,
            created_at=row[0].created_at,
        )
        for row in rows
    ]
    return _page(items, total=total, page=page, page_size=page_size)
