"""Fragrance Trend API routes."""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from database.models import (
    FragranceTrend, FragranceTrendExample, FragranceTrendReport,
    Product, ProductAttributes, Retailer, TrendStatus,
)
from pydantic import BaseModel

router = APIRouter()


class FragranceTrendExampleOut(BaseModel):
    product_id: int
    name: str
    url: str
    price: Optional[float]
    currency: str
    primary_image_url: Optional[str]
    retailer_name: str
    retailer_slug: str
    retailer_country: str
    colours: list[str]
    materials: list[str]
    is_hero: bool
    is_best_seller: bool = False

    class Config:
        from_attributes = True


# Historical name — kept for backward compat with the existing /weeks/
# endpoint. Fragrance is now run-based, not weekly, so the semantics are:
#   `week`   → the run's timestamp as an ISO datetime string.
#   `generation_count` → count of sets in that run (max).
#   `generations` → the actual list of set numbers in that run (e.g. [1, 3, 4]
#                   after Set 2 was deleted). Frontend renders tabs from this.
class WeekInfo(BaseModel):
    week: str
    generation_count: int
    generations: list[int] = []


class FragranceTrendOut(BaseModel):
    id: int
    week_start: datetime
    generation: int = 1
    name: str
    description: str
    rationale: str
    category: str
    status: TrendStatus
    product_count: int
    retailer_count: int
    retailer_names: list[str]
    avg_price: Optional[float]
    momentum_pct: Optional[float]
    dominant_colours: list[str]
    dominant_materials: list[str]
    container_styles: list[str]
    scent_families: list[str]
    sustainability_signals: list[str]
    markets: list[str]
    price_tier: Optional[str]
    examples: list[FragranceTrendExampleOut] = []
    # New (2026-08-06): keyword list used by the "View all N products"
    # modal to run a live query against the whole fragrance catalogue.
    # Empty on legacy trends → modal falls back to stored examples.
    filter_keywords: list[str] = []
    # Full-catalogue match count under this trend's keywords + fragrance
    # keywords + image gate. Powers the "View all N products" button label.
    # Server sets this at read time (live count). Null on legacy trends.
    matching_product_count: Optional[int] = None

    class Config:
        from_attributes = True


class FragranceTrendReportOut(BaseModel):
    id: int
    week_start: datetime
    title: str
    summary: str
    total_products_analysed: int
    retailers_covered: int
    trend_count: int
    generation_count: int = 1
    trends: list[FragranceTrendOut]
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/weeks/", response_model=list[WeekInfo])
async def list_weeks(db: AsyncSession = Depends(get_db)):
    """List every fragrance analysis run, newest first.

    Endpoint kept at /weeks/ for backward compatibility. The `week` field
    is now the run's full ISO datetime string (not a date), and each entry
    includes the actual list of generation numbers in that run.
    """
    result = await db.execute(
        select(FragranceTrend.week_start, FragranceTrend.generation)
        .group_by(FragranceTrend.week_start, FragranceTrend.generation)
        .order_by(desc(FragranceTrend.week_start), FragranceTrend.generation)
    )
    by_run: dict = {}
    order: list = []
    for row in result.all():
        w = row.week_start
        if w not in by_run:
            by_run[w] = []
            order.append(w)
        by_run[w].append(int(row.generation))
    return [
        WeekInfo(
            week=w.isoformat(),
            generation_count=max(by_run[w]),
            generations=sorted(by_run[w]),
        )
        for w in order
    ]


@router.get("/latest", response_model=FragranceTrendReportOut)
async def get_latest_report(
    generation: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FragranceTrendReport).order_by(desc(FragranceTrendReport.week_start)).limit(1)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="No fragrance trend reports yet")
    return await _build_report_out(report, db, generation=generation)


@router.get("/", response_model=list[FragranceTrendReportOut])
async def list_reports(limit: int = 10, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(FragranceTrendReport).order_by(desc(FragranceTrendReport.week_start)).limit(limit)
    )
    reports = result.scalars().all()
    return [await _build_report_out(r, db) for r in reports]


@router.get("/trend/{trend_id}", response_model=FragranceTrendOut)
async def get_trend(trend_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FragranceTrend).where(FragranceTrend.id == trend_id))
    trend = result.scalar_one_or_none()
    if not trend:
        raise HTTPException(status_code=404, detail="Fragrance trend not found")
    return await _build_trend_out(trend, db, max_examples=20)


class FragranceTrendProductOut(BaseModel):
    id: int
    name: str
    url: str
    price: Optional[float] = None
    currency: str = "USD"
    primary_image_url: Optional[str] = None
    retailer_name: str
    retailer_slug: str
    is_best_seller: bool = False


class FragranceTrendProductsPage(BaseModel):
    total: int
    items: list[FragranceTrendProductOut]


@router.get("/trend/{trend_id}/products", response_model=FragranceTrendProductsPage)
async def get_trend_products(
    trend_id: int,
    limit: int = 48,
    offset: int = 0,
    only_best_sellers: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Live paginated query of every fragrance product matching this
    trend's keywords. Powers the 'View all N products' modal — buyers
    see thousands of matches instead of just the ~20 stored examples.

    Filters:
      - Product is active
      - Product NAME matches the fragrance scope keyword set (candle,
        wax melt, diffuser, ...) via word-boundary regex
      - Product NAME also matches this trend's filter_keywords
      - Image-quality gate
      - Optional best-sellers-only toggle

    Sort: best-sellers first, then name. Same shape as Product Trends.

    Legacy trends (filter_keywords = []) fall back to the stored
    FragranceTrendExample rows so nothing looks broken.
    """
    from sqlalchemy import text as sa_text
    from analysis.fragrance_keywords import fragrance_regex_pattern
    from analysis.image_filter import image_ok_sql
    import re as _re

    trend = await db.get(FragranceTrend, trend_id)
    if not trend:
        raise HTTPException(status_code=404, detail="Fragrance trend not found")

    tr_kws = [k for k in (trend.filter_keywords or []) if k]

    # Legacy fallback — no keywords → surface the stored examples.
    if not tr_kws:
        examples_result = await db.execute(
            select(FragranceTrendExample, Product, Retailer)
            .join(Product, FragranceTrendExample.product_id == Product.id)
            .join(Retailer, Product.retailer_id == Retailer.id)
            .where(FragranceTrendExample.trend_id == trend_id)
            .order_by(desc(Product.is_best_seller), desc(FragranceTrendExample.relevance_score))
        )
        rows = list(examples_result.all())
        if only_best_sellers:
            rows = [r for r in rows if r[1].is_best_seller]
        items = [
            FragranceTrendProductOut(
                id=p.id, name=p.name, url=p.url, price=p.price,
                currency=p.currency or "USD",
                primary_image_url=p.primary_image_url,
                retailer_name=r.name, retailer_slug=r.slug,
                is_best_seller=bool(p.is_best_seller),
            )
            for _, p, r in rows
        ]
        return FragranceTrendProductsPage(
            total=len(items),
            items=items[offset:offset + limit],
        )

    trend_pattern = r"\y(?:" + "|".join(_re.escape(k) for k in tr_kws) + r")\y"
    best_seller_clause = "AND p.is_best_seller = TRUE" if only_best_sellers else ""

    count_sql = sa_text(f"""
        SELECT COUNT(*) FROM products p
        WHERE p.is_active = TRUE
          AND p.name ~* :frag_pattern
          AND p.name ~* :trend_pattern
          {best_seller_clause}
          AND {image_ok_sql()}
    """)
    total = (await db.execute(count_sql, {
        "frag_pattern": fragrance_regex_pattern(),
        "trend_pattern": trend_pattern,
    })).scalar() or 0

    page_sql = sa_text(f"""
        SELECT p.id, p.name, p.url, p.price, p.currency, p.primary_image_url,
               p.is_best_seller, r.name AS retailer_name, r.slug AS retailer_slug
        FROM products p
        JOIN retailers r ON r.id = p.retailer_id
        WHERE p.is_active = TRUE
          AND p.name ~* :frag_pattern
          AND p.name ~* :trend_pattern
          {best_seller_clause}
          AND {image_ok_sql()}
        ORDER BY p.is_best_seller DESC, p.name ASC
        LIMIT :lim OFFSET :off
    """)
    rows = (await db.execute(page_sql, {
        "frag_pattern": fragrance_regex_pattern(),
        "trend_pattern": trend_pattern,
        "lim": limit,
        "off": offset,
    })).all()

    items = [
        FragranceTrendProductOut(
            id=row.id, name=row.name, url=row.url, price=row.price,
            currency=row.currency or "USD",
            primary_image_url=row.primary_image_url,
            retailer_name=row.retailer_name, retailer_slug=row.retailer_slug,
            is_best_seller=bool(row.is_best_seller),
        )
        for row in rows
    ]
    return FragranceTrendProductsPage(total=total, items=items)


@router.post("/generate")
async def generate_report():
    """Trigger fragrance trend analysis."""
    from tasks.analysis_tasks import run_fragrance_trend_analysis_task
    task = run_fragrance_trend_analysis_task.apply_async(queue="reports")
    return {"task_id": task.id, "status": "queued"}


@router.delete("/clear")
async def clear_all(db: AsyncSession = Depends(get_db)):
    """Delete all fragrance trend sets across all generations and weeks."""
    await db.execute(delete(FragranceTrendExample))
    await db.execute(delete(FragranceTrend))
    await db.execute(delete(FragranceTrendReport))
    await db.commit()
    return {"status": "cleared"}


@router.post("/regenerate")
async def regenerate_report():
    """Generate a fresh set of fragrance trends without deleting the previous generation (Try Again)."""
    from tasks.analysis_tasks import regenerate_fragrance_trend_analysis_task
    task = regenerate_fragrance_trend_analysis_task.apply_async(queue="reports")
    return {"task_id": task.id, "status": "queued"}


@router.delete("/runs/{run_id}/generations/{generation}")
async def delete_run_generation(
    run_id: int,
    generation: int,
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete a single Set N for the given fragrance analysis run.

    Order matters here (Fragrance has one extra step vs Product Trends —
    the report row's JSONB trend_ids list has to be pruned too):

      1. Resolve the run's timestamp from the report id.
      2. Count what's here and what would remain.
      3. Null any prev_trend_id from other trends that references rows
         we are about to delete (the FK has no ON DELETE clause).
      4. Delete the FragranceTrendExample rows for the affected trends.
      5. Delete the FragranceTrend rows.
      6. Prune the deleted IDs out of report.trend_ids, and recompute
         report.generation_count from what remains.

    Guardrails:
      - 404 if the run doesn't exist, or the generation has no trends.
      - 409 if this would leave the whole run with zero trends — the
        caller should delete the whole run via /clear or a per-run
        delete instead.

    Numbering is not compacted. Sets 1/2/3 minus 2 → Sets 1/3; the next
    Try Again produces Set 4.
    """
    from sqlalchemy import delete as sa_delete, update as sa_update

    report = await db.get(FragranceTrendReport, run_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    run_at = report.week_start  # legacy column name; semantically run_at

    # Count what's in this generation for this run
    count_row = await db.execute(
        select(func.count(FragranceTrend.id)).where(
            FragranceTrend.week_start == run_at,
            FragranceTrend.generation == generation,
        )
    )
    to_delete = count_row.scalar_one() or 0
    if to_delete == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No fragrance trends found for run {run_id} generation {generation}",
        )

    # Count what would remain
    remaining_row = await db.execute(
        select(func.count(FragranceTrend.id)).where(
            FragranceTrend.week_start == run_at,
            FragranceTrend.generation != generation,
        )
    )
    remaining = remaining_row.scalar_one() or 0
    if remaining == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Refusing to delete — this is the only remaining generation for "
                f"run {run_id}. Delete the whole run via /clear if you want a "
                f"clean slate."
            ),
        )

    # Collect trend IDs we're about to delete
    id_rows = await db.execute(
        select(FragranceTrend.id).where(
            FragranceTrend.week_start == run_at,
            FragranceTrend.generation == generation,
        )
    )
    trend_ids = [tid for (tid,) in id_rows.all()]
    trend_id_set = set(trend_ids)

    # 3. Null downstream backlinks (any FragranceTrend anywhere pointing at
    # rows we are deleting). Momentum is gone for new rows, but legacy
    # pre-refactor rows may still hold prev_trend_id values.
    unlinked_result = await db.execute(
        sa_update(FragranceTrend)
        .where(FragranceTrend.prev_trend_id.in_(trend_ids))
        .values(prev_trend_id=None)
    )
    unlinked = unlinked_result.rowcount or 0

    # 4. Delete the FragranceTrendExample rows
    examples_result = await db.execute(
        sa_delete(FragranceTrendExample).where(
            FragranceTrendExample.trend_id.in_(trend_ids)
        )
    )
    examples_deleted = examples_result.rowcount or 0

    # 5. Delete the FragranceTrend rows
    await db.execute(
        sa_delete(FragranceTrend).where(FragranceTrend.id.in_(trend_ids))
    )

    # 6. Prune report.trend_ids and recompute generation_count. Both are
    # stored fields on FragranceTrendReport that would drift otherwise.
    report.trend_ids = [tid for tid in (report.trend_ids or []) if tid not in trend_id_set]
    gen_rows = await db.execute(
        select(FragranceTrend.generation)
        .where(FragranceTrend.week_start == run_at)
        .distinct()
    )
    remaining_gens = sorted({int(g) for (g,) in gen_rows.all()})
    report.generation_count = max(remaining_gens) if remaining_gens else 1

    await db.commit()

    return {
        "run_id": run_id,
        "generation": generation,
        "deleted_trends": to_delete,
        "deleted_examples": examples_deleted,
        "unlinked_backlinks": unlinked,
        "remaining_generations": remaining_gens,
    }


@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """Poll the status of a fragrance trend analysis task."""
    from celery.result import AsyncResult
    from tasks.celery_app import app as celery_app

    result = AsyncResult(task_id, app=celery_app)
    state = result.state

    if state == "PROGRESS":
        info = result.info or {}
        return {"task_id": task_id, "state": "PROGRESS", "pct": info.get("pct", 0), "step": info.get("step", "")}
    elif state == "SUCCESS":
        return {"task_id": task_id, "state": "SUCCESS", "pct": 100, "step": "Complete!"}
    elif state == "FAILURE":
        return {"task_id": task_id, "state": "FAILURE", "pct": 0, "step": "Analysis failed"}
    else:
        return {"task_id": task_id, "state": state, "pct": 2, "step": "Queued…"}


async def _build_report_out(
    report: FragranceTrendReport,
    db: AsyncSession,
    generation: Optional[int] = None,
) -> FragranceTrendReportOut:
    generation_count = report.generation_count or 1
    effective_gen = generation if generation is not None else generation_count

    if not report.trend_ids:
        return FragranceTrendReportOut(
            id=report.id, week_start=report.week_start, title=report.title,
            summary=report.summary, total_products_analysed=report.total_products_analysed,
            retailers_covered=report.retailers_covered, trend_count=0,
            generation_count=generation_count, trends=[], created_at=report.created_at,
        )

    result = await db.execute(
        select(FragranceTrend)
        .where(FragranceTrend.id.in_(report.trend_ids))
        .where(FragranceTrend.generation == effective_gen)
        .order_by(desc(FragranceTrend.product_count))
    )
    trends = result.scalars().all()
    trend_outs = [await _build_trend_out(t, db) for t in trends]

    return FragranceTrendReportOut(
        id=report.id, week_start=report.week_start, title=report.title,
        summary=report.summary, total_products_analysed=report.total_products_analysed,
        retailers_covered=report.retailers_covered, trend_count=len(trends),
        generation_count=generation_count, trends=trend_outs, created_at=report.created_at,
    )


async def _build_trend_out(
    trend: FragranceTrend, db: AsyncSession, max_examples: int = 6
) -> FragranceTrendOut:
    from analysis.image_filter import image_ok_orm
    from analysis.fragrance_keywords import fragrance_regex_pattern
    from sqlalchemy import text as sa_text

    # Best-sellers first (matches Product Trends default sort). Image
    # gate applied at read time so historical trends that stored
    # examples pointing at placeholder URLs don't surface broken tiles.
    examples_result = await db.execute(
        select(FragranceTrendExample, Product, ProductAttributes, Retailer)
        .join(Product, FragranceTrendExample.product_id == Product.id)
        .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(FragranceTrendExample.trend_id == trend.id)
        .where(image_ok_orm())
        .order_by(
            desc(Product.is_best_seller),
            desc(FragranceTrendExample.is_hero),
            desc(FragranceTrendExample.relevance_score),
        )
        .limit(max_examples)
    )

    examples = []
    for ex, product, attrs, retailer in examples_result.all():
        examples.append(FragranceTrendExampleOut(
            product_id=product.id,
            name=product.name,
            url=product.url,
            price=product.price,
            currency=product.currency,
            primary_image_url=product.primary_image_url,
            retailer_name=retailer.name,
            retailer_slug=retailer.slug,
            retailer_country=retailer.country,
            colours=attrs.colours if attrs else [],
            materials=attrs.materials if attrs else [],
            is_hero=ex.is_hero,
            is_best_seller=bool(product.is_best_seller),
        ))

    # Read cached count from the trend row rather than running the
    # live regex COUNT — the live version was a full-table scan against
    # 156k products with two `~*` predicates, ~3-5s per call, multiplied
    # by every trend on a page (30s+ tab switches).
    #
    # Backfill: for new-shape trends persisted BEFORE the count column
    # existed, cached_count is NULL but filter_keywords is populated.
    # Compute lazily and persist here so subsequent loads are instant.
    # Legacy trends (filter_keywords == []) stay NULL forever — the
    # frontend hides the "View all" button when count is null.
    tr_kws = [k for k in (trend.filter_keywords or []) if k]
    matching_count = trend.matching_product_count
    if tr_kws and matching_count is None:
        import re as _re
        from analysis.image_filter import image_ok_sql
        trend_pattern = r"\y(?:" + "|".join(_re.escape(k) for k in tr_kws) + r")\y"
        count_row = await db.execute(sa_text(f"""
            SELECT COUNT(*) FROM products p
            WHERE p.is_active = TRUE
              AND p.name ~* :frag_pattern
              AND p.name ~* :trend_pattern
              AND {image_ok_sql()}
        """), {
            "frag_pattern": fragrance_regex_pattern(),
            "trend_pattern": trend_pattern,
        })
        matching_count = count_row.scalar() or 0
        trend.matching_product_count = matching_count
        await db.commit()  # persist so we don't recompute on next page load

    return FragranceTrendOut(
        id=trend.id,
        week_start=trend.week_start,
        generation=trend.generation,
        name=trend.name,
        description=trend.description,
        rationale=trend.rationale,
        category=trend.category,
        status=trend.status,
        product_count=trend.product_count,
        retailer_count=trend.retailer_count,
        retailer_names=trend.retailer_names or [],
        avg_price=trend.avg_price,
        momentum_pct=trend.momentum_pct,
        dominant_colours=trend.dominant_colours or [],
        dominant_materials=trend.dominant_materials or [],
        container_styles=trend.container_styles or [],
        scent_families=trend.scent_families or [],
        sustainability_signals=trend.sustainability_signals or [],
        markets=trend.markets or [],
        price_tier=trend.price_tier,
        examples=examples,
        filter_keywords=tr_kws,
        matching_product_count=matching_count,
    )
