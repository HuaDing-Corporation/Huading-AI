from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
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


_BACKFILL_PROVIDERS = {
    "apimart",
    "omnihuman",
    "doubao-seed-tts",
    "deepseek",
}


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
    if provider == "apimart":
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


def backfill_provider_zero_costs(
    db: Session,
    *,
    apply: bool,
    limit: int | None = None,
) -> BackfillSummary:
    query = (
        select(UsageRecord)
        .where(
            UsageRecord.provider.in_(_BACKFILL_PROVIDERS),
            UsageRecord.status == "settled",
            UsageRecord.cost_cents == 0,
        )
        .order_by(UsageRecord.created_at.asc(), UsageRecord.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)

    preview: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    updated = 0
    for record in db.scalars(query):
        task = db.get(VideoTask, record.video_task_id) if record.video_task_id else None
        new_cost_cents, basis = _cost_for_record(record, task)
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
        preview.append(
            {
                "usage_record_id": record.id,
                "tenant_id": record.tenant_id,
                "video_task_id": record.video_task_id,
                "provider": record.provider,
                "model": record.model,
                "unit": record.unit,
                "quantity": str(record.quantity),
                "old_cost_cents": record.cost_cents,
                "new_cost_cents": new_cost_cents,
                "basis": basis,
            }
        )
        if apply:
            record.cost_cents = new_cost_cents
            updated += 1

    return BackfillSummary(
        matched=len(preview),
        updated=updated,
        apply=apply,
        preview=preview,
        skipped=skipped,
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
