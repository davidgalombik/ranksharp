"""In-store Trend Report API routes.

Parallel to api/routes/reports.py (Online Products trends) but reads from
the InStoreTrend / InStoreTrendReport tables. Source data is the In-store
Products catalogue (InStoreCatalogueItem rows), not the Online Products
table.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from database.models import (
    InStoreTrendReport, InStoreTrend, InStoreTrendExample,
    InStoreTrendRecommendation, TrendStatus,
    InStoreCatalogueItem, InStoreCatalogueImage,
    Product, Retailer,
)
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

router = APIRouter()


class InStoreTrendExampleItemOut(BaseModel):
    id: int
    product_name: str
    category: Optional[str] = None
    subcategory: Optional[str] = None
    product_segment: Optional[str] = None
    image_id: int
    has_crop: bool
    retailer: Optional[str] = None


class InStoreTrendRecommendationOut(BaseModel):
    product_id: int
    name: str
    retailer_name: Optional[str] = None
    retailer_slug: Optional[str] = None
    url: str
    price: Optional[float] = None
    currency: str = "USD"
    primary_image_url: Optional[str] = None
    similarity: float
    is_best_seller: bool = False


class InStoreTrendOut(BaseModel):
    id: int
    name: str
    description: str
    rationale: str
    category: str
    status: str
    # Buyer-voice momentum for this store walk: emerging | noted |
    # prominent | strong_focus | shifting. None on legacy trends.
    momentum: Optional[str] = None
    item_count: int
    momentum_pct: Optional[float] = None
    dominant_colours: list[str]
    dominant_materials: list[str]
    dominant_patterns: list[str]
    dominant_styles: list[str]
    dominant_taxonomy: list[str]
    examples: list[InStoreTrendExampleItemOut] = []
    recommendations: list[InStoreTrendRecommendationOut] = []
    # Count of image-qualified recommendations stored for this trend.
    # Populates the "View all N recommended products" button label.
    total_recommendation_count: int = 0


class InStoreReportOut(BaseModel):
    id: int
    week_start: datetime
    title: str
    summary: str
    total_items_analysed: int
    trend_count: int
    # Horizon + country of the Set currently in view. NULL = all time /
    # all countries. Rendered in the header so buyers always know what
    # was analysed.
    months_window: Optional[int] = None
    country: Optional[str] = None
    rising_trends: list[InStoreTrendOut]
    new_trends: list[InStoreTrendOut]
    declining_trends: list[InStoreTrendOut]
    all_trends: list[InStoreTrendOut]
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/", response_model=list[InStoreReportOut])
async def list_reports(limit: int = 10, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(InStoreTrendReport).order_by(desc(InStoreTrendReport.week_start)).limit(limit)
    )
    reports = result.scalars().all()
    return [await _build_report_out(r, db) for r in reports]


@router.get("/latest", response_model=InStoreReportOut)
async def get_latest(
    generation: Optional[int] = Query(
        default=None,
        description="Filter trends to a single Set. Omit for the latest Set.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Latest report. When `generation` is provided, only trends from that
    Set are returned + the report's months_window switches to that Set's
    horizon (each Set can have been run against a different window)."""
    result = await db.execute(
        select(InStoreTrendReport).order_by(desc(InStoreTrendReport.week_start)).limit(1)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="No reports yet")
    return await _build_report_out(report, db, generation=generation)


class InStoreSetOut(BaseModel):
    """One Set on the latest report — for the Set tab bar."""
    generation: int
    months_window: Optional[int] = None
    country: Optional[str] = None
    trend_count: int
    item_count: int  # sum of trend.item_count across the set


@router.get("/sets", response_model=list[InStoreSetOut])
async def list_sets(db: AsyncSession = Depends(get_db)):
    """Every Set on the latest report, in generation order. Powers the
    Set tab bar on the /instore page.

    A Set is homogeneous per (generation, months_window, country) —
    the engine stamps every trend in a run with the same tuple — so
    grouping by generation and taking max() over the other two just
    hoists the shared value into the row.
    """
    latest_report = (await db.execute(
        select(InStoreTrendReport).order_by(desc(InStoreTrendReport.week_start)).limit(1)
    )).scalar_one_or_none()
    if not latest_report:
        return []

    rows = await db.execute(
        select(
            InStoreTrend.generation,
            func.max(InStoreTrend.months_window).label("months_window"),
            func.max(InStoreTrend.country).label("country"),
            func.count(InStoreTrend.id).label("trend_count"),
            func.coalesce(func.sum(InStoreTrend.item_count), 0).label("item_count"),
        )
        .where(InStoreTrend.week_start == latest_report.week_start)
        .group_by(InStoreTrend.generation)
        .order_by(InStoreTrend.generation)
    )
    return [
        InStoreSetOut(
            generation=int(gen),
            months_window=(int(mw) if mw is not None else None),
            country=(str(ctry) if ctry else None),
            trend_count=int(tc),
            item_count=int(ic),
        )
        for gen, mw, ctry, tc, ic in rows.all()
    ]


@router.get("/{report_id}", response_model=InStoreReportOut)
async def get_report(report_id: int, db: AsyncSession = Depends(get_db)):
    report = await db.get(InStoreTrendReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return await _build_report_out(report, db)


def _validate_country(country: Optional[str]) -> Optional[str]:
    if country is None:
        return None
    up = country.upper()
    if up not in ("US", "AU"):
        raise HTTPException(status_code=400, detail="country must be 'US' or 'AU'")
    return up


@router.post("/generate")
async def generate_report(
    months_window: Optional[int] = Query(
        default=None, ge=0, le=60,
        description="Time horizon for shelf photos to analyse. 0 = current "
                    "month only. 1 = previous complete month only. N>=2 = "
                    "trailing N calendar months including current. Omit for "
                    "all time.",
    ),
    country: Optional[str] = Query(
        default=None,
        description="Restrict shelf photos to a single country ('US' or 'AU'). "
                    "Omit to analyse mixed across every country.",
    ),
):
    """Trigger a fresh in-store trend analysis."""
    from tasks.analysis_tasks import run_instore_trend_analysis_task
    country = _validate_country(country)
    task = run_instore_trend_analysis_task.apply_async(
        queue="reports", args=[months_window, country],
    )
    return {
        "task_id": task.id, "status": "queued",
        "months_window": months_window, "country": country,
    }


@router.post("/regenerate")
async def regenerate_report(
    months_window: Optional[int] = Query(
        default=None, ge=0, le=60,
        description="Time horizon for shelf photos to analyse. Same shape as /generate.",
    ),
    country: Optional[str] = Query(
        default=None,
        description="Restrict shelf photos to a single country ('US' or 'AU'). "
                    "Omit to analyse mixed across every country.",
    ),
):
    """Generate a new generation of trends for the current week (Try Again)."""
    from tasks.analysis_tasks import regenerate_instore_trend_analysis_task
    country = _validate_country(country)
    task = regenerate_instore_trend_analysis_task.apply_async(
        queue="reports", args=[months_window, country],
    )
    return {
        "task_id": task.id, "status": "queued",
        "months_window": months_window, "country": country,
    }


@router.delete("/clear")
async def clear_all(db: AsyncSession = Depends(get_db)):
    """Delete every in-store trend report, trend, example, and recommendation."""
    await db.execute(delete(InStoreTrendRecommendation))
    await db.execute(delete(InStoreTrendExample))
    await db.execute(delete(InStoreTrend))
    await db.execute(delete(InStoreTrendReport))
    await db.commit()
    return {"status": "cleared"}


@router.delete("/reports/{report_id}/generations/{generation}")
async def delete_set(
    report_id: int,
    generation: int,
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete a single Set on an in-store trend report.

    Steps (order matters):
      1. Resolve the report's week_start.
      2. Count what's in this generation and what remains.
      3. Null any prev_trend_id backlinks pointing at the doomed rows.
      4. Delete recommendations, examples, then the trend rows.
      5. Prune the deleted IDs from report.trend_ids; recompute
         generation_count from what's left.

    Guardrails:
      - 404 if the report doesn't exist, or the generation has no trends.
      - 409 if deleting would leave the report with zero trends
        (clear the whole run via /clear instead).
    """
    from sqlalchemy import delete as sa_delete, update as sa_update

    report = await db.get(InStoreTrendReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    count_row = await db.execute(
        select(func.count(InStoreTrend.id)).where(
            InStoreTrend.week_start == report.week_start,
            InStoreTrend.generation == generation,
        )
    )
    to_delete = count_row.scalar_one() or 0
    if to_delete == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No in-store trends found for report {report_id} generation {generation}",
        )

    remaining_row = await db.execute(
        select(func.count(InStoreTrend.id)).where(
            InStoreTrend.week_start == report.week_start,
            InStoreTrend.generation != generation,
        )
    )
    remaining = remaining_row.scalar_one() or 0
    if remaining == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Refusing to delete — this is the only remaining Set for "
                f"report {report_id}. Use /clear if you want a clean slate."
            ),
        )

    id_rows = await db.execute(
        select(InStoreTrend.id).where(
            InStoreTrend.week_start == report.week_start,
            InStoreTrend.generation == generation,
        )
    )
    trend_ids = [tid for (tid,) in id_rows.all()]
    trend_id_set = set(trend_ids)

    # Null downstream backlinks (any trend elsewhere pointing at rows
    # we're about to delete). Momentum was never wired for In-store,
    # but the FK has no ON DELETE clause so we clear it defensively.
    unlinked_result = await db.execute(
        sa_update(InStoreTrend)
        .where(InStoreTrend.prev_trend_id.in_(trend_ids))
        .values(prev_trend_id=None)
    )
    unlinked = unlinked_result.rowcount or 0

    rec_result = await db.execute(
        sa_delete(InStoreTrendRecommendation).where(
            InStoreTrendRecommendation.trend_id.in_(trend_ids)
        )
    )
    recs_deleted = rec_result.rowcount or 0

    ex_result = await db.execute(
        sa_delete(InStoreTrendExample).where(
            InStoreTrendExample.trend_id.in_(trend_ids)
        )
    )
    examples_deleted = ex_result.rowcount or 0

    await db.execute(
        sa_delete(InStoreTrend).where(InStoreTrend.id.in_(trend_ids))
    )

    report.trend_ids = [tid for tid in (report.trend_ids or []) if tid not in trend_id_set]
    gen_rows = await db.execute(
        select(InStoreTrend.generation)
        .where(InStoreTrend.week_start == report.week_start)
        .distinct()
    )
    remaining_gens = sorted({int(g) for (g,) in gen_rows.all()})
    report.generation_count = max(remaining_gens) if remaining_gens else 1

    await db.commit()

    return {
        "report_id": report_id,
        "generation": generation,
        "deleted_trends": to_delete,
        "deleted_examples": examples_deleted,
        "deleted_recommendations": recs_deleted,
        "unlinked_backlinks": unlinked,
        "remaining_generations": remaining_gens,
    }


@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """Poll the status of a trend analysis Celery task."""
    from celery.result import AsyncResult
    from tasks.celery_app import app as celery_app

    result = AsyncResult(task_id, app=celery_app)
    state = result.state
    if state == "PROGRESS":
        info = result.info or {}
        return {"task_id": task_id, "state": "PROGRESS",
                "pct": info.get("pct", 0), "step": info.get("step", "")}
    elif state == "SUCCESS":
        return {"task_id": task_id, "state": "SUCCESS", "pct": 100, "step": "Complete!"}
    elif state == "FAILURE":
        return {"task_id": task_id, "state": "FAILURE", "pct": 0, "step": "Analysis failed"}
    else:
        return {"task_id": task_id, "state": state, "pct": 2, "step": "Queued…"}


class InStoreTrendRecommendationsPage(BaseModel):
    total: int
    items: list[InStoreTrendRecommendationOut]


@router.get(
    "/trend/{trend_id}/recommendations",
    response_model=InStoreTrendRecommendationsPage,
)
async def get_trend_recommendations(
    trend_id: int,
    limit: int = 48,
    offset: int = 0,
    only_best_sellers: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Paginated recommendations for an in-store trend.

    Powers the 'View all N recommended products' modal on the trend card.
    Reads the stored InStoreTrendRecommendation rows (matched by the
    engine via embedding similarity) rather than a live regex query —
    the recommendations are already carefully selected, and in-store's
    scale (dozens to hundreds per trend) doesn't need the live-catalogue
    approach we use for Online / Fragrance.

    Filters:
      - Image-quality gate (never surface a broken tile)
      - Optional best-sellers-only toggle
    Sort: best-sellers first, then original engine rank.
    """
    from sqlalchemy import func as sa_func
    from analysis.image_filter import image_ok_orm

    trend = await db.get(InStoreTrend, trend_id)
    if not trend:
        raise HTTPException(status_code=404, detail="In-store trend not found")

    base = (
        select(InStoreTrendRecommendation, Product, Retailer)
        .join(Product, InStoreTrendRecommendation.product_id == Product.id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(InStoreTrendRecommendation.trend_id == trend_id)
        .where(image_ok_orm())
    )
    if only_best_sellers:
        base = base.where(Product.is_best_seller == True)

    # Total count under the same filters
    total_row = await db.execute(
        select(sa_func.count()).select_from(base.subquery())
    )
    total = total_row.scalar_one() or 0

    page = (
        base.order_by(
            desc(Product.is_best_seller),
            InStoreTrendRecommendation.rank,
        )
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(page)).all()

    items = [
        InStoreTrendRecommendationOut(
            product_id=prod.id,
            name=prod.name,
            retailer_name=ret.name,
            retailer_slug=ret.slug,
            url=prod.url,
            price=prod.price,
            currency=prod.currency or "USD",
            primary_image_url=prod.primary_image_url,
            similarity=rec.similarity,
            is_best_seller=bool(prod.is_best_seller),
        )
        for rec, prod, ret in rows
    ]
    return InStoreTrendRecommendationsPage(total=total, items=items)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _build_report_out(
    report: InStoreTrendReport,
    db: AsyncSession,
    generation: Optional[int] = None,
) -> InStoreReportOut:
    if not report.trend_ids:
        return InStoreReportOut(
            id=report.id, week_start=report.week_start, title=report.title,
            summary=report.summary, total_items_analysed=report.total_items_analysed,
            trend_count=0, months_window=report.months_window, country=None,
            rising_trends=[], new_trends=[], declining_trends=[],
            all_trends=[], created_at=report.created_at,
        )

    # Resolve which Set to show. Default = latest generation for this
    # report (so the buyer lands on their newest Set on first load,
    # matching Product Trends' behaviour).
    if generation is None:
        latest_gen_row = await db.execute(
            select(func.max(InStoreTrend.generation))
            .where(InStoreTrend.week_start == report.week_start)
        )
        generation = latest_gen_row.scalar_one_or_none() or 1

    q = (
        select(InStoreTrend)
        .where(InStoreTrend.id.in_(report.trend_ids))
        .where(InStoreTrend.generation == generation)
        .order_by(desc(InStoreTrend.item_count))
    )
    result = await db.execute(q)
    trends = result.scalars().all()
    trend_ids = [t.id for t in trends]

    # Header horizon + country = whatever the Set actually being shown
    # was stamped with. Legacy trends (pre per-trend columns) fall back
    # to the report row's months_window; country falls back to None
    # (was mixed before the column existed).
    active_window = report.months_window
    active_country: Optional[str] = None
    if trends:
        window_from_trends = trends[0].months_window
        if window_from_trends is not None:
            active_window = window_from_trends
        active_country = trends[0].country

    # Bulk-fetch examples + their items + parent images for retailer/image_id.
    # "Is-a-product" gate — filter to items that Claude Vision actually
    # classified as physical products, not signage / section headers /
    # background shelf edges:
    #   - cropped_file_path IS NOT NULL: Claude successfully cropped a region
    #   - prominence != 'background': item was foreground on the shelf
    #   - has at least one classification tag: colours OR materials OR patterns
    #     OR style_tags is non-empty (Claude classified it as a real product)
    from sqlalchemy import or_ as sa_or
    ex_result = await db.execute(
        select(InStoreTrendExample, InStoreCatalogueItem, InStoreCatalogueImage)
        .join(InStoreCatalogueItem, InStoreTrendExample.item_id == InStoreCatalogueItem.id)
        .join(InStoreCatalogueImage, InStoreCatalogueItem.image_id == InStoreCatalogueImage.id)
        .where(InStoreTrendExample.trend_id.in_(trend_ids))
        .where(InStoreCatalogueItem.cropped_file_path.isnot(None))
        .where(sa_or(
            InStoreCatalogueItem.prominence.is_(None),
            InStoreCatalogueItem.prominence != "background",
        ))
        # At least one classification tag populated — Claude Vision only
        # writes these when it identified a real product. Signage /
        # section headers / blank crops leave them null.
        .where(sa_or(
            InStoreCatalogueItem.colours.isnot(None),
            InStoreCatalogueItem.materials.isnot(None),
            InStoreCatalogueItem.patterns.isnot(None),
            InStoreCatalogueItem.style_tags.isnot(None),
        ))
        .order_by(desc(InStoreTrendExample.relevance_score))
    )
    examples_by_trend: dict[int, list[InStoreTrendExampleItemOut]] = {}
    for ex, item, image in ex_result.all():
        examples_by_trend.setdefault(ex.trend_id, []).append(
            InStoreTrendExampleItemOut(
                id=item.id,
                product_name=item.product_name,
                category=item.category,
                subcategory=item.subcategory,
                product_segment=item.product_segment,
                image_id=item.image_id,
                has_crop=bool(item.cropped_file_path),
                retailer=image.retailer,
            )
        )

    # Bulk-fetch recommendations + their products + retailer name.
    # Image gate — never surface a broken tile. Best-sellers first, then
    # the engine's original rank (embedding similarity ordering).
    from analysis.image_filter import image_ok_orm
    rec_result = await db.execute(
        select(InStoreTrendRecommendation, Product, Retailer)
        .join(Product, InStoreTrendRecommendation.product_id == Product.id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(InStoreTrendRecommendation.trend_id.in_(trend_ids))
        .where(image_ok_orm())
        .order_by(
            InStoreTrendRecommendation.trend_id,
            desc(Product.is_best_seller),
            InStoreTrendRecommendation.rank,
        )
    )
    recs_by_trend: dict[int, list[InStoreTrendRecommendationOut]] = {}
    for rec, prod, ret in rec_result.all():
        recs_by_trend.setdefault(rec.trend_id, []).append(
            InStoreTrendRecommendationOut(
                product_id=prod.id,
                name=prod.name,
                retailer_name=ret.name,
                retailer_slug=ret.slug,
                url=prod.url,
                price=prod.price,
                currency=prod.currency or "USD",
                primary_image_url=prod.primary_image_url,
                similarity=rec.similarity,
                is_best_seller=bool(prod.is_best_seller),
            )
        )

    def to_out(t: InStoreTrend) -> InStoreTrendOut:
        recs = recs_by_trend.get(t.id, [])
        return InStoreTrendOut(
            id=t.id, name=t.name, description=t.description, rationale=t.rationale,
            category=t.category, status=t.status.value, momentum=t.momentum,
            item_count=t.item_count, momentum_pct=t.momentum_pct,
            dominant_colours=t.dominant_colours or [],
            dominant_materials=t.dominant_materials or [],
            dominant_patterns=t.dominant_patterns or [],
            dominant_styles=t.dominant_styles or [],
            dominant_taxonomy=t.dominant_taxonomy or [],
            examples=examples_by_trend.get(t.id, []),
            # Trim the recommendations list embedded in the report — the
            # card only needs the first ~6 for hero images. The rest are
            # fetched paginated via /trend/{id}/recommendations when the
            # user opens the "View all N" modal.
            recommendations=recs[:6],
            total_recommendation_count=len(recs),
        )

    rising = [to_out(t) for t in trends if t.status == TrendStatus.RISING]
    new = [to_out(t) for t in trends if t.status == TrendStatus.NEW]
    declining = [to_out(t) for t in trends if t.status == TrendStatus.DECLINING]

    return InStoreReportOut(
        id=report.id, week_start=report.week_start, title=report.title,
        summary=report.summary, total_items_analysed=report.total_items_analysed,
        trend_count=len(trends), months_window=active_window, country=active_country,
        rising_trends=rising, new_trends=new, declining_trends=declining,
        all_trends=[to_out(t) for t in trends],
        created_at=report.created_at,
    )
