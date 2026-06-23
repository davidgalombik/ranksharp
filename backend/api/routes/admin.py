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
from sqlalchemy import select, update, delete, text, func
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


@router.post("/backfill-instore-embeddings")
async def trigger_instore_embedding_backfill(
    force: bool = Query(False, description="If true, recompute embedding even when one is already set"),
    _: bool = Depends(_require_admin),
):
    """One-shot Celery task: compute the 1536-dim keyword embedding for every
    InStoreCatalogueItem (or only those with NULL embedding by default).
    Required before InStoreTrendEngine can cluster — items with NULL embedding
    are excluded from the trend run."""
    from tasks.catalogue_tasks import backfill_catalogue_embeddings
    task = backfill_catalogue_embeddings.delay(force=force)
    return {
        "task_id": task.id,
        "force": force,
        "note": ("Embedding backfill dispatched. Watch dynamic-reprieve logs for "
                 "embed_catalogue_item completions. Items already embedded are "
                 "skipped unless force=true."),
    }


@router.post("/backfill-instore-recommendations")
async def trigger_instore_recommendations_backfill(
    _: bool = Depends(_require_admin),
):
    """For every trend in the latest in-store trend report, compute the top
    matching online products (cosine similarity over embeddings, >= 0.7).
    Replaces any existing recommendations. Use this once after deploying the
    feature, or after adding new online products to refresh the matches."""
    from tasks.analysis_tasks import backfill_instore_recommendations_task
    task = backfill_instore_recommendations_task.apply_async(queue="reports")
    return {
        "task_id": task.id,
        "note": ("Recommendation backfill dispatched. Walks every trend in the "
                 "latest report and finds the top 10 online products with "
                 "cosine similarity >= 0.7. Watch the 'reports' worker logs."),
    }


@router.post("/backfill-instore-taxonomy")
async def trigger_instore_taxonomy_backfill(
    force: bool = Query(False, description="If true, re-classify every item even if already fully classified"),
    _: bool = Depends(_require_admin),
):
    """Kick off the one-shot Celery task that walks every InStoreCatalogueItem
    and re-classifies it into the new 3-level taxonomy (text-only Claude call,
    no vision). Returns immediately; progress visible in the dynamic-reprieve
    worker logs."""
    from tasks.catalogue_tasks import backfill_catalogue_taxonomy
    task = backfill_catalogue_taxonomy.delay(force=force)
    return {
        "task_id": task.id,
        "force": force,
        "note": ("Backfill dispatched. Watch dynamic-reprieve logs for "
                 "reclassify_catalogue_item completions. Items already fully "
                 "classified are skipped unless force=true."),
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
