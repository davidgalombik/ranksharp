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
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from database.models import Product, Retailer, ScrapeStatus
import structlog

log = structlog.get_logger()
router = APIRouter()

MAX_ROWS_PER_UPLOAD = 5000
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB — generous for 5000 rows

REQUIRED_COLUMNS = {"url", "name", "primary_image_url", "retailer_slug"}
OPTIONAL_COLUMNS = {
    "price", "currency", "category", "subcategory",
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
    rejects: list[RejectRow]
    retailers_referenced: list[str]


class CommitSummary(PreviewSummary):
    inserted: int
    updated: int
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

        # Category/subcategory validation against the taxonomy catalog.
        # If the retailer has a catalog, any provided category + subcategory
        # must form a valid pair; unknown values reject the row.
        from scraper import category_catalog as cc
        row_cat_raw = (row.get("category") or "").strip()
        row_sub_raw = (row.get("subcategory") or "").strip()
        if cc.has_catalog(retailer.slug):
            row_cat = cc.resolve_label(retailer.slug, row_cat_raw, kind="category") if row_cat_raw else None
            row_sub = cc.resolve_label(retailer.slug, row_sub_raw, kind="subcategory") if row_sub_raw else None
            if row_cat_raw and not row_cat:
                rejects.append(RejectRow(row_number=idx, url=url,
                                         reason=f"unknown category '{row_cat_raw}' for retailer '{retailer.slug}'"))
                continue
            if row_sub_raw and not row_sub:
                rejects.append(RejectRow(row_number=idx, url=url,
                                         reason=f"unknown subcategory '{row_sub_raw}' for retailer '{retailer.slug}'"))
                continue
            if row_cat and row_sub and not cc.is_valid(retailer.slug, row_cat, row_sub):
                rejects.append(RejectRow(
                    row_number=idx, url=url,
                    reason=f"subcategory '{row_sub}' is not under category '{row_cat}' "
                           f"for retailer '{retailer.slug}'",
                ))
                continue
            # Normalise to canonical display labels for storage
            row["category"] = row_cat or ""
            row["subcategory"] = row_sub or ""

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

    return PreviewSummary(
        total_rows=len(rows),
        valid_rows=len(valid),
        new_count=new_count,
        update_count=update_count,
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

    new_count, update_count, existing_keys = await _classify_new_vs_update(valid, db)

    inserted = 0
    updated = 0
    queued: list[int] = []
    now = datetime.utcnow()

    for row in valid:
        retailer: Retailer = row["_retailer"]
        url = row["url"]
        key = (retailer.id, url)

        # Grab existing record (for update path)
        product = None
        if key in existing_keys:
            result = await db.execute(
                select(Product).where(
                    Product.retailer_id == retailer.id,
                    Product.url == url,
                )
            )
            product = result.scalar_one_or_none()

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

    await db.commit()

    # Re-fetch products to get their ids (especially for new rows)
    # We'll dispatch analysis tasks after commit so we have persisted IDs.
    product_ids: list[int] = []
    for row in valid:
        key = (row["_retailer"].id, row["url"])
        result = await db.execute(
            select(Product.id).where(
                Product.retailer_id == row["_retailer"].id,
                Product.url == row["url"],
            )
        )
        pid = result.scalar_one_or_none()
        if pid is not None:
            product_ids.append(pid)

    # Queue analysis
    try:
        from tasks.analysis_tasks import analyse_product
        from datetime import timedelta
        # Pace dispatch at 5/sec so a 5000-row upload doesn't saturate Anthropic
        for i, pid in enumerate(product_ids):
            eta = now + timedelta(milliseconds=i * 200)
            analyse_product.apply_async(args=[pid], eta=eta)
        queued = product_ids
    except Exception as exc:
        log.warning("csv_upload_analysis_dispatch_failed", error=str(exc))
        queued = []

    log.info(
        "csv_upload_commit",
        inserted=inserted, updated=updated, queued=len(queued),
        rejects=len(rejects),
    )

    return CommitSummary(
        total_rows=len(rows),
        valid_rows=len(valid),
        new_count=new_count,
        update_count=update_count,
        rejects=rejects,
        retailers_referenced=sorted(list({r["_retailer"].slug for r in valid})),
        inserted=inserted,
        updated=updated,
        analysis_queued=len(queued),
    )
