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


@router.post("/backfill-voyage-embeddings")
async def trigger_voyage_backfill(
    force: bool = Query(False, description="If true, recompute even when embedding already set"),
    _: bool = Depends(_require_admin),
):
    """One-shot backfill of voyage-3 embeddings across both online products
    and in-store catalogue items. Dispatches two Celery tasks (one per table)
    that batch text into 128-doc Voyage calls. Required after the migration
    from the placeholder hash-based embedding scheme to voyage-3."""
    from tasks.analysis_tasks import backfill_product_embeddings
    from tasks.catalogue_tasks import backfill_catalogue_embeddings
    products_task = backfill_product_embeddings.apply_async(
        args=[force], queue="analysis")
    catalogue_task = backfill_catalogue_embeddings.apply_async(
        args=[force], queue="aldi")
    return {
        "products_task_id": products_task.id,
        "catalogue_task_id": catalogue_task.id,
        "force": force,
        "note": ("Voyage embedding backfill dispatched. Watch the worker logs "
                 "for 'backfill_product_embeddings_progress' (analysis queue) "
                 "and 'backfill_catalogue_embeddings_progress' (aldi queue). "
                 "Cost estimate: ~$1-2 total for a fresh repo via voyage-3."),
    }


@router.get("/embeddings-health")
async def embeddings_health(
    _: bool = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Quick diagnostic — count products + in-store items by embedding state,
    plus run a tiny pgvector similarity probe to verify the operator works."""
    from sqlalchemy import text as sa_text

    counts = {}
    counts["products_total"] = (await db.execute(sa_text(
        "SELECT COUNT(*) FROM products WHERE is_active = TRUE"
    ))).scalar()
    counts["products_with_attrs"] = (await db.execute(sa_text(
        "SELECT COUNT(*) FROM products p "
        "JOIN product_attributes pa ON pa.product_id = p.id "
        "WHERE p.is_active = TRUE"
    ))).scalar()
    counts["products_with_embedding"] = (await db.execute(sa_text(
        "SELECT COUNT(*) FROM products p "
        "JOIN product_attributes pa ON pa.product_id = p.id "
        "WHERE p.is_active = TRUE AND pa.embedding IS NOT NULL"
    ))).scalar()
    counts["instore_items_total"] = (await db.execute(sa_text(
        "SELECT COUNT(*) FROM instore_catalogue_items"
    ))).scalar()
    counts["instore_items_with_embedding"] = (await db.execute(sa_text(
        "SELECT COUNT(*) FROM instore_catalogue_items WHERE embedding IS NOT NULL"
    ))).scalar()

    # Column type probe — use format_type so the result includes the
    # parameterised vector dim (pgvector's atttypmod IS the dim itself,
    # not dim+4 like Postgres VARHDRSZ types).
    dim_probe = (await db.execute(sa_text(
        "SELECT c.relname || '.' || a.attname AS col, "
        "       format_type(a.atttypid, a.atttypmod) AS type "
        "FROM pg_attribute a "
        "JOIN pg_class c ON a.attrelid = c.oid "
        "WHERE c.relname IN ('product_attributes', 'instore_catalogue_items') "
        "AND a.attname = 'embedding' AND a.attnum > 0"
    ))).all()

    # Live pgvector probe: take any product's embedding, do a similarity query
    probe = None
    try:
        sample_emb = (await db.execute(sa_text(
            "SELECT pa.embedding FROM product_attributes pa "
            "WHERE pa.embedding IS NOT NULL LIMIT 1"
        ))).scalar()
        if sample_emb is not None:
            probe_result = (await db.execute(sa_text(
                "SELECT COUNT(*) FROM products p "
                "JOIN product_attributes pa ON pa.product_id = p.id "
                "WHERE p.is_active = TRUE AND pa.embedding IS NOT NULL "
                "AND (pa.embedding <=> CAST(:vec AS vector)) <= 1.5"
            ), {"vec": str(sample_emb)})).scalar()
            probe = {"reachable_via_pgvector": probe_result}
    except Exception as exc:
        probe = {"error": str(exc)[:200]}

    return {
        "counts": counts,
        "column_types": {row[0]: row[1] for row in dim_probe},
        "pgvector_probe": probe,
    }


@router.post("/backfill-instore-recommendations")
async def trigger_instore_recommendations_backfill(
    _: bool = Depends(_require_admin),
):
    """For every trend in the latest in-store trend report, compute the top
    matching online products by cosine similarity over their embeddings.
    Threshold is set by RECOMMENDATION_THRESHOLD in instore_trend_engine.py.
    Replaces any existing recommendations. Use this once after deploying the
    feature, or after adding new online products to refresh the matches."""
    from tasks.analysis_tasks import backfill_instore_recommendations_task
    from analysis.instore_trend_engine import (
        RECOMMENDATION_THRESHOLD, RECOMMENDATIONS_PER_TREND,
    )
    task = backfill_instore_recommendations_task.apply_async(queue="reports")
    return {
        "task_id": task.id,
        "threshold": RECOMMENDATION_THRESHOLD,
        "limit_per_trend": RECOMMENDATIONS_PER_TREND,
        "note": (f"Recommendation backfill dispatched. Walks every trend in "
                 f"the latest report and finds the top {RECOMMENDATIONS_PER_TREND} "
                 f"online products with cosine similarity >= {RECOMMENDATION_THRESHOLD}. "
                 f"Watch the 'reports' worker logs."),
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
