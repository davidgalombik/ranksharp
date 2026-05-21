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
