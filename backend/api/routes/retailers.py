"""Retailer management API routes."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from database.models import Retailer, ScrapeJob, Product, ScrapeStatus
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from tasks.scrape_tasks import scrape_retailer

router = APIRouter()


class RetailerOut(BaseModel):
    id: int
    slug: str
    name: str
    base_url: str
    country: str
    tier: str
    is_active: bool
    product_count: int = 0
    pending_analysis_count: int = 0
    last_scrape: Optional[datetime] = None
    last_scrape_status: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("/", response_model=list[RetailerOut])
async def list_retailers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Retailer).order_by(Retailer.country, Retailer.name))
    retailers = result.scalars().all()

    output = []
    for r in retailers:
        product_count_result = await db.execute(
            select(func.count(Product.id)).where(
                Product.retailer_id == r.id, Product.is_active == True
            )
        )
        product_count = product_count_result.scalar() or 0

        pending_analysis_result = await db.execute(
            select(func.count(Product.id)).where(
                Product.retailer_id == r.id,
                Product.analysis_status.in_([ScrapeStatus.PENDING, ScrapeStatus.FAILED]),
            )
        )
        pending_analysis_count = pending_analysis_result.scalar() or 0

        last_job_result = await db.execute(
            select(ScrapeJob)
            .where(ScrapeJob.retailer_id == r.id)
            .order_by(ScrapeJob.created_at.desc())
            .limit(1)
        )
        last_job = last_job_result.scalar_one_or_none()

        output.append(RetailerOut(
            id=r.id,
            slug=r.slug,
            name=r.name,
            base_url=r.base_url,
            country=r.country,
            tier=r.tier.value,
            is_active=r.is_active,
            product_count=product_count,
            pending_analysis_count=pending_analysis_count,
            last_scrape=last_job.created_at if last_job else None,
            last_scrape_status=last_job.status.value if last_job else None,
        ))
    return output


@router.post("/{retailer_id}/scrape")
async def trigger_scrape(
    retailer_id: int,
    skip_analysis: bool = Query(False, description="Queue scrape without triggering Claude analysis"),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a scrape for a specific retailer."""
    retailer = await db.get(Retailer, retailer_id)
    if not retailer:
        raise HTTPException(status_code=404, detail="Retailer not found")
    task = scrape_retailer.delay(retailer_id, skip_analysis=skip_analysis)
    return {"task_id": task.id, "retailer": retailer.name, "status": "queued", "skip_analysis": skip_analysis}


@router.post("/scrape-all")
async def trigger_scrape_all(
    skip_analysis: bool = Query(False, description="Queue scrapes without triggering Claude analysis"),
):
    """Manually trigger scraping for all active retailers."""
    from tasks.scrape_tasks import scrape_all_retailers
    task = scrape_all_retailers.delay(skip_analysis=skip_analysis)
    return {"task_id": task.id, "status": "queued", "skip_analysis": skip_analysis}


@router.post("/{retailer_id}/analyse")
async def trigger_analyse(retailer_id: int, db: AsyncSession = Depends(get_db)):
    """Queue Claude analysis for all unanalysed (pending/failed) products of a retailer."""
    from tasks.analysis_tasks import analyse_pending_products
    from sqlalchemy import select, func

    retailer = await db.get(Retailer, retailer_id)
    if not retailer:
        raise HTTPException(status_code=404, detail="Retailer not found")

    pending_result = await db.execute(
        select(func.count(Product.id)).where(
            Product.retailer_id == retailer_id,
            Product.analysis_status.in_([ScrapeStatus.PENDING, ScrapeStatus.FAILED]),
        )
    )
    pending_count = pending_result.scalar() or 0

    task = analyse_pending_products.delay(retailer_id=retailer_id)
    return {
        "task_id": task.id,
        "retailer": retailer.name,
        "status": "queued",
        "products_queued": pending_count,
    }


@router.post("/analyse-all")
async def trigger_analyse_all(db: AsyncSession = Depends(get_db)):
    """Queue Claude analysis for all unanalysed products across all retailers."""
    from tasks.analysis_tasks import analyse_pending_products
    from sqlalchemy import select, func

    pending_result = await db.execute(
        select(func.count(Product.id)).where(
            Product.analysis_status.in_([ScrapeStatus.PENDING, ScrapeStatus.FAILED]),
        )
    )
    pending_count = pending_result.scalar() or 0

    task = analyse_pending_products.delay(retailer_id=None)
    return {"task_id": task.id, "status": "queued", "products_queued": pending_count}
