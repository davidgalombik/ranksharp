"""ALDI web product recommendations — API (Phase 3, 2026-11-01).

Mounted under /api/aldi next to the session routes. A run is one click of
"Product Recommendations" or "Try Again" for one team on one session; the
pipeline itself runs in the worker (tasks/aldi_recommend_tasks.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from analysis.aldi_recommender import PREFERRED_RETAILERS, TEAMS, TIER_LABEL
from database.db import get_db
from database.models import (
    AldiPreferredRetailer, AldiRecommendation, AldiRecommendationFeedback, AldiRecommendationRun,
    AldiSession, AldiTeamPreference, AldiUploadStatus,
)

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────────────

class StartRunBody(BaseModel):
    team: str


class FeedbackBody(BaseModel):
    verdict: str          # good | bad
    note: Optional[str] = None


class PreferencesBody(BaseModel):
    memo: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None


class RetailerBody(BaseModel):
    name: str
    domain: Optional[str] = None
    tier: str
    priority: str = "normal"
    country: str = "US"
    active: bool = True


def _team(team: str) -> str:
    t = (team or "").strip().lower()
    if t not in TEAMS:
        raise HTTPException(status_code=400, detail=f"team must be one of {sorted(TEAMS)}")
    return t


def _rec_out(r: AldiRecommendation) -> dict:
    return {
        "id": r.id, "kind": r.kind or "pick", "fails": r.fails, "note": r.how_it_matches if (r.kind == "near_miss") else None,
        "position": r.position, "product_name": r.product_name, "retailer": r.retailer,
        "retailer_tier": r.retailer_tier, "url": r.url, "material_family": r.material_family,
        "theme_id": r.theme_id, "theme_name": r.theme_name, "evidence": r.evidence or [],
        "how_it_matches": r.how_it_matches, "verification_status": r.verification_status,
        "thumbnail_url": f"/api/aldi/recommendations/{r.id}/thumbnail" if r.thumbnail else r.thumbnail_url,
        "feedback": [{"id": f.id, "verdict": f.verdict, "note": f.note, "created_at": f.created_at.isoformat()}
                     for f in (r.feedback or [])],
    }


def _run_out(run: AldiRecommendationRun) -> dict:
    return {
        "id": run.id, "team": run.team, "generation": run.generation, "status": run.status, "stage": run.stage,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "cost_usd": round(float(run.cost_usd or 0.0), 4), "searches": run.searches, "fetches": run.fetches,
        "error": run.error, "stats": run.stats or {},
        "recommendations": [_rec_out(r) for r in sorted(run.recommendations or [], key=lambda x: x.position)
                            if (r.kind or "pick") == "pick"],
        # Near misses are rows since 2026-11-04 (feedback-able); older runs
        # only have the JSON list, served as-is without ids.
        "near_misses": ([_rec_out(r) for r in sorted(run.recommendations or [], key=lambda x: x.position)
                         if r.kind == "near_miss"] or (run.near_misses or [])),
    }


async def _prefs(db: AsyncSession, team: str) -> AldiTeamPreference:
    row = await db.get(AldiTeamPreference, team)
    if not row:
        row = AldiTeamPreference(team=team, memo="", price_min=15.0, price_max=60.0, updated_at=datetime.utcnow())
        db.add(row)
        await db.flush()
    return row


# ── Runs ─────────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/recommendations", status_code=202)
async def start_run(session_id: int, body: StartRunBody, db: AsyncSession = Depends(get_db)):
    """Queue a recommendation run for one team. Builds the session's trend
    digest first if it is missing (sessions analysed before Phase 2)."""
    team = _team(body.team)
    sess = (await db.execute(
        select(AldiSession).options(selectinload(AldiSession.uploads)).where(AldiSession.id == session_id)
    )).scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if not any(u.status == AldiUploadStatus.DONE for u in sess.uploads):
        raise HTTPException(status_code=409, detail="The report has not finished analysing yet")

    in_flight = (await db.execute(
        select(AldiRecommendationRun).where(
            AldiRecommendationRun.session_id == session_id, AldiRecommendationRun.team == team,
            AldiRecommendationRun.status.in_(["queued", "running"]))
    )).scalars().first()
    if in_flight:
        raise HTTPException(status_code=409, detail=f"A run is already in progress for {TEAMS[team]['label']}")

    if not (sess.trend_digest or {}).get("themes"):
        from api.routes.aldi import rebuild_trend_digest
        await rebuild_trend_digest(session_id, force=False, db=db)
        sess = await db.get(AldiSession, session_id)
        if not (sess.trend_digest or {}).get("themes"):
            raise HTTPException(status_code=502, detail="Could not build the trend digest for this report")

    gen = (await db.execute(
        select(func.coalesce(func.max(AldiRecommendationRun.generation), 0)).where(
            AldiRecommendationRun.session_id == session_id, AldiRecommendationRun.team == team)
    )).scalar_one() + 1
    run = AldiRecommendationRun(session_id=session_id, team=team, generation=gen, status="queued")
    db.add(run)
    await db.commit()
    await db.refresh(run)

    from tasks.aldi_recommend_tasks import recommend_aldi_products
    try:
        recommend_aldi_products.delay(run.id)
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = f"Could not queue the run: {exc}"
        await db.commit()
        raise HTTPException(status_code=503, detail="Worker queue unavailable — try again in a minute")
    return _run_out(run)


@router.get("/sessions/{session_id}/recommendations")
async def list_runs(session_id: int, db: AsyncSession = Depends(get_db)):
    """Both reels: every run for every team, newest generation first, with
    picks, feedback, near misses and cost; plus each team's preferences."""
    if not await db.get(AldiSession, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    runs = (await db.execute(
        select(AldiRecommendationRun)
        .options(selectinload(AldiRecommendationRun.recommendations).selectinload(AldiRecommendation.feedback))
        .where(AldiRecommendationRun.session_id == session_id)
        .order_by(desc(AldiRecommendationRun.generation))
    )).scalars().all()
    out = {}
    for key, cfg in TEAMS.items():
        prefs = await _prefs(db, key)
        team_runs = [r for r in runs if r.team == key]
        out[key] = {
            "label": cfg["label"], "families": cfg["families"],
            "preferences": {"memo": prefs.memo, "price_min": prefs.price_min, "price_max": prefs.price_max},
            "runs": [_run_out(r) for r in team_runs],
            "latest": _run_out(team_runs[0]) if team_runs else None,
        }
    await db.commit()
    return {"session_id": session_id, "teams": out}


@router.get("/recommendation-runs/{run_id}")
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)):
    run = (await db.execute(
        select(AldiRecommendationRun)
        .options(selectinload(AldiRecommendationRun.recommendations).selectinload(AldiRecommendation.feedback))
        .where(AldiRecommendationRun.id == run_id)
    )).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_out(run)


@router.get("/recommendations/{rec_id}/thumbnail")
async def thumbnail(rec_id: int, db: AsyncSession = Depends(get_db)):
    r = await db.get(AldiRecommendation, rec_id)
    if not r or not r.thumbnail:
        raise HTTPException(status_code=404, detail="No thumbnail")
    return Response(content=bytes(r.thumbnail), media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


# ── Feedback ─────────────────────────────────────────────────────────────────

@router.post("/recommendations/{rec_id}/feedback")
async def add_feedback(rec_id: int, body: FeedbackBody, db: AsyncSession = Depends(get_db)):
    verdict = (body.verdict or "").strip().lower()
    if verdict not in ("good", "bad"):
        raise HTTPException(status_code=400, detail="verdict must be 'good' or 'bad'")
    r = (await db.execute(
        select(AldiRecommendation).options(selectinload(AldiRecommendation.feedback))
        .where(AldiRecommendation.id == rec_id)
    )).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    fb = AldiRecommendationFeedback(recommendation_id=rec_id, verdict=verdict,
                                    note=(body.note or "").strip()[:1000] or None)
    db.add(fb)
    await db.commit()
    await db.refresh(r)
    return _rec_out(r)


@router.delete("/recommendation-feedback/{feedback_id}")
async def delete_feedback(feedback_id: int, db: AsyncSession = Depends(get_db)):
    fb = await db.get(AldiRecommendationFeedback, feedback_id)
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback not found")
    await db.delete(fb)
    await db.commit()
    return {"deleted": feedback_id}


# ── Team preferences ─────────────────────────────────────────────────────────

@router.get("/teams/{team}/preferences")
async def get_preferences(team: str, db: AsyncSession = Depends(get_db)):
    t = _team(team)
    p = await _prefs(db, t)
    await db.commit()
    return {"team": t, "label": TEAMS[t]["label"], "families": TEAMS[t]["families"],
            "memo": p.memo, "price_min": p.price_min, "price_max": p.price_max,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


@router.put("/teams/{team}/preferences")
async def put_preferences(team: str, body: PreferencesBody, db: AsyncSession = Depends(get_db)):
    t = _team(team)
    p = await _prefs(db, t)
    if body.memo is not None:
        p.memo = body.memo.strip()[:4000]
    if body.price_min is not None:
        p.price_min = max(0.0, float(body.price_min))
    if body.price_max is not None:
        p.price_max = max(0.0, float(body.price_max))
    if p.price_max < p.price_min:
        raise HTTPException(status_code=400, detail="price_max must be at least price_min")
    p.updated_at = datetime.utcnow()
    await db.commit()
    return await get_preferences(t, db)


# ── Preferred retailers ──────────────────────────────────────────────────────

@router.get("/preferred-retailers")
async def list_preferred_retailers(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AldiPreferredRetailer).order_by(AldiPreferredRetailer.tier, AldiPreferredRetailer.name))).scalars().all()
    return {
        "tiers": TIER_LABEL,
        "retailers": [{"id": r.id, "name": r.name, "domain": r.domain, "tier": r.tier, "priority": r.priority,
                       "country": r.country, "active": r.active} for r in rows],
    }


@router.put("/preferred-retailers")
async def put_preferred_retailers(body: list[RetailerBody], db: AsyncSession = Depends(get_db)):
    """Upsert by name. Retailers not in the body are left as they are."""
    for item in body:
        if item.tier not in TIER_LABEL:
            raise HTTPException(status_code=400, detail=f"unknown tier {item.tier!r}")
        row = (await db.execute(select(AldiPreferredRetailer).where(AldiPreferredRetailer.name == item.name.strip()))).scalar_one_or_none()
        if row is None:
            row = AldiPreferredRetailer(name=item.name.strip())
            db.add(row)
        row.domain = (item.domain or "").strip().lower() or None
        row.tier = item.tier
        row.priority = item.priority if item.priority in ("high", "normal", "low") else "normal"
        row.country = (item.country or "US")[:2].upper()
        row.active = bool(item.active)
    await db.commit()
    return await list_preferred_retailers(db)
