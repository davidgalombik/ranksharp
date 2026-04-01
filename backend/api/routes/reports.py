"""Report API routes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from database.models import TrendReport, Trend, TrendStatus
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

router = APIRouter()


class TrendSummaryOut(BaseModel):
    id: int
    name: str
    category: str
    status: str
    product_count: int
    retailer_count: int
    dominant_colours: list[str]
    dominant_materials: list[str]
    momentum_pct: Optional[float]


class ReportOut(BaseModel):
    id: int
    week_start: datetime
    title: str
    summary: str
    total_products_analysed: int
    retailers_covered: int
    trend_count: int
    rising_trends: list[TrendSummaryOut]
    new_trends: list[TrendSummaryOut]
    declining_trends: list[TrendSummaryOut]
    all_trends: list[TrendSummaryOut]
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/", response_model=list[ReportOut])
async def list_reports(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(TrendReport).order_by(desc(TrendReport.week_start)).limit(limit)
    )
    reports = result.scalars().all()
    return [await _build_report_out(r, db) for r in reports]


@router.get("/latest", response_model=ReportOut)
async def get_latest_report(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(TrendReport).order_by(desc(TrendReport.week_start)).limit(1)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="No reports yet")
    return await _build_report_out(report, db)


@router.get("/{report_id}", response_model=ReportOut)
async def get_report(report_id: int, db: AsyncSession = Depends(get_db)):
    report = await db.get(TrendReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return await _build_report_out(report, db)


@router.post("/generate")
async def generate_report():
    """Manually trigger the weekly trend analysis + report generation."""
    from tasks.analysis_tasks import run_trend_analysis
    # Use the 'reports' queue so the task is not blocked behind product-analysis jobs
    task = run_trend_analysis.apply_async(queue="reports")
    return {"task_id": task.id, "status": "queued"}


async def _build_report_out(report: TrendReport, db: AsyncSession) -> ReportOut:
    if not report.trend_ids:
        return ReportOut(
            id=report.id, week_start=report.week_start, title=report.title,
            summary=report.summary, total_products_analysed=report.total_products_analysed,
            retailers_covered=report.retailers_covered, trend_count=0,
            rising_trends=[], new_trends=[], declining_trends=[], all_trends=[],
            created_at=report.created_at,
        )

    result = await db.execute(
        select(Trend).where(Trend.id.in_(report.trend_ids))
        .order_by(Trend.product_count.desc())
    )
    trends = result.scalars().all()

    def to_summary(t: Trend) -> TrendSummaryOut:
        return TrendSummaryOut(
            id=t.id, name=t.name, category=t.category, status=t.status.value,
            product_count=t.product_count, retailer_count=t.retailer_count,
            dominant_colours=t.dominant_colours or [], dominant_materials=t.dominant_materials or [],
            momentum_pct=t.momentum_pct,
        )

    rising = [to_summary(t) for t in trends if t.status == TrendStatus.RISING]
    new = [to_summary(t) for t in trends if t.status == TrendStatus.NEW]
    declining = [to_summary(t) for t in trends if t.status == TrendStatus.DECLINING]

    return ReportOut(
        id=report.id, week_start=report.week_start, title=report.title,
        summary=report.summary, total_products_analysed=report.total_products_analysed,
        retailers_covered=report.retailers_covered, trend_count=len(trends),
        rising_trends=rising, new_trends=new, declining_trends=declining,
        all_trends=[to_summary(t) for t in trends],
        created_at=report.created_at,
    )
