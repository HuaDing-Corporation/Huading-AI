from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.models import UsageRecord, VideoTask
from app.db.session import SessionLocal
from app.services import apimart_costs, provider_costs


@dataclass
class BackfillSummary:
    matched: int
    updated: int
    apply: bool
    preview: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    unchanged: list[dict[str, Any]]
    anomalies: list[dict[str, Any]]


_BACKFILL_PROVIDERS = {
    "apimart",
    "omnihuman",
    "doubao-seed-tts",
    "deepseek",
}
_APIMART_PROVIDER = "apimart"
_NON_APIMART_ZERO_COST_PROVIDERS = _BACKFILL_PROVIDERS - {_APIMART_PROVIDER}
_MISMATCH_MIN_ABSOLUTE_CENTS = 1
_MISMATCH_RELATIVE_TOLERANCE = Decimal("0.05")


def _decimal_quantity(record: UsageRecord) -> Decimal:
    return Decimal(str(record.quantity or 0))


def _task_params(task: VideoTask | None) -> dict[str, Any]:
    if task is None or not isinstance(task.params, dict):
        return {}
    return dict(task.params)


def _task_duration(task: VideoTask | None, record: UsageRecord) -> float | None:
    if task is not None and task.duration_sec:
        return float(task.duration_sec)
    quantity = _decimal_quantity(record)
    return float(quantity) if quantity > 0 else None


def _apimart_backfill_cost(record: UsageRecord, task: VideoTask | None) -> int:
    params = _task_params(task)
    return apimart_costs.apimart_cost_cents_from_price_table(
        model=record.model,
        resolution=str(params.get("resolution") or params.get("size") or ""),
        duration_sec=_task_duration(task, record),
    )


def _omnihuman_backfill_cost(record: UsageRecord, task: VideoTask | None) -> int:
    seconds = _task_duration(task, record)
    if seconds is None:
        return 0
    return provider_costs.omnihuman_cost_cents(seconds)


def _seed_tts_backfill_cost(record: UsageRecord, _task: VideoTask | None) -> int:
    if record.unit != "char":
        return 0
    return provider_costs.seed_tts_cost_cents(_decimal_quantity(record))


def _deepseek_backfill_cost(record: UsageRecord, _task: VideoTask | None) -> int:
    if record.unit != "token":
        return 0
    total_tokens = int(_decimal_quantity(record))
    if total_tokens <= 0:
        return 0
    return provider_costs.deepseek_cost_cents(
        prompt_tokens=total_tokens,
        completion_tokens=0,
    )


def _cost_for_record(record: UsageRecord, task: VideoTask | None) -> tuple[int, str]:
    provider = (record.provider or "").strip().lower()
    if provider == _APIMART_PROVIDER:
        return _apimart_backfill_cost(record, task), "apimart_price_table"
    if provider == "omnihuman":
        return _omnihuman_backfill_cost(record, task), "omnihuman_duration_rate"
    if provider == "doubao-seed-tts":
        return _seed_tts_backfill_cost(record, task), "seed_tts_char_rate"
    if provider == "deepseek":
        return _deepseek_backfill_cost(record, task), "deepseek_token_input_rate"
    return 0, "unsupported_provider"


def _skip_reason(record: UsageRecord) -> str:
    provider = (record.provider or "").strip().lower()
    if provider == "doubao-seed-tts" and record.unit != "char":
        return "missing_char_quantity"
    if provider == "deepseek" and record.unit != "token":
        return "missing_token_quantity"
    return "cost_basis_unavailable"


def _record_provider(record: UsageRecord) -> str:
    return (record.provider or "").strip().lower()


def _cost_mismatch_exceeds_tolerance(*, old_cost_cents: int, new_cost_cents: int) -> bool:
    diff = abs(int(old_cost_cents) - int(new_cost_cents))
    if diff < _MISMATCH_MIN_ABSOLUTE_CENTS:
        return False
    denominator = max(abs(int(new_cost_cents)), 1)
    return (Decimal(diff) / Decimal(denominator)) > _MISMATCH_RELATIVE_TOLERANCE


def _summary_item(
    record: UsageRecord,
    *,
    old_cost_cents: int,
    new_cost_cents: int,
    basis: str,
    reason: str | None = None,
) -> dict[str, Any]:
    item = {
        "usage_record_id": record.id,
        "tenant_id": record.tenant_id,
        "video_task_id": record.video_task_id,
        "provider": record.provider,
        "model": record.model,
        "unit": record.unit,
        "quantity": str(record.quantity),
        "old_cost_cents": old_cost_cents,
        "new_cost_cents": new_cost_cents,
        "basis": basis,
    }
    if reason:
        item["reason"] = reason
    return item


def backfill_provider_zero_costs(
    db: Session,
    *,
    apply: bool,
    limit: int | None = None,
) -> BackfillSummary:
    query = (
        select(UsageRecord)
        .where(
            UsageRecord.status == "settled",
            or_(
                UsageRecord.provider == _APIMART_PROVIDER,
                and_(
                    UsageRecord.provider.in_(_NON_APIMART_ZERO_COST_PROVIDERS),
                    UsageRecord.cost_cents == 0,
                ),
            ),
        )
        .order_by(UsageRecord.created_at.asc(), UsageRecord.id.asc())
    )
    preview: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    updated = 0
    for record in db.scalars(query):
        task = db.get(VideoTask, record.video_task_id) if record.video_task_id else None
        new_cost_cents, basis = _cost_for_record(record, task)
        old_cost_cents = int(record.cost_cents or 0)
        if new_cost_cents <= 0:
            skipped.append(
                {
                    "usage_record_id": record.id,
                    "tenant_id": record.tenant_id,
                    "video_task_id": record.video_task_id,
                    "provider": record.provider,
                    "model": record.model,
                    "unit": record.unit,
                    "quantity": str(record.quantity),
                    "reason": _skip_reason(record),
                }
            )
            continue
        provider = _record_provider(record)
        if (
            provider == _APIMART_PROVIDER
            and old_cost_cents > 0
            and not _cost_mismatch_exceeds_tolerance(
                old_cost_cents=old_cost_cents,
                new_cost_cents=new_cost_cents,
            )
        ):
            unchanged.append(
                _summary_item(
                    record,
                    old_cost_cents=old_cost_cents,
                    new_cost_cents=new_cost_cents,
                    basis=basis,
                    reason="within_tolerance",
                )
            )
            continue

        item = _summary_item(
            record,
            old_cost_cents=old_cost_cents,
            new_cost_cents=new_cost_cents,
            basis=basis,
        )
        if limit is not None and len(preview) >= limit:
            continue
        preview.append(item)
        if provider == _APIMART_PROVIDER and old_cost_cents > 0:
            anomalies.append({**item, "reason": "apimart_nonzero_mismatch"})
        if apply:
            record.cost_cents = new_cost_cents
            updated += 1

    return BackfillSummary(
        matched=len(preview),
        updated=updated,
        apply=apply,
        preview=preview,
        skipped=skipped,
        unchanged=unchanged,
        anomalies=anomalies,
    )


def backfill_apimart_zero_costs(
    db: Session,
    *,
    apply: bool,
    limit: int | None = None,
) -> BackfillSummary:
    return backfill_provider_zero_costs(db, apply=apply, limit=limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill settled provider UsageRecord.cost_cents from price tables."
    )
    parser.add_argument("--apply", action="store_true", help="write changes; default is dry-run")
    parser.add_argument("--limit", type=int, default=None, help="maximum candidate rows")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        summary = backfill_provider_zero_costs(db, apply=args.apply, limit=args.limit)
        if args.apply:
            db.commit()
        else:
            db.rollback()
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
