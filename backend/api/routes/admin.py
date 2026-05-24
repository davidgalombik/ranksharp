"""Admin maintenance endpoints.

Gated behind a shared-secret header (`X-Admin-Token`) sourced from the
`ADMIN_TOKEN` env var so they aren't reachable by random callers who
discover the backend URL through the frontend's network requests.

If `ADMIN_TOKEN` is unset, every admin endpoint returns 503 — so an
accidentally-deployed instance with no token configured is closed by
default rather than open.
"""
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select, update, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from database.models import Retailer, Product

router = APIRouter()


def _require_admin(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=503,
                            detail="Admin endpoints disabled (ADMIN_TOKEN env var not set).")
    if not x_admin_token or x_admin_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Token")
    return True


@router.post("/reset-taxonomy/{retailer_slug}")
async def reset_taxonomy(
    retailer_slug: str,
    include_csv: bool = False,
    _: bool = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Null out `category`, `subcategory`, and `product_segment` for every
    product belonging to this retailer. By default, only scraper-sourced
    products are touched (those with `scrape_job_id IS NOT NULL`) so
    CSV-uploaded products that the user manually categorised are preserved.

    Pass `?include_csv=true` to wipe everything.

    The next scrape repopulates from the catalog at
    `backend/scraper/catalogs/<retailer-slug>.csv`.
    """
    retailer = (await db.execute(
        select(Retailer).where(Retailer.slug == retailer_slug)
    )).scalar_one_or_none()
    if retailer is None:
        raise HTTPException(status_code=404, detail=f"Retailer '{retailer_slug}' not found")

    stmt = update(Product).where(Product.retailer_id == retailer.id)
    if not include_csv:
        stmt = stmt.where(Product.scrape_job_id.is_not(None))
    stmt = stmt.values(category=None, subcategory=None, product_segment=None)

    result = await db.execute(stmt)
    await db.commit()

    return {
        "retailer_slug": retailer.slug,
        "retailer_name": retailer.name,
        "rows_wiped": result.rowcount,
        "include_csv": include_csv,
        "note": ("CSV-uploaded products preserved. Pass ?include_csv=true to wipe those too."
                 if not include_csv else "All products wiped (scraper + CSV)."),
    }


@router.post("/purge-analysis-queue")
async def purge_analysis_queue(
    _: bool = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Purge all queued analyse_product tasks from the Celery 'analysis' queue
    and report active vs inactive pending counts.

    Use when the queue is clogged with stale tasks (e.g. an "Analyse all"
    fired before Historical products were excluded). After purging, re-trigger
    analysis from the Retailers page — the dispatcher now only queues ACTIVE
    products, so the worker stops wasting throughput on Historical items.
    """
    from sqlalchemy import or_
    from database.models import ScrapeStatus

    # Diagnostic counts before purge
    active_pending = (await db.execute(
        select(func.count(Product.id)).where(
            Product.is_active == True,
            Product.analysis_status.in_([ScrapeStatus.PENDING, ScrapeStatus.FAILED]),
        )
    )).scalar_one()
    inactive_pending = (await db.execute(
        select(func.count(Product.id)).where(
            Product.is_active == False,
            Product.analysis_status.in_([ScrapeStatus.PENDING, ScrapeStatus.FAILED]),
        )
    )).scalar_one()

    purged = 0
    try:
        from tasks.celery_app import app as celery_app
        with celery_app.connection_for_write() as conn:
            purged = conn.default_channel.queue_purge("analysis")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Purge failed: {exc}")

    return {
        "purged_tasks": purged,
        "active_pending": active_pending,
        "inactive_pending": inactive_pending,
        "note": ("Queue cleared. Re-trigger analysis from the Retailers page — "
                 "only ACTIVE products will be queued now."),
    }


@router.delete("/products/{retailer_slug}")
async def delete_all_products(
    retailer_slug: str,
    confirm: str = Query(..., description="Must be literal string 'YES' to proceed"),
    _: bool = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete every product (and its dependent rows) for a
    retailer. Destructive — requires `?confirm=YES` as a safety gate.

    Deletes in the right order to satisfy foreign keys:
      product_attributes -> trend_examples -> fragrance_trend_examples -> products
    """
    if confirm != "YES":
        raise HTTPException(status_code=400, detail="Pass ?confirm=YES to proceed")

    retailer = (await db.execute(
        select(Retailer).where(Retailer.slug == retailer_slug)
    )).scalar_one_or_none()
    if retailer is None:
        raise HTTPException(status_code=404, detail=f"Retailer '{retailer_slug}' not found")

    # Subquery for the IDs we're about to delete (so we can cascade manually)
    pid_subq = select(Product.id).where(Product.retailer_id == retailer.id).subquery()

    # 1. Detach from trend examples (both flavours)
    await db.execute(text(
        "DELETE FROM trend_examples WHERE product_id IN "
        "(SELECT id FROM products WHERE retailer_id = :rid)"
    ), {"rid": retailer.id})
    await db.execute(text(
        "DELETE FROM fragrance_trend_examples WHERE product_id IN "
        "(SELECT id FROM products WHERE retailer_id = :rid)"
    ), {"rid": retailer.id})

    # 2. Delete product attributes
    await db.execute(text(
        "DELETE FROM product_attributes WHERE product_id IN "
        "(SELECT id FROM products WHERE retailer_id = :rid)"
    ), {"rid": retailer.id})

    # 3. Delete the products themselves
    result = await db.execute(delete(Product).where(Product.retailer_id == retailer.id))
    deleted = result.rowcount
    await db.commit()

    return {
        "retailer_slug": retailer.slug,
        "retailer_name": retailer.name,
        "products_deleted": deleted,
        "note": "All products (and dependent attributes/trend examples) permanently removed.",
    }
