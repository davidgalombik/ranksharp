"""Trend API routes."""
from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, desc, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from database.db import get_db
from database.models import Trend, TrendExample, Product, ProductAttributes, Retailer, TrendStatus
from pydantic import BaseModel

router = APIRouter()

# JSON column names on ProductAttributes to check for a given trend category.
# None means the tag column doesn't exist for that dimension (Seasonal has
# no seasonal tag — matching is name-only).
TAG_COLUMN_BY_CATEGORY: dict[str, Optional[str]] = {
    "colour": "colours",
    "material": "materials",
    "style": "style_tags",
    "pattern": "patterns",
    "seasonal": None,
}


def _get_trend_keywords(trend: Trend) -> list[str]:
    """Return the list of keywords to substring-match a trend against
    the products table. Umbrella trends (name matches a key in
    MATERIAL_UMBRELLAS / STYLE_UMBRELLAS / SEASONAL_UMBRELLAS /
    PATTERN_UMBRELLAS) expand to their full keyword list; other trends
    fall back to the single trend name."""
    from analysis.design_trend_engine import (
        MATERIAL_UMBRELLAS, STYLE_UMBRELLAS,
        SEASONAL_UMBRELLAS, PATTERN_UMBRELLAS,
    )
    umbrellas_by_category = {
        "material": MATERIAL_UMBRELLAS,
        "style": STYLE_UMBRELLAS,
        "seasonal": SEASONAL_UMBRELLAS,
        "pattern": PATTERN_UMBRELLAS,
    }
    umbrellas = umbrellas_by_category.get(trend.category, {})
    if trend.name in umbrellas:
        return umbrellas[trend.name]
    return [trend.name]


class TrendExampleOut(BaseModel):
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
    style_tags: list[str]
    is_hero: bool
    # Populated from Product.is_best_seller so the "View all" modal can
    # badge best-seller cards and sort them first (2026-07-28).
    is_best_seller: bool = False

    class Config:
        from_attributes = True


class TrendOut(BaseModel):
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
    dominant_patterns: list[str]
    dominant_styles: list[str]
    markets: list[str]
    price_tier: Optional[str]
    examples: list[TrendExampleOut] = []

    class Config:
        from_attributes = True


@router.get("/", response_model=list[TrendOut])
async def list_trends(
    week_start: Optional[date] = None,
    category: Optional[str] = None,
    status: Optional[TrendStatus] = None,
    generation: Optional[int] = None,
    # Default bumped 20 -> 300 so the frontend receives the full set of
    # trends for a generation (a run typically emits ~60-100 trends across
    # 5 categories; the old default silently truncated to style+material,
    # which dominate on product_count). Frontend filters client-side.
    limit: int = Query(default=300, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List trends, optionally filtered by week, category, status, or generation.

    If generation is not supplied the latest generation for each week is returned.
    """
    effective_week: Optional[datetime] = None
    if week_start:
        effective_week = datetime.combine(week_start, datetime.min.time())

    # Resolve which generation to show
    if generation is None:
        # Find the max generation for the target week (or the most-recent week overall)
        gen_q = select(func.max(Trend.generation))
        if effective_week:
            gen_q = gen_q.where(Trend.week_start == effective_week)
        else:
            # Most recent week
            latest_week_q = select(func.max(Trend.week_start))
            latest_week_result = await db.execute(latest_week_q)
            latest_week = latest_week_result.scalar_one_or_none()
            if latest_week:
                gen_q = gen_q.where(Trend.week_start == latest_week)
                effective_week = latest_week
        gen_result = await db.execute(gen_q)
        generation = gen_result.scalar_one_or_none() or 1

    q = select(Trend).order_by(desc(Trend.week_start), desc(Trend.product_count))
    q = q.where(Trend.generation == generation)

    if effective_week:
        q = q.where(Trend.week_start == effective_week)
    if category:
        q = q.where(Trend.category == category)
    if status:
        q = q.where(Trend.status == status)

    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    trends = result.scalars().all()

    # Load examples for each trend
    output = []
    for trend in trends:
        trend_out = await _build_trend_out(trend, db)
        output.append(trend_out)

    return output


@router.get("/latest", response_model=list[TrendOut])
async def latest_trends(
    limit: int = Query(default=10, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent week's trends."""
    # Find the most recent week
    latest_result = await db.execute(
        select(Trend.week_start).order_by(desc(Trend.week_start)).limit(1)
    )
    latest_week = latest_result.scalar_one_or_none()
    if not latest_week:
        return []

    result = await db.execute(
        select(Trend)
        .where(Trend.week_start == latest_week)
        .order_by(desc(Trend.product_count))
        .limit(limit)
    )
    trends = result.scalars().all()
    return [await _build_trend_out(t, db) for t in trends]


@router.get("/{trend_id}", response_model=TrendOut)
async def get_trend(trend_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single trend with all examples (up to 100 — used by the
    "View all N products" modal on the frontend). List/latest endpoints
    stay at ~10 examples for card previews to keep payload small."""
    result = await db.execute(select(Trend).where(Trend.id == trend_id))
    trend = result.scalar_one_or_none()
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    return await _build_trend_out(trend, db, max_examples=100)


class TrendProductsPage(BaseModel):
    total: int
    items: list[TrendExampleOut]


@router.get("/{trend_id}/products", response_model=TrendProductsPage)
async def get_trend_products(
    trend_id: int,
    limit: int = Query(default=48, le=200),
    offset: int = 0,
    only_best_sellers: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Return the paginated list of products matching a trend, computed
    live against the full products table (not the ~100 stored examples).

    Matching:
      * name substring — every keyword for the trend (umbrella lists for
        Fabrics/Modern/Fall/etc., else the trend name) is turned into
        `\\y<keyword>\\y` regex and OR'd with the product name
      * tag column — for colour/material/style/pattern, the same keyword
        list is checked against `ProductAttributes.<col>` array values
      * A product counts if EITHER match hits

    Sorted best-sellers first, then by product id desc (stable-ish).
    """
    trend = (await db.execute(select(Trend).where(Trend.id == trend_id))).scalar_one_or_none()
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")

    keywords = [k.lower() for k in _get_trend_keywords(trend) if k]
    if not keywords:
        return TrendProductsPage(total=0, items=[])

    # Pure-ORM keyword match: for each keyword build (name ILIKE '%kw%'
    # OR tag_column::text ILIKE '%kw%'), then OR them all together.
    # Casting a JSONB array to text gives something like `["minimalist",
    # "modern"]` — ILIKE against that catches products tagged with the
    # keyword without needing a lateral jsonb_array_elements_text join.
    # No word-boundary matching (slight false-positive risk on very short
    # keywords) — the trade-off for the earlier bind-param version
    # returning zero results on every trend.
    tag_col_name = TAG_COLUMN_BY_CATEGORY.get(trend.category)

    def match_for(kw: str):
        name_cond = Product.name.ilike(f"%{kw}%")
        if not tag_col_name:
            return name_cond
        tag_col_attr = getattr(ProductAttributes, tag_col_name)
        tag_cond = cast(tag_col_attr, String).ilike(f"%{kw}%")
        return or_(name_cond, tag_cond)

    match_clause = or_(*(match_for(kw) for kw in keywords))
    where_conds = [Product.is_active == True, match_clause]
    if only_best_sellers:
        where_conds.append(Product.is_best_seller == True)

    count_stmt = (
        select(func.count())
        .select_from(Product)
        .outerjoin(ProductAttributes, ProductAttributes.product_id == Product.id)
        .where(*where_conds)
    )
    total = (await db.execute(count_stmt)).scalar_one()

    items_stmt = (
        select(Product, ProductAttributes, Retailer)
        .outerjoin(ProductAttributes, ProductAttributes.product_id == Product.id)
        .join(Retailer, Retailer.id == Product.retailer_id)
        .where(*where_conds)
        .order_by(desc(Product.is_best_seller), desc(Product.id))
        .limit(limit).offset(offset)
    )
    rows = (await db.execute(items_stmt)).all()
    items = [
        TrendExampleOut(
            product_id=p.id,
            name=p.name,
            url=p.url,
            price=p.price,
            currency=p.currency,
            primary_image_url=p.primary_image_url,
            retailer_name=r.name,
            retailer_slug=r.slug,
            retailer_country=r.country,
            colours=attrs.colours if attrs else [],
            materials=attrs.materials if attrs else [],
            style_tags=attrs.style_tags if attrs else [],
            is_hero=False,
            is_best_seller=bool(p.is_best_seller),
        )
        for p, attrs, r in rows
    ]
    return TrendProductsPage(total=total, items=items)


class WeekInfo(BaseModel):
    week: str
    # Max generation number seen this week. Kept for backward compatibility
    # with clients that only need "how many sets exist".
    generation_count: int
    # The ACTUAL distinct generation numbers this week, sorted ascending.
    # Populated so the frontend can render tabs for exactly what exists
    # rather than assuming 1..N (which is wrong after a delete leaves a gap).
    generations: list[int] = []


@router.get("/weeks/", response_model=list[WeekInfo])
async def list_weeks(db: AsyncSession = Depends(get_db)):
    """List all weeks with trend data, including generation count per week."""
    result = await db.execute(
        select(Trend.week_start, Trend.generation)
        .group_by(Trend.week_start, Trend.generation)
        .order_by(desc(Trend.week_start), Trend.generation)
    )
    # Group into {week: [gens]} preserving week ordering (desc)
    by_week: dict = {}
    order: list = []
    for row in result.all():
        w = row.week_start
        if w not in by_week:
            by_week[w] = []
            order.append(w)
        by_week[w].append(int(row.generation))
    return [
        WeekInfo(
            week=str(w.date()),
            generation_count=max(by_week[w]),
            generations=sorted(by_week[w]),
        )
        for w in order
    ]


@router.delete("/week/{week_start}/generations/{generation}")
async def delete_trend_generation(
    week_start: date,
    generation: int,
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete a single Set N for a given week.

    Order matters:
      1. Count what's here and what would remain.
      2. Null out any prev_trend_id from LATER weeks that references rows we
         are about to delete — the FK has no ON DELETE clause, so leaving
         those references would fail the DELETE with a FK violation.
      3. Delete the TrendExample rows for the affected trends.
      4. Delete the Trend rows themselves.

    Guardrails:
      - 404 if the week/generation has no trends.
      - 409 if this would leave the week with zero trends — the caller
        should delete no trends at all, or delete the whole week's data
        via /api/analysis/reset if that's what they actually want.

    Notes:
      - Numbering is not compacted. Sets 1/2/3 with 2 removed becomes
        Sets 1/3; the next Try Again produces Set 4 (max+1).
      - Downstream trends' momentum_pct values stay intact (they were
        snapshotted at creation time). Only the click-through backlink
        via prev_trend_id is severed for rows that pointed at deletions.
    """
    from sqlalchemy import delete as sa_delete, update as sa_update

    # Cast the path date back to a datetime at midnight to match the stored
    # week_start values (which are stored as DateTime at 00:00:00).
    week_dt = datetime.combine(week_start, datetime.min.time())

    # Count what's in this generation for this week
    count_row = await db.execute(
        select(func.count(Trend.id)).where(
            Trend.week_start == week_dt,
            Trend.generation == generation,
        )
    )
    to_delete = count_row.scalar_one() or 0
    if to_delete == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No trends found for week {week_start} generation {generation}",
        )

    # Count what would remain for this week if we did the delete
    remaining_row = await db.execute(
        select(func.count(Trend.id)).where(
            Trend.week_start == week_dt,
            Trend.generation != generation,
        )
    )
    remaining = remaining_row.scalar_one() or 0
    if remaining == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Refusing to delete — this is the only remaining generation for "
                f"week {week_start}. Delete a different set or wipe the whole "
                f"week's data via the analysis reset flow."
            ),
        )

    # Collect the trend IDs we're about to delete — needed to sever any
    # downstream prev_trend_id references and to scope the TrendExample delete.
    id_rows = await db.execute(
        select(Trend.id).where(
            Trend.week_start == week_dt,
            Trend.generation == generation,
        )
    )
    trend_ids = [tid for (tid,) in id_rows.all()]

    # 2. Null downstream backlinks (any Trend anywhere pointing at rows we
    # are deleting). No cascade — we want to keep those downstream rows;
    # they just lose the ability to link back to the prior. Their stored
    # momentum_pct remains valid as a historical snapshot.
    unlinked_result = await db.execute(
        sa_update(Trend)
        .where(Trend.prev_trend_id.in_(trend_ids))
        .values(prev_trend_id=None)
    )
    unlinked = unlinked_result.rowcount or 0

    # 3. Delete the TrendExample rows for the affected trends.
    examples_result = await db.execute(
        sa_delete(TrendExample).where(TrendExample.trend_id.in_(trend_ids))
    )
    examples_deleted = examples_result.rowcount or 0

    # 4. Delete the Trend rows themselves.
    await db.execute(
        sa_delete(Trend).where(Trend.id.in_(trend_ids))
    )
    await db.commit()

    # Return what's left for the frontend to update its tabs
    remaining_gens_rows = await db.execute(
        select(Trend.generation)
        .where(Trend.week_start == week_dt)
        .distinct()
    )
    remaining_gens = sorted({int(g) for (g,) in remaining_gens_rows.all()})

    return {
        "week": str(week_start),
        "generation": generation,
        "deleted_trends": to_delete,
        "deleted_examples": examples_deleted,
        "unlinked_backlinks": unlinked,
        "remaining_generations": remaining_gens,
    }


async def _build_trend_out(
    trend: Trend, db: AsyncSession, max_examples: int = 6
) -> TrendOut:
    """Build a TrendOut including example products."""
    examples_result = await db.execute(
        select(TrendExample, Product, ProductAttributes, Retailer)
        .join(Product, TrendExample.product_id == Product.id)
        .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(TrendExample.trend_id == trend.id)
        # Best-sellers ranked first (matches DesignTrendEngine._create_examples),
        # then hero flag, then relevance_score. Modal on the frontend can
        # still re-sort but this is the sensible default.
        .order_by(
            desc(Product.is_best_seller),
            desc(TrendExample.is_hero),
            desc(TrendExample.relevance_score),
        )
        .limit(max_examples)
    )

    examples = []
    for ex, product, attrs, retailer in examples_result.all():
        examples.append(TrendExampleOut(
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
            style_tags=attrs.style_tags if attrs else [],
            is_hero=ex.is_hero,
            is_best_seller=bool(product.is_best_seller),
        ))

    return TrendOut(
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
        dominant_patterns=trend.dominant_patterns or [],
        dominant_styles=trend.dominant_styles or [],
        markets=trend.markets or [],
        price_tier=trend.price_tier,
        examples=examples,
    )
