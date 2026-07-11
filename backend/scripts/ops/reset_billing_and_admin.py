from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import Plan, Role, Subscription, Tenant, UsageRecord, User
from app.db.session import SessionLocal

_MIN_BACKUP_BYTES = 1024
_PLAIN_DUMP_MARKER = b"PostgreSQL database dump"
_TARGET_TOTAL_CREDITS = 10_000_000


class BackupGateError(RuntimeError):
    pass


class BillingResetError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetSnapshot:
    tenant_id: str
    user_id: str
    email: str
    role: str
    plan_code: str
    quota_credits_total: int
    quota_credits_used: int
    quota_credits_reserved: int


@dataclass(frozen=True)
class BillingSnapshot:
    usage_records: int
    subscriptions_total: int
    subscriptions_with_balance: int
    target: TargetSnapshot


@dataclass(frozen=True)
class BillingResetSummary:
    apply: bool
    usage_records_deleted: int
    subscriptions_reset: int
    before: BillingSnapshot
    after: BillingSnapshot


def validate_pg_dump_backup(backup_path: Path) -> Path:
    path = backup_path.expanduser().resolve()
    if not path.is_file():
        raise BackupGateError(f"Backup file does not exist: {path}")
    if path.stat().st_size < _MIN_BACKUP_BYTES:
        raise BackupGateError(
            f"Backup file is smaller than {_MIN_BACKUP_BYTES} bytes: {path}"
        )
    with path.open("rb") as backup_file:
        header = backup_file.read(512)
    if not header.startswith(b"PGDMP") and _PLAIN_DUMP_MARKER not in header:
        raise BackupGateError("Backup file is not a recognizable pg_dump output.")
    return path


def _target_user(
    db: Session,
    *,
    tenant_slug: str | None,
    email: str | None,
) -> User:
    if not tenant_slug and not email:
        raise BillingResetError("Provide --tenant-slug, --email, or both.")
    query = select(User).join(Tenant, Tenant.id == User.tenant_id).where(User.is_active.is_(True))
    if tenant_slug:
        query = query.where(Tenant.slug == tenant_slug)
    if email:
        query = query.where(func.lower(User.email) == email.strip().lower())
    users = list(db.scalars(query.order_by(User.created_at.asc(), User.id.asc())))
    if not users:
        raise BillingResetError("Target user was not found.")
    if len(users) != 1:
        raise BillingResetError("Target user is ambiguous; provide both tenant slug and email.")
    return users[0]


def _active_subscription(db: Session, *, tenant_id: str) -> Subscription:
    now = datetime.now(UTC)
    subscription = db.scalar(
        select(Subscription)
        .where(
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
            Subscription.period_start <= now,
            Subscription.period_end >= now,
        )
        .order_by(Subscription.period_end.desc())
    )
    if subscription is None:
        raise BillingResetError("Target tenant has no active subscription.")
    return subscription


def _huading_plan(db: Session) -> Plan:
    plan = db.scalar(
        select(Plan).where(Plan.code == "huading", Plan.is_active.is_(True))
    )
    if plan is None:
        raise BillingResetError("Active huading plan was not found; run migrations first.")
    return plan


def _target_snapshot(db: Session, *, user: User, subscription: Subscription) -> TargetSnapshot:
    plan_code = db.scalar(select(Plan.code).where(Plan.id == subscription.plan_id))
    return TargetSnapshot(
        tenant_id=user.tenant_id,
        user_id=user.id,
        email=user.email,
        role=user.role,
        plan_code=str(plan_code or ""),
        quota_credits_total=subscription.quota_credits_total,
        quota_credits_used=subscription.quota_credits_used,
        quota_credits_reserved=subscription.quota_credits_reserved,
    )


def _billing_snapshot(
    db: Session,
    *,
    user: User,
    subscription: Subscription,
) -> BillingSnapshot:
    usage_records = db.scalar(select(func.count()).select_from(UsageRecord)) or 0
    subscriptions_total = db.scalar(select(func.count()).select_from(Subscription)) or 0
    subscriptions_with_balance = (
        db.scalar(
            select(func.count())
            .select_from(Subscription)
            .where(
                or_(
                    Subscription.quota_credits_used != 0,
                    Subscription.quota_credits_reserved != 0,
                )
            )
        )
        or 0
    )
    return BillingSnapshot(
        usage_records=int(usage_records),
        subscriptions_total=int(subscriptions_total),
        subscriptions_with_balance=int(subscriptions_with_balance),
        target=_target_snapshot(db, user=user, subscription=subscription),
    )


def _projected_snapshot(
    before: BillingSnapshot,
    *,
    user: User,
) -> BillingSnapshot:
    return BillingSnapshot(
        usage_records=0,
        subscriptions_total=before.subscriptions_total,
        subscriptions_with_balance=0,
        target=TargetSnapshot(
            tenant_id=user.tenant_id,
            user_id=user.id,
            email=user.email,
            role=Role.ADMIN.value,
            plan_code="huading",
            quota_credits_total=_TARGET_TOTAL_CREDITS,
            quota_credits_used=0,
            quota_credits_reserved=0,
        ),
    )


def reset_billing_and_admin(
    db: Session,
    *,
    tenant_slug: str | None = None,
    email: str | None = None,
    apply: bool = False,
) -> BillingResetSummary:
    user = _target_user(db, tenant_slug=tenant_slug, email=email)
    subscription = _active_subscription(db, tenant_id=user.tenant_id)
    huading_plan = _huading_plan(db)
    before = _billing_snapshot(db, user=user, subscription=subscription)
    if not apply:
        return BillingResetSummary(
            apply=False,
            usage_records_deleted=before.usage_records,
            subscriptions_reset=before.subscriptions_with_balance,
            before=before,
            after=_projected_snapshot(before, user=user),
        )

    deleted = db.execute(delete(UsageRecord)).rowcount or 0
    reset = (
        db.execute(
            update(Subscription)
            .where(
                or_(
                    Subscription.quota_credits_used != 0,
                    Subscription.quota_credits_reserved != 0,
                )
            )
            .values(quota_credits_used=0, quota_credits_reserved=0)
            .execution_options(synchronize_session=False)
        ).rowcount
        or 0
    )
    user.role = Role.ADMIN.value
    subscription.plan_id = huading_plan.id
    subscription.quota_credits_total = _TARGET_TOTAL_CREDITS
    subscription.quota_credits_used = 0
    subscription.quota_credits_reserved = 0
    db.flush()
    return BillingResetSummary(
        apply=True,
        usage_records_deleted=int(deleted),
        subscriptions_reset=int(reset),
        before=before,
        after=_billing_snapshot(db, user=user, subscription=subscription),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset billing counters and grant one account Huading admin access."
    )
    parser.add_argument("--backup", required=True, type=Path, help="existing pg_dump file")
    parser.add_argument("--tenant-slug", default=None, help="target tenant slug")
    parser.add_argument("--email", default=None, help="target user email")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="commit changes")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="preview only (the default)",
    )
    args = parser.parse_args(argv)
    if not args.tenant_slug and not args.email:
        print("error: provide --tenant-slug, --email, or both", file=sys.stderr)
        return 2
    try:
        backup_path = validate_pg_dump_backup(args.backup)
    except BackupGateError as exc:
        print(f"backup error: {exc}", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        try:
            summary = reset_billing_and_admin(
                db,
                tenant_slug=args.tenant_slug,
                email=args.email,
                apply=args.apply,
            )
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except BillingResetError as exc:
            db.rollback()
            print(f"reset error: {exc}", file=sys.stderr)
            return 2
        except Exception:
            db.rollback()
            raise

    output = {"backup": str(backup_path), **asdict(summary)}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
