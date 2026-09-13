"""Retailer management API routes."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from database.models import Retailer, ScrapeJob, Product, ScrapeStatus, ScrapeTier
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
    # 'luxury' / 'middle' / 'mass' / None (unclassified). Drives which
    # segmented trend runs this retailer's products participate in.
    market_segment: Optional[str] = None
    tier: str
    adapter_class: str = ""
    is_active: bool
    product_count: int = 0
    pending_analysis_count: int = 0
    last_scrape: Optional[datetime] = None
    last_scrape_status: Optional[str] = None

    class Config:
        from_attributes = True


class SegmentUpdate(BaseModel):
    market_segment: Optional[str] = None  # None clears the classification


@router.get("/", response_model=list[RetailerOut])
async def list_retailers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Retailer).order_by(func.lower(Retailer.name)))
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
                Product.is_active == True,
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
            market_segment=r.market_segment,
            tier=r.tier.value,
            adapter_class=r.adapter_class or "",
            is_active=r.is_active,
            product_count=product_count,
            pending_analysis_count=pending_analysis_count,
            last_scrape=last_job.created_at if last_job else None,
            last_scrape_status=last_job.status.value if last_job else None,
        ))
    return output


_ALLOWED_SEGMENTS = {"luxury", "middle", "mass"}
_ALLOWED_COUNTRIES = {"US", "AU", "GB", "EU"}


class RetailerCreate(BaseModel):
    name: str
    base_url: str
    country: str = "US"
    market_segment: Optional[str] = None  # luxury / middle / mass / None


def _slugify(name: str) -> str:
    """'McGee & Co.' → 'mcgee-and-co'. Mirrors the client-side preview."""
    import re
    s = name.strip().lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s or "retailer")[:80]


@router.post("/", response_model=RetailerOut, status_code=201)
async def create_retailer(body: RetailerCreate, db: AsyncSession = Depends(get_db)):
    """Create a CSV-fed retailer — no scraper, products arrive via
    /csv-upload. Slug is generated from the name and de-duplicated with a
    numeric suffix. The slug is what goes in the CSV's `retailer_slug` column."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    base_url = body.base_url.strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")
    if not base_url.lower().startswith(("http://", "https://")):
        base_url = "https://" + base_url
    country = (body.country or "US").strip().upper()
    if country not in _ALLOWED_COUNTRIES:
        raise HTTPException(status_code=400, detail=f"country must be one of {sorted(_ALLOWED_COUNTRIES)}")
    segment = body.market_segment.strip().lower() if body.market_segment else None
    if segment is not None and segment not in _ALLOWED_SEGMENTS:
        raise HTTPException(status_code=400, detail=f"market_segment must be one of {sorted(_ALLOWED_SEGMENTS)} or null")

    base_slug = _slugify(name)
    slug, n = base_slug, 2
    while (await db.execute(select(Retailer).where(Retailer.slug == slug))).scalar_one_or_none():
        slug = f"{base_slug}-{n}"
        n += 1

    r = Retailer(
        slug=slug, name=name, base_url=base_url, country=country,
        market_segment=segment, tier=ScrapeTier.CSV, adapter_class="csv",
        is_active=True, categories={},
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return RetailerOut(
        id=r.id, slug=r.slug, name=r.name, base_url=r.base_url, country=r.country,
        market_segment=r.market_segment, tier=r.tier.value, adapter_class=r.adapter_class,
        is_active=r.is_active,
    )


@router.patch("/{retailer_id}/segment", response_model=dict)
async def update_market_segment(
    retailer_id: int,
    body: SegmentUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Set / clear the market_segment for a retailer.

    Pass `{"market_segment": "luxury"}` to classify.
    Pass `{"market_segment": null}` to un-classify (retailer drops out of
    segmented trend runs until re-classified).
    """
    retailer = await db.get(Retailer, retailer_id)
    if not retailer:
        raise HTTPException(status_code=404, detail="Retailer not found")
    if body.market_segment is not None and body.market_segment not in _ALLOWED_SEGMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"market_segment must be one of {sorted(_ALLOWED_SEGMENTS)} or null",
        )
    retailer.market_segment = body.market_segment
    await db.commit()
    return {"id": retailer.id, "market_segment": retailer.market_segment}


@router.get("/{slug}/categories")
async def get_retailer_categories(slug: str, db: AsyncSession = Depends(get_db)):
    """Return distinct product categories for a given retailer slug."""
    result = await db.execute(
        select(distinct(Product.category))
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(Retailer.slug == slug, Product.is_active == True, Product.category.isnot(None), Product.category != "")
        .order_by(Product.category)
    )
    categories = [row[0] for row in result.all()]
    return categories


@router.get("/{slug}/taxonomy")
async def get_retailer_taxonomy(slug: str):
    """Return the catalog-defined category → subcategory tree for a retailer.

    UI uses this to drive cascading Category → Subcategory dropdowns on
    Online Products. When `has_catalog` is False, callers fall back to the
    legacy /categories endpoint (DB-derived strings)."""
    from scraper import category_catalog as cc
    return {
        "has_catalog": cc.has_catalog(slug),
        "tree": cc.get_tree(slug),
    }


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
    if retailer.tier == ScrapeTier.CSV:
        raise HTTPException(
            status_code=400,
            detail=f"{retailer.name} is CSV-fed — upload products via CSV instead of scraping.",
        )
    task = scrape_retailer.delay(retailer_id, skip_analysis=skip_analysis)
    return {"task_id": task.id, "retailer": retailer.name, "status": "queued", "skip_analysis": skip_analysis}


@router.delete("/locks")
async def clear_all_scrape_locks():
    """Clear all Redis scrape locks (use when a lock is stuck after a crash)."""
    import redis as redis_lib
    from config import settings
    r = redis_lib.from_url(settings.redis_url, decode_responses=True)
    keys = r.keys("scrape_lock:*")
    if keys:
        r.delete(*keys)
    return {"cleared": keys}


@router.delete("/locks/{slug}")
async def clear_scrape_lock(slug: str):
    """Clear the Redis scrape lock for a specific retailer slug."""
    import redis as redis_lib
    from config import settings
    r = redis_lib.from_url(settings.redis_url, decode_responses=True)
    key = f"scrape_lock:{slug}"
    existed = r.delete(key)
    return {"slug": slug, "cleared": bool(existed)}


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
            Product.is_active == True,
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
            Product.is_active == True,
            Product.analysis_status.in_([ScrapeStatus.PENDING, ScrapeStatus.FAILED]),
        )
    )
    pending_count = pending_result.scalar() or 0

    task = analyse_pending_products.delay(retailer_id=None)
    return {"task_id": task.id, "status": "queued", "products_queued": pending_count}
