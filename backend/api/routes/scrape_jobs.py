"""Scrape job progress API — powers the live progress dashboard."""
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from database.models import ScrapeJob, Retailer, Product, ScrapeStatus
from pydantic import BaseModel

router = APIRouter()


class ScrapeJobOut(BaseModel):
    job_id: int
    retailer_id: int
    retailer_name: str
    retailer_slug: str
    retailer_country: str
    tier: str
    status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    products_found: int
    products_new: int
    products_updated: int
    total_products_in_db: int
    error_message: Optional[str]
    duration_seconds: Optional[float]

    class Config:
        from_attributes = True


class OverallProgressOut(BaseModel):
    total_retailers: int
    retailers_pending: int
    retailers_running: int
    retailers_done: int
    retailers_failed: int
    total_products_found: int
    total_products_new: int
    jobs: list[ScrapeJobOut]


@router.get("/active", response_model=OverallProgressOut)
async def get_active_progress(db: AsyncSession = Depends(get_db)):
    """
    Returns the latest scrape job per retailer, prioritising any
    currently running jobs. Used by the live progress dashboard.
    """
    # Get all retailers
    retailers_result = await db.execute(
        select(Retailer).where(Retailer.is_active == True).order_by(Retailer.name)
    )
    retailers = retailers_result.scalars().all()

    jobs_out = []

    for retailer in retailers:
        # Get the most recent job for this retailer
        job_result = await db.execute(
            select(ScrapeJob)
            .where(ScrapeJob.retailer_id == retailer.id)
            .order_by(desc(ScrapeJob.created_at))
            .limit(1)
        )
        job = job_result.scalar_one_or_none()

        # Total products in DB for this retailer
        count_result = await db.execute(
            select(func.count(Product.id)).where(
                Product.retailer_id == retailer.id,
                Product.is_active == True,
            )
        )
        total_in_db = count_result.scalar() or 0

        duration = None
        if job and job.started_at and job.finished_at:
            duration = (job.finished_at - job.started_at).total_seconds()
        elif job and job.started_at and job.status == ScrapeStatus.RUNNING:
            duration = (datetime.utcnow() - job.started_at).total_seconds()

        jobs_out.append(ScrapeJobOut(
            job_id=job.id if job else -1,
            retailer_id=retailer.id,
            retailer_name=retailer.name,
            retailer_slug=retailer.slug,
            retailer_country=retailer.country,
            tier=retailer.tier.value,
            status=job.status.value if job else "never_run",
            started_at=job.started_at if job else None,
            finished_at=job.finished_at if job else None,
            products_found=job.products_found if job else 0,
            products_new=job.products_new if job else 0,
            products_updated=job.products_updated if job else 0,
            total_products_in_db=total_in_db,
            error_message=job.error_message if job else None,
            duration_seconds=duration,
        ))

    # Aggregate counts
    statuses = [j.status for j in jobs_out]
    return OverallProgressOut(
        total_retailers=len(jobs_out),
        retailers_pending=statuses.count("pending") + statuses.count("never_run"),
        retailers_running=statuses.count("running"),
        retailers_done=statuses.count("success"),
        retailers_failed=statuses.count("failed"),
        total_products_found=sum(j.products_found for j in jobs_out),
        total_products_new=sum(j.products_new for j in jobs_out),
        jobs=jobs_out,
    )


@router.post("/stop-all")
async def stop_all_scrapes(db: AsyncSession = Depends(get_db)):
    """Cancel all pending and running scrape jobs and purge the scrape queue."""
    import redis as redis_lib
    from config import settings
    from tasks.celery_app import app as celery_app

    # 1. Revoke any active/reserved scrape tasks on workers
    try:
        inspect = celery_app.control.inspect(timeout=2)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        for worker_tasks in list(active.values()) + list(reserved.values()):
            for task in worker_tasks:
                if "scrape" in task.get("name", ""):
                    celery_app.control.revoke(task["id"], terminate=True, signal="SIGTERM")
    except Exception:
        pass  # Best-effort

    # 2. Purge the scrape queue in Redis
    try:
        r = redis_lib.from_url(settings.celery_broker_url)
        r.delete("scrape")
    except Exception:
        pass

    # 3. Mark all pending/running jobs as cancelled in DB
    result = await db.execute(
        select(ScrapeJob).where(
            ScrapeJob.status.in_([ScrapeStatus.PENDING, ScrapeStatus.RUNNING])
        )
    )
    jobs = result.scalars().all()
    for job in jobs:
        job.status = ScrapeStatus.FAILED
        job.error_message = "Stopped by user"
        job.finished_at = datetime.utcnow()

    await db.commit()
    return {"stopped": len(jobs)}


@router.post("/{job_id}/cancel")
async def cancel_scrape_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Cancel a single pending or running scrape job."""
    job = await db.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (ScrapeStatus.PENDING, ScrapeStatus.RUNNING):
        raise HTTPException(status_code=400, detail="Job is not running or pending")
    job.status = ScrapeStatus.FAILED
    job.error_message = "Cancelled by user"
    job.finished_at = datetime.utcnow()
    await db.commit()
    return {"cancelled": job_id}


@router.get("/history/{retailer_id}", response_model=list[ScrapeJobOut])
async def get_retailer_history(retailer_id: int, db: AsyncSession = Depends(get_db)):
    """Last 10 scrape jobs for a specific retailer."""
    retailer = await db.get(Retailer, retailer_id)
    if not retailer:
        return []

    result = await db.execute(
        select(ScrapeJob)
        .where(ScrapeJob.retailer_id == retailer_id)
        .order_by(desc(ScrapeJob.created_at))
        .limit(10)
    )
    jobs = result.scalars().all()

    count_result = await db.execute(
        select(func.count(Product.id)).where(
            Product.retailer_id == retailer_id, Product.is_active == True
        )
    )
    total_in_db = count_result.scalar() or 0

    return [
        ScrapeJobOut(
            job_id=j.id,
            retailer_id=retailer.id,
            retailer_name=retailer.name,
            retailer_slug=retailer.slug,
            retailer_country=retailer.country,
            tier=retailer.tier.value,
            status=j.status.value,
            started_at=j.started_at,
            finished_at=j.finished_at,
            products_found=j.products_found,
            products_new=j.products_new,
            products_updated=j.products_updated,
            total_products_in_db=total_in_db,
            error_message=j.error_message,
            duration_seconds=(
                (j.finished_at - j.started_at).total_seconds()
                if j.started_at and j.finished_at else None
            ),
        )
        for j in jobs
    ]
