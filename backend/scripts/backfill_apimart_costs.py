from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.models import UsageRecord
from app.db.session import SessionLocal
from app.services.apimart_costs import apimart_cost_cents_from_credits


@dataclass
class BackfillSummary:
    matched: int
    updated: int
    apply: bool
    preview: list[dict[str, Any]]


def backfill_apimart_zero_costs(
    db: Session,
    *,
    apply: bool,
    limit: int | None = None,
) -> BackfillSummary:
    query = (
        select(UsageRecord)
        .where(
            UsageRecord.provider == "apimart",
            UsageRecord.status == "settled",
            UsageRecord.cost_cents == 0,
            UsageRecord.credits > 0,
        )
        .order_by(UsageRecord.created_at.asc(), UsageRecord.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)

    records = list(db.scalars(query))
    preview: list[dict[str, Any]] = []
    updated = 0
    for record in records:
        new_cost_cents = apimart_cost_cents_from_credits(record.credits)
        if new_cost_cents <= 0:
            continue
        preview.append(
            {
                "usage_record_id": record.id,
                "tenant_id": record.tenant_id,
                "video_task_id": record.video_task_id,
                "credits": str(record.credits),
                "old_cost_cents": record.cost_cents,
                "new_cost_cents": new_cost_cents,
            }
        )
        if apply:
            record.cost_cents = new_cost_cents
            updated += 1

    return BackfillSummary(
        matched=len(records),
        updated=updated,
        apply=apply,
        preview=preview,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill settled APIMart UsageRecord.cost_cents from credits."
    )
    parser.add_argument("--apply", action="store_true", help="write changes; default is dry-run")
    parser.add_argument("--limit", type=int, default=None, help="maximum candidate rows")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        summary = backfill_apimart_zero_costs(db, apply=args.apply, limit=args.limit)
        if args.apply:
            db.commit()
        else:
            db.rollback()
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
