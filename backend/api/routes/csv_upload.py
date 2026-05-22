"""CSV-based retailer product upload.

Two endpoints, both accept the same multipart file:

- POST /api/retailers/csv-upload/preview — parses, validates, returns summary
  + rejects. No DB writes. Use this first to show the user what's about to
  happen.
- POST /api/retailers/csv-upload/commit — parses, validates, upserts each
  valid row (by retailer_id + url). Auto-queues attribute analysis for every
  new or updated product. Returns the final result.

Design choices (see user conversation):
- If any row references an unknown retailer_slug, the WHOLE upload is rejected.
- If a product with the same URL already exists for that retailer, update it.
- Auto-queue analyse_product for each new/updated row.
- Max 5000 rows per upload.
"""
import csv
import io
import re
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from database.models import Product, Retailer, ScrapeStatus
import structlog

log = structlog.get_logger()
router = APIRouter()

MAX_ROWS_PER_UPLOAD = 20000
MAX_FILE_BYTES = 40 * 1024 * 1024  # 40 MB — generous for 20000 rows

REQUIRED_COLUMNS = {"url", "name", "primary_image_url", "retailer_slug"}
OPTIONAL_COLUMNS = {
    "price", "currency", "category", "subcategory", "product_segment",
    "is_best_seller", "is_new", "has_patent",
    "description", "sku", "brand",
}
ALL_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS

_TRUE_VALUES = {"true", "1", "yes", "y", "t"}
_FALSE_VALUES = {"false", "0", "no", "n", "f", ""}
_PRICE_RE = re.compile(r"[^\d.]")  # strip currency symbols, commas, etc.


class RejectRow(BaseModel):
    row_number: int                # 1-indexed, header = row 1
    url: Optional[str] = None
    reason: str


class PreviewSummary(BaseModel):
    total_rows: int                # rows in the CSV (excluding header)
    valid_rows: int                # rows that would be inserted/updated
    new_count: int                 # rows without an existing match
    update_count: int              # rows matching an existing (retailer_id, url)
    would_deactivate: int = 0      # active products absent from this CSV that
                                   # would move to Historical (per-retailer)
    rejects: list[RejectRow]
    retailers_referenced: list[str]


class CommitSummary(PreviewSummary):
    inserted: int
    updated: int
    deactivated: int = 0           # active products moved to Historical
    analysis_queued: int


def _parse_bool(raw: str | None) -> Optional[bool]:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in _TRUE_VALUES:
        return True
    if s in _FALSE_VALUES:
        # Explicit false-ish values mean false; blank means "leave default"
        return False if s else None
    return None


def _parse_price(raw: str | None) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Strip currency symbols, commas, whitespace
    cleaned = _PRICE_RE.sub("", s)
    if not cleaned or cleaned == ".":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_csv_bytes(data: bytes) -> list[dict]:
    """Read CSV bytes (with BOM tolerance) into a list of lowercased-key dicts."""
    try:
        text = data.decode("utf-8-sig")   # handles Excel BOM
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict] = []
    for r in reader:
        # Normalise headers: lowercase, strip, drop None keys (extra commas in rows)
        clean = {(k or "").strip().lower(): (v or "").strip() if v is not None else ""
                 for k, v in r.items() if k is not None}
        rows.append(clean)
    return rows


async def _load_retailer_map(db: AsyncSession, slugs: set[str]) -> dict[str, Retailer]:
    """Case-insensitive slug lookup. Returned dict is keyed by the ORIGINAL
    (lowercased) CSV slug so callers can match directly without worrying
    about case. Both sides are compared lowercased."""
    if not slugs:
        return {}
    # Normalise the CSV's slugs for comparison
    lowered = {s.lower() for s in slugs}
    result = await db.execute(
        select(Retailer).where(func.lower(Retailer.slug).in_(lowered))
    )
    rows = result.scalars().all()
    return {r.slug.lower(): r for r in rows}


async def _validate_rows(
    rows: list[dict],
    db: AsyncSession,
) -> tuple[list[dict], list[RejectRow], dict[str, Retailer], set[str]]:
    """Validate each row and classify as valid or reject. Returns:
    - valid: list of dicts ready to be upserted, with retailer_id attached
    - rejects: rows with a reason
    - retailer_map: slug → Retailer for everyone referenced
    - missing_slugs: slugs used in the CSV that don't exist in DB
    """
    # First pass: collect referenced slugs (lowercased for case-insensitive match)
    all_slugs = {(r.get("retailer_slug") or "").strip() for r in rows}
    all_slugs.discard("")
    all_slugs_lc = {s.lower() for s in all_slugs}
    retailer_map = await _load_retailer_map(db, all_slugs_lc)
    # Missing = slugs (lowercased) present in CSV but not found in DB
    missing_slugs = all_slugs_lc - set(retailer_map.keys())

    valid: list[dict] = []
    rejects: list[RejectRow] = []

    for idx, row in enumerate(rows, start=2):   # row 1 is header
        url = (row.get("url") or "").strip()
        name = (row.get("name") or "").strip()
        img = (row.get("primary_image_url") or "").strip()
        slug = (row.get("retailer_slug") or "").strip()

        # Missing required fields
        missing = []
        if not url: missing.append("url")
        if not name: missing.append("name")
        if not img: missing.append("primary_image_url")
        if not slug: missing.append("retailer_slug")
        if missing:
            rejects.append(RejectRow(row_number=idx, url=url or None,
                                     reason=f"missing required field(s): {', '.join(missing)}"))
            continue

        # Basic URL sanity
        if not url.startswith(("http://", "https://")):
            rejects.append(RejectRow(row_number=idx, url=url,
                                     reason="url must start with http:// or https://"))
            continue
        if not img.startswith(("http://", "https://")):
            rejects.append(RejectRow(row_number=idx, url=url,
                                     reason="primary_image_url must start with http:// or https://"))
            continue

        # Retailer lookup (case-insensitive)
        retailer = retailer_map.get(slug.lower())
        if not retailer:
            # If the whole-file policy kicks in, this gets rejected at the
            # outer level before commit. For preview we still mark individuals
            # so the user can see which ones.
            rejects.append(RejectRow(row_number=idx, url=url,
                                     reason=f"unknown retailer_slug '{slug}'"))
            continue

        row["_retailer"] = retailer
        row["_price_parsed"] = _parse_price(row.get("price"))
        row["_is_best_seller"] = _parse_bool(row.get("is_best_seller"))
        row["_is_new"] = _parse_bool(row.get("is_new"))
        row["_has_patent"] = _parse_bool(row.get("has_patent"))

        # Category/subcategory/product_segment validation against the taxonomy
        # catalog. If the retailer has a catalog, any provided value(s) must
        # form a valid (category, subcategory, product_segment) path; unknown
        # or mismatched values reject the row.
        from scraper import category_catalog as cc
        row_cat_raw = (row.get("category") or "").strip()
        row_sub_raw = (row.get("subcategory") or "").strip()
        row_seg_raw = (row.get("product_segment") or "").strip()
        if cc.has_catalog(retailer.slug):
            row_cat = cc.resolve_label(retailer.slug, row_cat_raw, kind="category") if row_cat_raw else None
            row_sub = cc.resolve_label(retailer.slug, row_sub_raw, kind="subcategory") if row_sub_raw else None
            row_seg = cc.resolve_label(retailer.slug, row_seg_raw, kind="product_segment") if row_seg_raw else None
            if row_cat_raw and not row_cat:
                rejects.append(RejectRow(row_number=idx, url=url,
                                         reason=f"unknown category '{row_cat_raw}' for retailer '{retailer.slug}'"))
                continue
            if row_sub_raw and not row_sub:
                rejects.append(RejectRow(row_number=idx, url=url,
                                         reason=f"unknown subcategory '{row_sub_raw}' for retailer '{retailer.slug}'"))
                continue
            if row_seg_raw and not row_seg:
                rejects.append(RejectRow(row_number=idx, url=url,
                                         reason=f"unknown product_segment '{row_seg_raw}' for retailer '{retailer.slug}'"))
                continue
            # Validate the path holds together at each provided level
            if row_cat and row_sub and not cc.is_valid(retailer.slug, row_cat, row_sub):
                rejects.append(RejectRow(
                    row_number=idx, url=url,
                    reason=f"subcategory '{row_sub}' is not under category '{row_cat}' "
                           f"for retailer '{retailer.slug}'",
                ))
                continue
            if row_cat and row_sub and row_seg and not cc.is_valid(retailer.slug, row_cat, row_sub, row_seg):
                rejects.append(RejectRow(
                    row_number=idx, url=url,
                    reason=f"product_segment '{row_seg}' is not under '{row_cat}' > '{row_sub}' "
                           f"for retailer '{retailer.slug}'",
                ))
                continue
            # Normalise to canonical display labels for storage
            row["category"] = row_cat or ""
            row["subcategory"] = row_sub or ""
            row["product_segment"] = row_seg or ""

        valid.append(row)

    return valid, rejects, retailer_map, missing_slugs


async def _preflight(
    file: UploadFile,
    db: AsyncSession,
) -> tuple[list[dict], list[RejectRow], dict[str, Retailer], set[str], list[dict]]:
    if not file.filename or not file.filename.lower().endswith((".csv", ".tsv", ".txt")):
        raise HTTPException(status_code=400, detail="File must be a .csv")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail=f"File exceeds {MAX_FILE_BYTES // (1024*1024)} MB")
    rows = _parse_csv_bytes(data)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV appears to be empty or has no data rows")
    if len(rows) > MAX_ROWS_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"CSV has {len(rows)} rows; max is {MAX_ROWS_PER_UPLOAD} per upload. "
                   f"Split the file and try again.",
        )
    # Check headers
    first_headers = set(rows[0].keys())
    missing_required = REQUIRED_COLUMNS - first_headers
    if missing_required:
        raise HTTPException(
            status_code=400,
            detail=f"CSV is missing required column(s): {', '.join(sorted(missing_required))}",
        )
    valid, rejects, retailer_map, missing_slugs = await _validate_rows(rows, db)
    return rows, rejects, retailer_map, missing_slugs, valid


async def _classify_new_vs_update(
    valid: list[dict], db: AsyncSession,
) -> tuple[int, int, set[tuple[int, str]]]:
    """Return (new_count, update_count, existing_keys) where existing_keys
    is a set of (retailer_id, url) that already exist in the products table."""
    if not valid:
        return 0, 0, set()

    # Group by retailer for efficient IN queries
    by_retailer: dict[int, list[str]] = {}
    for row in valid:
        by_retailer.setdefault(row["_retailer"].id, []).append(row["url"])

    existing: set[tuple[int, str]] = set()
    for retailer_id, urls in by_retailer.items():
        # Split into chunks in case of very large URL sets
        for i in range(0, len(urls), 500):
            chunk = urls[i:i+500]
            result = await db.execute(
                select(Product.retailer_id, Product.url).where(
                    Product.retailer_id == retailer_id,
                    Product.url.in_(chunk),
                )
            )
            for rid, url in result.all():
                existing.add((rid, url))

    new_count = 0
    update_count = 0
    for row in valid:
        key = (row["_retailer"].id, row["url"])
        if key in existing:
            update_count += 1
        else:
            new_count += 1
    return new_count, update_count, existing


async def _bulk_fetch_existing(
    valid: list[dict], db: AsyncSession,
) -> dict[tuple[int, str], Product]:
    """Bulk-fetch every existing Product matching (retailer_id, url) for the
    valid CSV rows. Returns a dict keyed by (retailer_id, url) for O(1)
    lookup during commit, replacing one SELECT-per-row with one SELECT
    per-retailer-chunk."""
    by_retailer: dict[int, list[str]] = {}
    for row in valid:
        by_retailer.setdefault(row["_retailer"].id, []).append(row["url"])
    existing: dict[tuple[int, str], Product] = {}
    for retailer_id, urls in by_retailer.items():
        # Chunk to stay well under Postgres' parameter limit
        for i in range(0, len(urls), 500):
            chunk = urls[i:i+500]
            result = await db.execute(
                select(Product).where(
                    Product.retailer_id == retailer_id,
                    Product.url.in_(chunk),
                )
            )
            for product in result.scalars().all():
                existing[(product.retailer_id, product.url)] = product
    return existing


def _urls_by_retailer(valid: list[dict]) -> dict[int, set[str]]:
    """Group the valid CSV rows' URLs by retailer_id. Used by the snapshot
    sweep to scope deactivation per retailer."""
    out: dict[int, set[str]] = {}
    for row in valid:
        out.setdefault(row["_retailer"].id, set()).add(row["url"])
    return out


async def _count_would_deactivate(
    valid: list[dict], db: AsyncSession,
) -> int:
    """Count active products that would be moved to Historical if this CSV
    were committed — i.e. active products for any retailer in the CSV whose
    URL is NOT in the CSV. Computed via Python-side set difference so we
    avoid sending NOT IN with thousands of bind parameters (asyncpg/Postgres
    chokes on those). Read-only; used by /preview."""
    total = 0
    for retailer_id, csv_urls in _urls_by_retailer(valid).items():
        result = await db.execute(
            select(Product.url).where(
                Product.retailer_id == retailer_id,
                Product.is_active == True,
            )
        )
        active_urls = {row[0] for row in result.all()}
        total += len(active_urls - csv_urls)
    return total


# ── /preview ─────────────────────────────────────────────────────────────────

@router.post("/preview", response_model=PreviewSummary)
async def preview_csv_upload(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Parse and validate the CSV. No DB writes. Returns summary + rejects."""
    rows, rejects, retailer_map, missing_slugs, valid = await _preflight(file, db)

    # Policy: if the file references retailer slugs that don't exist, we will
    # refuse to commit. Preview still returns the summary so the user sees it.
    if missing_slugs:
        # Also ensure every row in rejects mentioning this slug is accounted for;
        # _validate_rows already did it.
        pass

    new_count, update_count, _ = await _classify_new_vs_update(valid, db)
    would_deactivate = await _count_would_deactivate(valid, db)

    return PreviewSummary(
        total_rows=len(rows),
        valid_rows=len(valid),
        new_count=new_count,
        update_count=update_count,
        would_deactivate=would_deactivate,
        rejects=rejects,
        retailers_referenced=sorted(list({r["_retailer"].slug for r in valid} | missing_slugs)),
    )


# ── /commit ──────────────────────────────────────────────────────────────────

@router.post("/commit", response_model=CommitSummary)
async def commit_csv_upload(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Parse, validate, and upsert. Rejects the whole file if any row references
    an unknown retailer_slug."""
    rows, rejects, retailer_map, missing_slugs, valid = await _preflight(file, db)

    # Policy: fail whole file if any unknown retailer_slug
    if missing_slugs:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Upload rejected — the CSV references retailer_slug(s) that don't "
                f"exist in the database: {', '.join(sorted(missing_slugs))}. "
                f"Create those retailers first, or fix the slugs in the CSV."
            ),
        )

    # Bulk-fetch existing products once instead of one SELECT per row.
    # Brings 5000-row commits from ~minutes to seconds.
    existing_products = await _bulk_fetch_existing(valid, db)
    new_count = sum(1 for r in valid if (r["_retailer"].id, r["url"]) not in existing_products)
    update_count = len(valid) - new_count

    inserted = 0
    updated = 0
    queued: list[int] = []
    now = datetime.utcnow()
    touched_products: list[Product] = []   # for post-commit ID retrieval

    for row in valid:
        retailer: Retailer = row["_retailer"]
        url = row["url"]
        key = (retailer.id, url)

        product = existing_products.get(key)

        if product is None:
            # Default currency heuristic from country — CSV value (if provided)
            # overrides this below.
            country_currency = {
                "AU": "AUD", "NZ": "NZD", "US": "USD", "CA": "CAD",
                "GB": "GBP", "UK": "GBP", "EU": "EUR",
            }
            default_currency = country_currency.get((retailer.country or "US").upper(), "USD")
            product = Product(
                retailer_id=retailer.id,
                url=url,
                name=row["name"][:1000],
                primary_image_url=row["primary_image_url"][:2000],
                currency=default_currency,
                analysis_status=ScrapeStatus.PENDING,
                is_active=True,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(product)
            inserted += 1
        else:
            # Update path
            product.name = row["name"][:1000]
            product.primary_image_url = row["primary_image_url"][:2000]
            product.last_seen_at = now
            product.is_active = True
            # Existing products appearing in this CSV are no longer "new" —
            # the "new" tag is reserved for rows that didn't previously exist.
            # An explicit is_new=true in the CSV row below will re-promote.
            product.is_new = False
            # Re-analyse since image/details may have changed
            product.analysis_status = ScrapeStatus.PENDING
            updated += 1

        # Optional fields — only set if the CSV provided a non-empty value
        price = row.get("_price_parsed")
        if price is not None:
            product.price = price
        currency = (row.get("currency") or "").strip()
        if currency:
            product.currency = currency[:5].upper()
        category = (row.get("category") or "").strip()
        if category:
            product.category = category[:500]
        subcategory = (row.get("subcategory") or "").strip()
        if subcategory:
            product.subcategory = subcategory[:500]
        product_segment = (row.get("product_segment") or "").strip()
        if product_segment:
            product.product_segment = product_segment[:500]
        desc = (row.get("description") or "").strip()
        if desc:
            product.description = desc
        sku = (row.get("sku") or "").strip()
        if sku:
            product.sku = sku[:200]
        brand = (row.get("brand") or "").strip()
        if brand:
            product.brand = brand[:200]

        # Bools — only override when CSV explicitly set true
        if row.get("_is_best_seller") is True:
            product.is_best_seller = True
        if row.get("_has_patent") is True:
            product.has_patent = True
        if row.get("_is_new") is False:
            product.is_new = False
        elif row.get("_is_new") is True:
            product.is_new = True

        touched_products.append(product)

    # CSV-as-snapshot sweep: per retailer in this CSV, deactivate any active
    # product whose URL is NOT in the CSV. Moves orphaned products from
    # Online -> Historical. Scoped per-retailer so a CSV for X never touches Y.
    #
    # Implementation note: we compute the diff in Python and UPDATE with IN
    # rather than UPDATE WHERE url NOT IN (csv_urls). Building a NOT IN with
    # thousands of bind parameters causes asyncpg/Postgres to fail. The IN
    # list is bounded by the much smaller diff set.
    deactivated = 0
    for retailer_id, csv_urls in _urls_by_retailer(valid).items():
        result = await db.execute(
            select(Product.url).where(
                Product.retailer_id == retailer_id,
                Product.is_active == True,
            )
        )
        active_urls = {row[0] for row in result.all()}
        to_deactivate = list(active_urls - csv_urls)
        if not to_deactivate:
            continue
        # Chunk the UPDATE so an enormous deactivation list still stays
        # within DB driver limits.
        for i in range(0, len(to_deactivate), 500):
            chunk = to_deactivate[i:i+500]
            res = await db.execute(
                update(Product)
                .where(
                    Product.retailer_id == retailer_id,
                    Product.url.in_(chunk),
                )
                .values(is_active=False)
            )
            deactivated += res.rowcount or 0

    await db.commit()

    # After commit, SQLAlchemy populates auto-increment IDs on inserted rows
    # and existing rows already had IDs from the bulk-fetch — so a per-row
    # post-commit SELECT is unnecessary.
    product_ids: list[int] = [p.id for p in touched_products if p.id is not None]

    # Queue analysis — fan out via the existing analyse_pending_products task
    # (one per retailer in the CSV) so the request handler doesn't sit through
    # N apply_async() round-trips to Redis for a large upload.
    try:
        from tasks.analysis_tasks import analyse_pending_products
        for retailer_id in _urls_by_retailer(valid).keys():
            analyse_pending_products.delay(retailer_id=retailer_id)
        queued = product_ids
    except Exception as exc:
        log.warning("csv_upload_analysis_dispatch_failed", error=str(exc))
        queued = []

    log.info(
        "csv_upload_commit",
        inserted=inserted, updated=updated, deactivated=deactivated,
        queued=len(queued), rejects=len(rejects),
    )

    return CommitSummary(
        total_rows=len(rows),
        valid_rows=len(valid),
        new_count=new_count,
        update_count=update_count,
        would_deactivate=deactivated,
        rejects=rejects,
        retailers_referenced=sorted(list({r["_retailer"].slug for r in valid})),
        inserted=inserted,
        updated=updated,
        deactivated=deactivated,
        analysis_queued=len(queued),
    )
