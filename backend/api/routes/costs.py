"""Costs API — the Claude usage ledger (2026-11-01).

Rows are written by analysis/llm_usage.py, one per tracked call. This
router serves the Costs page: aggregates (today / this month / all time /
a chosen range, by feature, by model, by day) and the per-call list.

All times are UTC. `from` and `to` are inclusive calendar dates
(YYYY-MM-DD). Costs are USD, computed at call time from the rates in
config.py and stored on the row.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Date, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from database.models import LlmCall

router = APIRouter()


def _parse_range(from_: Optional[str], to: Optional[str]) -> tuple[Optional[datetime], Optional[datetime]]:
    try:
        start = datetime.fromisoformat(from_) if from_ else None
        end = datetime.fromisoformat(to) + timedelta(days=1) if to else None
    except ValueError:
        raise HTTPException(status_code=400, detail="from / to must be YYYY-MM-DD")
    return start, end


def _scope(stmt, start, end, feature: Optional[str] = None, status: Optional[str] = None):
    if start is not None:
        stmt = stmt.where(LlmCall.created_at >= start)
    if end is not None:
        stmt = stmt.where(LlmCall.created_at < end)
    if feature:
        stmt = stmt.where(LlmCall.feature == feature)
    if status:
        stmt = stmt.where(LlmCall.status == status)
    return stmt


_SUMS = (
    func.count(LlmCall.id),
    func.coalesce(func.sum(LlmCall.cost_usd), 0.0),
    func.coalesce(func.sum(LlmCall.input_tokens), 0),
    func.coalesce(func.sum(LlmCall.output_tokens), 0),
    func.coalesce(func.sum(LlmCall.cache_read_tokens), 0),
    func.coalesce(func.sum(LlmCall.cache_write_tokens), 0),
    func.coalesce(func.sum(LlmCall.web_searches), 0),
    func.coalesce(func.sum(LlmCall.web_fetches), 0),
    func.count(LlmCall.id).filter(LlmCall.status == "error"),
)


def _sums(row, offset: int = 0) -> dict:
    return {
        "calls": int(row[offset]),
        "cost_usd": round(float(row[offset + 1]), 4),
        "input_tokens": int(row[offset + 2]),
        "output_tokens": int(row[offset + 3]),
        "cache_read_tokens": int(row[offset + 4]),
        "cache_write_tokens": int(row[offset + 5]),
        "web_searches": int(row[offset + 6]),
        "web_fetches": int(row[offset + 7]),
        "errors": int(row[offset + 8]),
    }


@router.get("/summary")
async def summary(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Totals for today / this month / all time / the requested range, plus
    breakdowns by feature, by model and by day inside the range (the last
    30 days when no range is given)."""
    start, end = _parse_range(from_, to)
    now = datetime.utcnow()
    today = datetime(now.year, now.month, now.day)
    month = datetime(now.year, now.month, 1)

    async def totals(s, e) -> dict:
        row = (await db.execute(_scope(select(*_SUMS), s, e))).one()
        return _sums(row)

    by_feature = (await db.execute(
        _scope(select(LlmCall.feature, *_SUMS), start, end)
        .group_by(LlmCall.feature)
        .order_by(desc(func.sum(LlmCall.cost_usd)))
    )).all()
    by_model = (await db.execute(
        _scope(select(LlmCall.model, *_SUMS), start, end)
        .group_by(LlmCall.model)
        .order_by(desc(func.sum(LlmCall.cost_usd)))
    )).all()
    day_col = cast(LlmCall.created_at, Date)
    day_start = start if start is not None else today - timedelta(days=29)
    by_day = (await db.execute(
        _scope(
            select(day_col, func.count(LlmCall.id), func.coalesce(func.sum(LlmCall.cost_usd), 0.0)),
            day_start, end,
        ).group_by(day_col).order_by(day_col)
    )).all()

    return {
        "timezone": "UTC",
        "range": {"from": from_, "to": to},
        "totals": {
            "today": await totals(today, None),
            "this_month": await totals(month, None),
            "all_time": await totals(None, None),
            "range": await totals(start, end),
        },
        "by_feature": [{"feature": r[0], **_sums(r, 1)} for r in by_feature],
        "by_model": [{"model": r[0], **_sums(r, 1)} for r in by_model],
        "by_day": [
            {"day": r[0].isoformat(), "calls": int(r[1]), "cost_usd": round(float(r[2]), 4)}
            for r in by_day
        ],
    }


@router.get("/calls")
async def calls(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    feature: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """The per-call list, newest first."""
    start, end = _parse_range(from_, to)
    total = (await db.execute(
        _scope(select(func.count(LlmCall.id)), start, end, feature, status)
    )).scalar_one()
    rows = (await db.execute(
        _scope(select(LlmCall), start, end, feature, status)
        .order_by(desc(LlmCall.created_at), desc(LlmCall.id))
        .limit(limit).offset(offset)
    )).scalars().all()
    return {
        "total": int(total),
        "rows": [
            {
                "id": c.id,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "feature": c.feature,
                "model": c.model,
                "ref_type": c.ref_type,
                "ref_id": c.ref_id,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "cache_read_tokens": c.cache_read_tokens,
                "cache_write_tokens": c.cache_write_tokens,
                "web_searches": c.web_searches,
                "web_fetches": c.web_fetches,
                "duration_ms": c.duration_ms,
                "status": c.status,
                "error": c.error,
                "cost_usd": round(float(c.cost_usd or 0.0), 6),
                "rates_source": (c.rates or {}).get("source"),
            }
            for c in rows
        ],
    }
