from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Plan, ProviderConfig, Role, Subscription, Tenant, UsageRecord, User
from app.db.session import SessionLocal

_MIN_BACKUP_BYTES = 1024
_PLAIN_DUMP_MARKER = b"PostgreSQL database dump"
_TARGET_TOTAL_CREDITS = 10_000_000
_DOUBAO_VOICE_CLONE_PROVIDER = "doubao-voice-clone"
_SPEAKER_ID_PATTERN = re.compile(r"^S_[A-Za-z0-9_-]{1,157}$")


class BackupGateError(RuntimeError):
    pass


class BillingResetError(RuntimeError):
    pass


class SpeakerSlotAssignmentError(RuntimeError):
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


@dataclass(frozen=True)
class SpeakerSlotAssignmentSummary:
    apply: bool
    tenant_id: str
    tenant_slug: str
    speaker_id: str
    config_created: bool
    changed: bool
    speaker_ids: tuple[str, ...]


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


def _speaker_ids(raw_ids: object) -> list[str]:
    candidates = raw_ids.split(",") if isinstance(raw_ids, str) else raw_ids or []
    return list(dict.fromkeys(str(item).strip() for item in candidates if str(item).strip()))


def assign_speaker_slot(
    db: Session,
    *,
    tenant_slug: str,
    speaker_id: str,
    apply: bool = False,
) -> SpeakerSlotAssignmentSummary:
    slug = tenant_slug.strip()
    normalized_speaker_id = speaker_id.strip()
    if not slug:
        raise SpeakerSlotAssignmentError("Tenant slug must not be empty.")
    if not _SPEAKER_ID_PATTERN.fullmatch(normalized_speaker_id):
        raise SpeakerSlotAssignmentError("Speaker ID must match S_[A-Za-z0-9_-]+.")

    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise SpeakerSlotAssignmentError("Target tenant was not found.")

    configs = list(
        db.scalars(
            select(ProviderConfig).where(
                ProviderConfig.capability == "voice_clone",
                ProviderConfig.provider == _DOUBAO_VOICE_CLONE_PROVIDER,
            )
        )
    )
    tenant_config = next((item for item in configs if item.tenant_id == tenant.id), None)
    for config in configs:
        if config is tenant_config:
            continue
        values = dict(config.config or {})
        configured = set(_speaker_ids(values.get("speaker_ids")))
        used = set(_speaker_ids(values.get("used_speaker_ids")))
        if normalized_speaker_id in configured or normalized_speaker_id in used:
            raise SpeakerSlotAssignmentError(
                "Speaker ID is already assigned to another tenant or the platform pool."
            )

    active_platform_config = next(
        (item for item in configs if item.tenant_id is None and item.is_active),
        None,
    )
    platform_values = (
        dict(active_platform_config.config or {})
        if active_platform_config is not None
        else {}
    )
    if (
        "speaker_ids" not in platform_values
        and normalized_speaker_id
        in _speaker_ids(settings.engine_doubao_voice_clone_speaker_ids)
    ):
        raise SpeakerSlotAssignmentError(
            "Speaker ID is already assigned to another tenant or the platform pool."
        )

    values = dict(tenant_config.config or {}) if tenant_config is not None else {}
    speaker_ids = _speaker_ids(values.get("speaker_ids"))
    slot_added = normalized_speaker_id not in speaker_ids
    if slot_added:
        speaker_ids.append(normalized_speaker_id)
    config_created = tenant_config is None
    changed = config_created or slot_added or not bool(tenant_config.is_active)

    if apply and changed:
        values["speaker_ids"] = speaker_ids
        if tenant_config is None:
            tenant_config = ProviderConfig(
                tenant_id=tenant.id,
                capability="voice_clone",
                provider=_DOUBAO_VOICE_CLONE_PROVIDER,
                config=values,
                is_active=True,
            )
            db.add(tenant_config)
        else:
            tenant_config.config = values
            tenant_config.is_active = True
        db.flush()

    return SpeakerSlotAssignmentSummary(
        apply=apply,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        speaker_id=normalized_speaker_id,
        config_created=config_created,
        changed=changed,
        speaker_ids=tuple(speaker_ids),
    )


def _add_mode_arguments(parser: argparse.ArgumentParser) -> None:
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="commit changes")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="preview only (the default)",
    )


def _validated_backup_or_error(backup: Path) -> Path | None:
    try:
        return validate_pg_dump_backup(backup)
    except BackupGateError as exc:
        print(f"backup error: {exc}", file=sys.stderr)
        return None


def _run_billing_reset(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Reset billing counters and grant one account Huading admin access."
    )
    parser.add_argument("--backup", required=True, type=Path, help="existing pg_dump file")
    parser.add_argument("--tenant-slug", default=None, help="target tenant slug")
    parser.add_argument("--email", default=None, help="target user email")
    _add_mode_arguments(parser)
    args = parser.parse_args(argv)
    if not args.tenant_slug and not args.email:
        print("error: provide --tenant-slug, --email, or both", file=sys.stderr)
        return 2
    backup_path = _validated_backup_or_error(args.backup)
    if backup_path is None:
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


def _run_assign_speaker_slot(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="assign-speaker-slot",
        description="Assign an exclusive Doubao voice-clone speaker slot to one tenant.",
    )
    parser.add_argument("--backup", required=True, type=Path, help="existing pg_dump file")
    parser.add_argument("--tenant-slug", required=True, help="target tenant slug")
    parser.add_argument("--speaker-id", required=True, help="purchased speaker ID")
    _add_mode_arguments(parser)
    args = parser.parse_args(argv)
    backup_path = _validated_backup_or_error(args.backup)
    if backup_path is None:
        return 2

    with SessionLocal() as db:
        try:
            summary = assign_speaker_slot(
                db,
                tenant_slug=args.tenant_slug,
                speaker_id=args.speaker_id,
                apply=args.apply,
            )
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except SpeakerSlotAssignmentError as exc:
            db.rollback()
            print(f"speaker slot error: {exc}", file=sys.stderr)
            return 2
        except Exception:
            db.rollback()
            raise

    output = {
        "operation": "assign-speaker-slot",
        "backup": str(backup_path),
        **asdict(summary),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "assign-speaker-slot":
        return _run_assign_speaker_slot(arguments[1:])
    return _run_billing_reset(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
