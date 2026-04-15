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

    class Config:
        from_attributes = True


class WeekInfo(BaseModel):
    week: str
    generation_count: int


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
    """List all weeks with fragrance trend data, including generation count per week."""
    result = await db.execute(
        select(FragranceTrend.week_start, func.max(FragranceTrend.generation).label("gen_count"))
        .group_by(FragranceTrend.week_start)
        .order_by(desc(FragranceTrend.week_start))
    )
    return [
        WeekInfo(week=str(row.week_start.date()), generation_count=row.gen_count)
        for row in result.all()
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
    examples_result = await db.execute(
        select(FragranceTrendExample, Product, ProductAttributes, Retailer)
        .join(Product, FragranceTrendExample.product_id == Product.id)
        .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(FragranceTrendExample.trend_id == trend.id)
        .order_by(desc(FragranceTrendExample.is_hero), desc(FragranceTrendExample.relevance_score))
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
        ))

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
    )
