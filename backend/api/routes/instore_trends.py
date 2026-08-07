"""In-store Trend Report API routes.

Parallel to api/routes/reports.py (Online Products trends) but reads from
the InStoreTrend / InStoreTrendReport tables. Source data is the In-store
Products catalogue (InStoreCatalogueItem rows), not the Online Products
table.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc, delete
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
async def get_latest(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(InStoreTrendReport).order_by(desc(InStoreTrendReport.week_start)).limit(1)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="No reports yet")
    return await _build_report_out(report, db)


@router.get("/{report_id}", response_model=InStoreReportOut)
async def get_report(report_id: int, db: AsyncSession = Depends(get_db)):
    report = await db.get(InStoreTrendReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return await _build_report_out(report, db)


@router.post("/generate")
async def generate_report():
    """Trigger a fresh in-store trend analysis."""
    from tasks.analysis_tasks import run_instore_trend_analysis_task
    task = run_instore_trend_analysis_task.apply_async(queue="reports")
    return {"task_id": task.id, "status": "queued"}


@router.post("/regenerate")
async def regenerate_report():
    """Generate a new generation of trends for the current week (Try Again)."""
    from tasks.analysis_tasks import regenerate_instore_trend_analysis_task
    task = regenerate_instore_trend_analysis_task.apply_async(queue="reports")
    return {"task_id": task.id, "status": "queued"}


@router.delete("/clear")
async def clear_all(db: AsyncSession = Depends(get_db)):
    """Delete every in-store trend report, trend, example, and recommendation."""
    await db.execute(delete(InStoreTrendRecommendation))
    await db.execute(delete(InStoreTrendExample))
    await db.execute(delete(InStoreTrend))
    await db.execute(delete(InStoreTrendReport))
    await db.commit()
    return {"status": "cleared"}


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

async def _build_report_out(report: InStoreTrendReport, db: AsyncSession) -> InStoreReportOut:
    if not report.trend_ids:
        return InStoreReportOut(
            id=report.id, week_start=report.week_start, title=report.title,
            summary=report.summary, total_items_analysed=report.total_items_analysed,
            trend_count=0, rising_trends=[], new_trends=[], declining_trends=[],
            all_trends=[], created_at=report.created_at,
        )

    result = await db.execute(
        select(InStoreTrend).where(InStoreTrend.id.in_(report.trend_ids))
        .order_by(desc(InStoreTrend.item_count))
    )
    trends = result.scalars().all()
    trend_ids = [t.id for t in trends]

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
            category=t.category, status=t.status.value,
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
        trend_count=len(trends),
        rising_trends=rising, new_trends=new, declining_trends=declining,
        all_trends=[to_out(t) for t in trends],
        created_at=report.created_at,
    )
