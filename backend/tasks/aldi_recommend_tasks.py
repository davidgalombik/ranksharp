"""Celery task + orchestrator for the ALDI web product recommender (Phase 3).

The stages live in analysis/aldi_recommender.py; this module owns the
database side: loading the run's context, persisting the candidate pool,
the picks, near misses, thumbnails and the run's cost.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import structlog
from sqlalchemy import select as sa_select, text, func

from analysis import aldi_recommender as rec
from config import settings
from database.models import (
    AldiCandidate, AldiPreferredRetailer, AldiRecommendation, AldiRecommendationFeedback,
    AldiRecommendationRun, AldiSession, AldiTeamPreference,
)
from tasks.aldi_tasks import _get_session
from tasks.celery_app import app

log = structlog.get_logger()

PICKS = 5


def _set_stage(db, run: AldiRecommendationRun, stage: str) -> None:
    run.stage = stage
    db.commit()
    log.info("aldi_recommend_stage", run_id=run.id, stage=stage)


def load_context(db, run: AldiRecommendationRun) -> rec.RunContext:
    sess = db.get(AldiSession, run.session_id)
    if not sess or not sess.trend_digest or not (sess.trend_digest.get("themes")):
        raise RuntimeError("session has no trend digest — build it first")
    digest = dict(sess.trend_digest)
    # Attach the per-upload element inventories so citations can be validated and described.
    digest["pages"] = [
        {"upload_id": u.id, "page": p.get("page"), "elements": u.elements or []}
        for p in (digest.get("pages") or [])
        for u in [next((x for x in sess.uploads if x.id == p["upload_id"]), None)] if u
    ]
    prefs_row = db.get(AldiTeamPreference, run.team)
    prefs = {"memo": prefs_row.memo if prefs_row else "", "price_min": float(prefs_row.price_min if prefs_row else 15.0),
             "price_max": float(prefs_row.price_max if prefs_row else 60.0),
             "learned_rules": (prefs_row.learned_rules if prefs_row else "") or ""}
    retailers = [
        {"name": r.name, "domain": r.domain, "tier": r.tier, "priority": r.priority, "active": r.active}
        for r in db.execute(sa_select(AldiPreferredRetailer)).scalars().all()
    ] or rec.PREFERRED_RETAILERS
    shown_rows = db.execute(
        sa_select(AldiRecommendation).where(AldiRecommendation.session_id == run.session_id,
                                            AldiRecommendation.team == run.team)
        .order_by(AldiRecommendation.id)
    ).scalars().all()
    # Picks are "shown" (never repeated). Near misses are not — a Good on one
    # pre-approves it for the next run, a Bad excludes it. (2026-11-04)
    shown = [{"product_name": s.product_name, "url": s.url, "url_hash": rec.url_hash(s.url), "retailer": s.retailer}
             for s in shown_rows if (s.kind or "pick") == "pick"]
    feedback: list[dict] = []
    approved: list[dict] = []
    rejected: list[dict] = []
    for s in shown_rows:
        fbs = list(s.feedback or [])
        for fb in fbs:
            feedback.append({"verdict": fb.verdict, "note": fb.note, "product_name": s.product_name,
                             "retailer": s.retailer, "kind": s.kind or "pick", "fails": s.fails})
        if s.kind == "near_miss" and fbs:
            latest = fbs[-1]
            if latest.verdict == "good" and s.candidate_id:
                approved.append({"candidate_id": int(s.candidate_id), "product_name": s.product_name,
                                 "retailer": s.retailer, "fails": s.fails or "a criterion", "note": latest.note})
            elif latest.verdict == "bad":
                rejected.append({"product_name": s.product_name, "retailer": s.retailer, "url": s.url,
                                 "url_hash": rec.url_hash(s.url)})
    previous_queries = [
        q.get("query") for r in db.execute(
            sa_select(AldiRecommendationRun).where(AldiRecommendationRun.session_id == run.session_id,
                                                   AldiRecommendationRun.team == run.team,
                                                   AldiRecommendationRun.id != run.id)
        ).scalars().all() for q in (r.search_queries or []) if isinstance(q, dict) and q.get("query")
    ]
    # Phase 5 rotation: which retailers earlier queries for this report targeted.
    searched: dict[str, int] = {}
    if previous_queries:
        import re as _re
        for r in retailers:
            pat = _re.compile(r"\b" + _re.escape(r["name"]) + r"\b", _re.I)
            n = sum(1 for q in previous_queries if pat.search(q))
            if n:
                searched[r["name"]] = n
    return rec.RunContext(run_id=run.id, session_id=run.session_id, team=run.team, digest=digest, prefs=prefs,
                          retailers=retailers, shown=shown, feedback=feedback, previous_queries=previous_queries,
                          approved=approved, rejected=rejected, searched_retailers=searched)


def _upsert_candidates(db, run: AldiRecommendationRun, found: list[dict]) -> None:
    for c in found:
        exists = db.execute(sa_select(AldiCandidate.id).where(
            AldiCandidate.session_id == run.session_id, AldiCandidate.team == run.team,
            AldiCandidate.url_hash == c["url_hash"])).scalar()
        if exists:
            continue
        db.add(AldiCandidate(
            session_id=run.session_id, team=run.team, url=c["url"], url_hash=c["url_hash"], domain=c["domain"],
            title=c.get("title"), snippet=c.get("snippet"), source_query=c.get("source_query"),
            theme_id=c.get("theme_id"), fit_score=c.get("fit"), first_run_id=run.id,
        ))
    db.commit()


def _pool(db, run: AldiRecommendationRun) -> list[AldiCandidate]:
    return db.execute(sa_select(AldiCandidate).where(
        AldiCandidate.session_id == run.session_id, AldiCandidate.team == run.team)).scalars().all()


def _as_dict(c: AldiCandidate) -> dict:
    return {"id": c.id, "url": c.url, "url_hash": c.url_hash, "domain": c.domain or "", "title": c.title or "",
            "snippet": c.snippet or "", "theme_id": c.theme_id, "fit": c.fit_score or 0.0,
            "status": c.verify_status, "facts": c.facts or {}, "og_image": c.og_image}


async def _select_round(db, run, ctx, shown_hashes: set[str]) -> tuple[list[dict], list[dict], dict[int, dict], int]:
    """Select + enforce over the whole verified pool. Returns
    (accepted picks, near misses, verified_by_id, verified pool size)."""
    verified = [_as_dict(c) for c in _pool(db, run)
                if c.verify_status == "verified" and c.url_hash not in shown_hashes]
    verified_by_id = {c["id"]: c for c in verified}
    if not verified:
        return [], [], verified_by_id, 0
    first = await rec.select(ctx, verified, PICKS)
    accepted, rejected = rec.enforce_rules(ctx, first["picks"], verified_by_id, shown_hashes)
    near_misses = first["near_misses"] + [{"candidate_id": r.get("candidate_id"), "fails": r["fails"], "note": "rejected by rule check"} for r in rejected]
    gap = rec.spread_gap(ctx, accepted, PICKS)
    remaining = [c for c in verified if c["id"] not in {a["candidate_id"] for a in accepted}
                 and c["id"] not in {r.get("candidate_id") for r in rejected}]
    if gap and remaining:
        _set_stage(db, run, "repairing")
        repair = await rec.select(ctx, remaining, PICKS - len(accepted) if len(accepted) < PICKS else 2,
                                  fixed=accepted, gap_note=f" that add {gap}")
        more, rejected2 = rec.enforce_rules(ctx, repair["picks"], verified_by_id, shown_hashes)
        counts: dict[str, int] = {}
        for a in accepted:
            counts[a["retailer"]] = counts.get(a["retailer"], 0) + 1
        for m in more:
            if len(accepted) >= PICKS:
                break
            if counts.get(m["retailer"], 0) >= 3:
                continue
            counts[m["retailer"]] = counts.get(m["retailer"], 0) + 1
            accepted.append(m)
        near_misses += repair["near_misses"] + [{"candidate_id": r.get("candidate_id"), "fails": r["fails"], "note": "rejected by rule check"} for r in rejected2]
    return accepted[:PICKS], near_misses, verified_by_id, len(verified)


async def run_pipeline(db, run: AldiRecommendationRun) -> None:
    ctx = load_context(db, run)
    shown_hashes = {s["url_hash"] for s in ctx.shown} | {r["url_hash"] for r in ctx.rejected}
    stats: dict = {"rounds": 0, "queries": 0, "found": 0, "to_verify": 0, "verified_now": 0, "blocked_now": 0}
    all_queries: list[dict] = []
    picks_out: list[dict] = []
    near_misses: list[dict] = []
    verified_by_id: dict[int, dict] = {}
    hints: list[str] = []

    for round_no in range(1, max(1, settings.aldi_recommend_rounds) + 1):
        stats["rounds"] = round_no
        _set_stage(db, run, "planning" if round_no == 1 else "planning again")
        queries = await rec.plan_queries(ctx, settings.aldi_recommend_queries, hints=hints or None)
        all_queries += queries
        run.search_queries = all_queries
        db.commit()
        if not queries:
            if round_no == 1:
                raise RuntimeError("query planning returned nothing")
            break
        # Later rounds must not repeat this run's own earlier queries either.
        ctx.previous_queries = ctx.previous_queries + [q["query"] for q in queries]

        _set_stage(db, run, "discovering")
        found = await rec.discover(ctx, queries)
        _upsert_candidates(db, run, found)
        pool = _pool(db, run)
        stats["queries"] += len(queries)
        stats["found"] += len(found)
        stats["pool"] = len(pool)

        _set_stage(db, run, "ranking")
        pending = [_as_dict(c) for c in pool if c.verify_status == "pending" and c.url_hash not in shown_hashes]
        to_verify = await rec.rank(ctx, pending, settings.aldi_recommend_verify)
        stats["to_verify"] += len(to_verify)

        _set_stage(db, run, "verifying")
        results = await rec.verify(ctx, to_verify)
        by_id = {c.id: c for c in pool}
        for cid, r in results.items():
            c = by_id.get(cid)
            if not c:
                continue
            c.verify_status = r["status"]
            c.facts = r["facts"]
            c.og_image = r.get("og_image")
            c.verified_at = datetime.utcnow()
        db.commit()
        stats["verified_now"] += sum(1 for r in results.values() if r["status"] == "verified")
        stats["blocked_now"] += sum(1 for r in results.values() if r["status"] in ("blocked", "dead"))

        _set_stage(db, run, "selecting")
        picks_out, near_misses, verified_by_id, stats["verified_pool"] = await _select_round(db, run, ctx, shown_hashes)
        if len(picks_out) >= PICKS:
            break
        # Steer the next round with what failed this time.
        pool_by_id = {c.id: c for c in _pool(db, run)}
        hints = []
        for m in near_misses:
            try:
                c = pool_by_id.get(int(m.get("candidate_id")))
            except Exception:  # noqa: BLE001
                c = None
            name = ((c.facts or {}).get("product_name") if c else None) or (c.title if c else "?")
            hints.append(f"{name[:70]} — {m.get('fails')}")
        log.info("aldi_recommend_round_short", run_id=run.id, round=round_no, picks=len(picks_out))

    _set_stage(db, run, "saving")
    elements = rec.element_lookup(ctx.digest)
    themes = {t["id"]: t for t in ctx.digest.get("themes") or []}
    for pos, p in enumerate(picks_out, 1):
        c = verified_by_id[p["candidate_id"]]
        f = c["facts"]
        theme = themes.get(str(p.get("theme_id") or ""))
        refs = rec.validate_refs(p.get("element_refs"), elements, theme)
        thumb = await rec.fetch_thumbnail(c.get("og_image") or f.get("image_url"))
        db.add(AldiRecommendation(
            run_id=run.id, session_id=run.session_id, team=run.team, position=pos, candidate_id=c["id"],
            product_name=(f.get("product_name") or c["title"] or c["url"])[:300],
            retailer=p.get("retailer"), retailer_tier=p.get("tier"), url=c["url"],
            price_usd=f.get("price_usd"), material_family=(p.get("material_family") or f.get("primary_family")),
            theme_id=theme["id"] if theme else None, theme_name=theme["name"] if theme else None,
            evidence=refs, how_it_matches=(p.get("how_it_matches") or "")[:1200] or None,
            demand_evidence=(p.get("demand_evidence") or "")[:300] or None,
            verification_status="verified", page_title=f.get("product_name"),
            thumbnail=thumb, thumbnail_url=(c.get("og_image") or f.get("image_url")),
        ))
    # Near misses: name them for the UI.
    pool_by_id = {c.id: c for c in _pool(db, run)}
    named = []
    seen_miss: set[int] = set()
    chosen_ids = {p["candidate_id"] for p in picks_out}
    for m in near_misses:
        if len(named) >= 12:
            break
        try:
            cid = int(m.get("candidate_id"))
        except Exception:  # noqa: BLE001
            continue
        c = pool_by_id.get(cid)
        if not c or cid in seen_miss or cid in chosen_ids:
            continue
        seen_miss.add(cid)
        named.append({"candidate_id": cid, "product_name": (c.facts or {}).get("product_name") or c.title,
                      "retailer": (c.facts or {}).get("retailer") or c.domain, "url": c.url,
                      "fails": str(m.get("fails") or "")[:120], "note": str(m.get("note") or "")[:200]})
    # Near misses as rows of their own, so the buyer can mark them too.
    for pos, m in enumerate(named, 1):
        c = pool_by_id[m["candidate_id"]]
        f = c.facts or {}
        name, tier = rec.retailer_for_domain(c.domain or "", ctx.retailers)
        thumb = await rec.fetch_thumbnail(c.og_image or f.get("image_url"))
        db.add(AldiRecommendation(
            run_id=run.id, session_id=run.session_id, team=run.team, kind="near_miss", position=pos,
            candidate_id=c.id, product_name=(m["product_name"] or c.url)[:300],
            retailer=(m["retailer"] or name)[:120] if (m["retailer"] or name) else None, retailer_tier=tier,
            url=c.url, price_usd=f.get("price_usd"), material_family=f.get("primary_family"),
            fails=m["fails"] or None, how_it_matches=m["note"] or None,
            verification_status=c.verify_status, page_title=f.get("product_name"),
            thumbnail=thumb, thumbnail_url=(c.og_image or f.get("image_url")),
        ))
    run.near_misses = named
    run.stats = stats
    row = db.execute(text(
        "select coalesce(sum(cost_usd),0), coalesce(sum(web_searches),0), coalesce(sum(web_fetches),0) "
        "from llm_calls where ref_type='aldi_run' and ref_id=:rid"), {"rid": run.id}).one()
    run.cost_usd, run.searches, run.fetches = float(row[0]), int(row[1]), int(row[2])
    run.status = "done"
    run.stage = None
    run.finished_at = datetime.utcnow()
    db.commit()
    log.info("aldi_recommend_done", run_id=run.id, picks=len(picks_out), near_misses=len(named),
             cost=run.cost_usd, stats=stats)


@app.task(bind=True, max_retries=0)
def recommend_aldi_products(self, run_id: int) -> dict:
    db = _get_session()
    try:
        run = db.get(AldiRecommendationRun, run_id)
        if not run:
            return {"status": "not_found", "run_id": run_id}
        if run.status not in ("queued", "failed"):
            return {"status": "skipped", "run_id": run_id, "current": run.status}
        run.status = "running"
        run.started_at = datetime.utcnow()
        run.model = settings.aldi_recommend_model
        run.prompt_version = settings.aldi_recommend_prompt_version
        db.commit()
        try:
            asyncio.run(run_pipeline(db, run))
        except Exception as exc:  # noqa: BLE001
            import traceback
            log.error("aldi_recommend_failed", run_id=run_id, error=str(exc), traceback=traceback.format_exc())
            db.rollback()
            run = db.get(AldiRecommendationRun, run_id)
            run.status = "failed"
            run.stage = None
            run.error = f"{type(exc).__name__}: {exc}"[:1000]
            run.finished_at = datetime.utcnow()
            row = db.execute(text("select coalesce(sum(cost_usd),0) from llm_calls where ref_type='aldi_run' and ref_id=:rid"), {"rid": run_id}).one()
            run.cost_usd = float(row[0])
            db.commit()
            return {"status": "failed", "run_id": run_id}
        return {"status": "done", "run_id": run_id}
    finally:
        db.close()


# ── Phase 5: distillation ────────────────────────────────────────────────────

def feedback_items_for_team(db, team: str) -> list[dict]:
    """Every Good / Bad this team has ever given, oldest first, with the
    product facts the distiller needs."""
    # Read from the feedback rows alone — they carry the product details and
    # survive the deletion of the report, run or recommendation (2026-11-04).
    rows = db.execute(
        sa_select(AldiRecommendationFeedback)
        .where(AldiRecommendationFeedback.team == team)
        .order_by(AldiRecommendationFeedback.created_at, AldiRecommendationFeedback.id)
    ).scalars().all()
    return [{
        "verdict": fb.verdict, "note": fb.note, "created_at": fb.created_at.isoformat() if fb.created_at else None,
        "product_name": fb.product_name, "retailer": fb.retailer, "price_usd": fb.price_usd,
        "material_family": fb.material_family, "kind": fb.kind or "pick", "fails": fb.fails, "theme_name": fb.theme_name,
    } for fb in rows]


@app.task(bind=True, max_retries=0)
def distill_team_feedback(self, team: str, force: bool = False) -> dict:
    """Rewrite a team's learned_rules from its whole feedback log. Runs when
    enough new feedback has arrived (settings.aldi_distill_min_new), from
    the daily backstop, or on demand from Team settings."""
    if team not in rec.TEAMS:
        return {"status": "unknown_team", "team": team}
    db = _get_session()
    try:
        prefs = db.get(AldiTeamPreference, team)
        if prefs is None:
            prefs = AldiTeamPreference(team=team, memo="", price_min=15.0, price_max=60.0)
            db.add(prefs)
            db.commit()
        items = feedback_items_for_team(db, team)
        if not items:
            return {"status": "no_feedback", "team": team}
        if not force and len(items) - (prefs.distilled_count or 0) < 1:
            return {"status": "nothing_new", "team": team, "count": len(items)}
        result = asyncio.run(rec.distill_rules(team, items, prefs.learned_rules or "", prefs.memo or ""))
        if result is None:
            return {"status": "failed", "team": team}
        prefs.learned_rules = "\n".join(f"- {r}" for r in result["rules"])
        prefs.distilled_at = datetime.utcnow()
        prefs.distilled_count = len(items)
        db.commit()
        log.info("aldi_distilled", team=team, items=len(items), rules=len(result["rules"]), summary=result["summary"])
        return {"status": "done", "team": team, "items": len(items), "rules": len(result["rules"]), "summary": result["summary"]}
    finally:
        db.close()


@app.task
def distill_all_teams() -> dict:
    """Daily backstop: distil any team with new feedback since its last run."""
    out = {}
    for team in rec.TEAMS:
        out[team] = distill_team_feedback.delay(team).id   # bound task: queue it, never call it directly
    return out


# ── Phase 5: link re-check ───────────────────────────────────────────────────

@app.task
def recheck_aldi_links() -> dict:
    """Weekly: re-open the links of recent picks (newest first, up to
    settings.aldi_linkcheck_batch) and mark the ones that have gone, so the
    card can warn the buyer before they click."""
    from datetime import timedelta
    db = _get_session()
    try:
        cutoff = datetime.utcnow() - timedelta(days=settings.aldi_linkcheck_days)
        since = datetime.utcnow() - timedelta(days=90)
        rows = db.execute(
            sa_select(AldiRecommendation)
            .where(AldiRecommendation.kind == "pick", AldiRecommendation.created_at >= since,
                   (AldiRecommendation.link_checked_at.is_(None)) | (AldiRecommendation.link_checked_at < cutoff))
            .order_by(AldiRecommendation.created_at.desc())
            .limit(settings.aldi_linkcheck_batch)
        ).scalars().all()
        if not rows:
            return {"checked": 0}

        async def run_all():
            sem = asyncio.Semaphore(settings.aldi_recommend_concurrency)
            async def one(r):
                async with sem:
                    return r.id, await rec.check_link(r.url, ref_id=r.id)
            return await asyncio.gather(*(one(r) for r in rows))

        results = dict(asyncio.run(run_all()))
        counts: dict[str, int] = {}
        for r in rows:
            res = results.get(r.id) or {"status": "blocked"}
            r.link_status = res["status"]
            r.link_checked_at = datetime.utcnow()
            counts[res["status"]] = counts.get(res["status"], 0) + 1
        db.commit()
        log.info("aldi_links_rechecked", checked=len(rows), **counts)
        return {"checked": len(rows), **counts}
    finally:
        db.close()
