"""Ranksharp Catalogue — products Ranksharp has sold to ALDI (and, later,
other customers).

Data model: RanksharpProduct (one row per SKU) + RanksharpProductSale
(many rows per product, one per PO event). CSV uploads are APPEND-ONLY —
a re-upload never overwrites metadata on an existing SKU, and existing
SKUs not in the new CSV are never deleted. Corrections to product
metadata are Phase 2.

Endpoints in this file:
  POST /csv/preview   — parse + validate a CSV, return summary + rejects
  POST /csv/commit    — parse + validate + append. Never deletes.
  POST /images/upload — bulk (ZIP) or single (SKU + file). PDF → page 1.
  GET  /products                — paginated, searchable, filterable
  GET  /products/{id}           — detail with sale history
  GET  /products/{id}/image     — serve the stored image file
  DELETE /products/{id}         — hard delete (product + all sales)
  DELETE /sales/{id}            — hard delete a single sale record
"""
from __future__ import annotations

import csv
import io
import pathlib
import uuid
import zipfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.db import get_db
from database.models import RanksharpProduct, RanksharpProductSale

import structlog

log = structlog.get_logger()
router = APIRouter()


# ── CSV schema ────────────────────────────────────────────────────────────────

REQUIRED_COLUMNS = {"sku", "name"}
OPTIONAL_COLUMNS = {
    "description", "category", "subcategory",
    "price_wholesale", "price_retail", "currency",
    "units_purchased", "on_sale_date", "customer", "notes",
}
MAX_ROWS_PER_UPLOAD = 20000
MAX_CSV_BYTES = 40 * 1024 * 1024   # 40 MB — plenty for 20k rows
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # per image
MAX_ZIP_BYTES = 400 * 1024 * 1024   # bulk uploads

_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic", "pdf"}
_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S",
    "%d-%m-%Y", "%Y/%m/%d",
)


class RejectRow(BaseModel):
    row_number: int
    sku: Optional[str] = None
    reason: str


class CsvPreviewSummary(BaseModel):
    total_rows: int
    valid_rows: int
    new_products: int         # SKUs not currently in DB
    existing_products: int    # SKUs that will get a new sale row appended
    sale_records: int         # rows the commit will actually insert
    rejects: list[RejectRow]


class CsvCommitSummary(CsvPreviewSummary):
    products_created: int
    sales_created: int


def _parse_date(raw: str) -> Optional[datetime]:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_float(raw: str) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("$", "").replace("£", "").replace("€", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(raw: str) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s:
        return None
    try:
        return int(float(s))  # tolerates "1000.0"
    except ValueError:
        return None


def _read_csv_bytes(data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8-sig")   # tolerate Excel BOM
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict] = []
    for r in reader:
        clean = {
            (k or "").strip().lower(): (v.strip() if isinstance(v, str) else v)
            for k, v in r.items() if k is not None
        }
        rows.append(clean)
    return rows


async def _load_existing_skus(db: AsyncSession, skus: set[str]) -> dict[str, RanksharpProduct]:
    if not skus:
        return {}
    result = await db.execute(
        select(RanksharpProduct).where(RanksharpProduct.sku.in_(list(skus)))
    )
    return {p.sku: p for p in result.scalars().all()}


def _validate_rows(rows: list[dict]) -> tuple[list[dict], list[RejectRow]]:
    """Row-level validation only (no DB access). Returns (parsed_rows, rejects).
    Each parsed row includes normalised fields ready for insert."""
    valid: list[dict] = []
    rejects: list[RejectRow] = []
    for idx, row in enumerate(rows, start=2):
        sku = (row.get("sku") or "").strip()
        name = (row.get("name") or "").strip()
        if not sku:
            rejects.append(RejectRow(row_number=idx, sku=None, reason="missing sku"))
            continue
        if not name:
            rejects.append(RejectRow(row_number=idx, sku=sku, reason="missing name"))
            continue

        parsed = {
            "sku": sku[:200],
            "name": name[:500],
            "description": (row.get("description") or "").strip() or None,
            "category": (row.get("category") or "").strip()[:500] or None,
            "subcategory": (row.get("subcategory") or "").strip()[:500] or None,
            "customer": (row.get("customer") or "").strip()[:200] or "ALDI",
            "price_wholesale": _parse_float(row.get("price_wholesale")),
            "price_retail": _parse_float(row.get("price_retail")),
            "currency": (row.get("currency") or "").strip()[:5].upper() or None,
            "units_purchased": _parse_int(row.get("units_purchased")),
            "on_sale_date": _parse_date(row.get("on_sale_date") or ""),
            "notes": (row.get("notes") or "").strip() or None,
        }
        # Sanity check on date parsing when the buyer provided one but it
        # didn't match any format we recognise.
        if (row.get("on_sale_date") or "").strip() and parsed["on_sale_date"] is None:
            rejects.append(RejectRow(
                row_number=idx, sku=sku,
                reason=f"unparseable on_sale_date '{row['on_sale_date']}' "
                       f"(try YYYY-MM-DD)",
            ))
            continue
        valid.append(parsed)
    return valid, rejects


async def _preflight(file: UploadFile, db: AsyncSession) -> tuple[list[dict], list[RejectRow], dict[str, RanksharpProduct]]:
    if not file.filename or not file.filename.lower().endswith((".csv", ".tsv", ".txt")):
        raise HTTPException(status_code=400, detail="File must be a .csv")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_CSV_BYTES:
        raise HTTPException(status_code=400, detail=f"File exceeds {MAX_CSV_BYTES // (1024*1024)} MB")
    rows = _read_csv_bytes(data)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV appears empty or missing header row")
    if len(rows) > MAX_ROWS_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"CSV has {len(rows)} rows; max is {MAX_ROWS_PER_UPLOAD}. Split and re-upload.",
        )
    missing = REQUIRED_COLUMNS - set(rows[0].keys())
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"CSV missing required column(s): {', '.join(sorted(missing))}",
        )
    valid, rejects = _validate_rows(rows)
    existing = await _load_existing_skus(db, {r["sku"] for r in valid})
    return valid, rejects, existing


# ── /csv/preview ─────────────────────────────────────────────────────────────

@router.post("/csv/preview", response_model=CsvPreviewSummary)
async def preview_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    valid, rejects, existing = await _preflight(file, db)
    seen_skus_in_csv: set[str] = set()
    new_products = 0
    existing_products = 0
    for row in valid:
        sku = row["sku"]
        if sku not in seen_skus_in_csv:
            seen_skus_in_csv.add(sku)
            if sku in existing:
                existing_products += 1
            else:
                new_products += 1
    return CsvPreviewSummary(
        total_rows=len(valid) + len(rejects),
        valid_rows=len(valid),
        new_products=new_products,
        existing_products=existing_products,
        sale_records=len(valid),
        rejects=rejects,
    )


# ── /csv/commit ──────────────────────────────────────────────────────────────

@router.post("/csv/commit", response_model=CsvCommitSummary)
async def commit_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    valid, rejects, existing = await _preflight(file, db)

    # Product upserts (append-only for metadata — existing SKUs untouched).
    products_created = 0
    products_by_sku: dict[str, RanksharpProduct] = dict(existing)
    for row in valid:
        sku = row["sku"]
        if sku in products_by_sku:
            continue
        product = RanksharpProduct(
            sku=sku,
            name=row["name"],
            description=row["description"],
            category=row["category"],
            subcategory=row["subcategory"],
        )
        db.add(product)
        products_by_sku[sku] = product
        products_created += 1

    # Flush so newly-added products get IDs before we build FK-bound sale rows.
    if products_created:
        await db.flush()

    # Sale rows — every valid CSV row becomes one sale record.
    sales_created = 0
    for row in valid:
        product = products_by_sku[row["sku"]]
        # A row with zero meaningful sale data (no price, no units, no date)
        # is treated as a product-only entry — don't create an empty sale.
        has_sale_data = any([
            row["price_wholesale"] is not None,
            row["price_retail"] is not None,
            row["units_purchased"] is not None,
            row["on_sale_date"] is not None,
        ])
        if not has_sale_data:
            continue
        db.add(RanksharpProductSale(
            product_id=product.id,
            customer=row["customer"],
            price_wholesale=row["price_wholesale"],
            price_retail=row["price_retail"],
            currency=row["currency"],
            units_purchased=row["units_purchased"],
            on_sale_date=row["on_sale_date"],
            notes=row["notes"],
        ))
        sales_created += 1

    await db.commit()
    log.info("ranksharp_csv_commit",
             products_created=products_created,
             sales_created=sales_created,
             rejects=len(rejects))

    seen_skus_in_csv: set[str] = set()
    new_products = 0
    existing_products = 0
    for row in valid:
        sku = row["sku"]
        if sku not in seen_skus_in_csv:
            seen_skus_in_csv.add(sku)
            if sku in existing:
                existing_products += 1
            else:
                new_products += 1

    return CsvCommitSummary(
        total_rows=len(valid) + len(rejects),
        valid_rows=len(valid),
        new_products=new_products,
        existing_products=existing_products,
        sale_records=len(valid),
        rejects=rejects,
        products_created=products_created,
        sales_created=sales_created,
    )


# ── Image upload + serving ───────────────────────────────────────────────────

def _image_ext_from_filename(filename: str) -> Optional[str]:
    if "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext if ext in _IMAGE_EXTENSIONS else None


def _sku_from_filename(filename: str) -> str:
    """Extract SKU from a filename like 'RS-1234.jpg' or 'RS_1234.pdf'.
    Returns the stem (everything before the final extension), preserving
    case and internal punctuation."""
    name = pathlib.Path(filename).name
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem.strip()


def _persist_image(sku: str, data: bytes, ext: str) -> tuple[str, str]:
    """Write image bytes to the ranksharp_image_dir. For PDF, extract the
    first page as JPEG. Returns (absolute_path, stored_extension).
    Overwrites any prior image for the same SKU (that's the intent — a
    re-upload replaces)."""
    upload_dir = pathlib.Path(settings.ranksharp_image_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    if ext == "pdf":
        # Extract page 1 as JPEG so browsers can render it.
        # Uses pdf2image (already in requirements — poppler-backed).
        try:
            import io as _io
            from pdf2image import convert_from_bytes
            pages = convert_from_bytes(data, dpi=200, first_page=1, last_page=1)
            if not pages:
                raise ValueError("PDF has no pages")
            out = _io.BytesIO()
            pages[0].convert("RGB").save(out, format="JPEG", quality=90, optimize=True)
            data = out.getvalue()
            ext = "jpg"
        except Exception as exc:
            log.warning("ranksharp_pdf_render_failed", sku=sku, error=str(exc))
            raise HTTPException(
                status_code=400,
                detail=f"Could not render PDF for SKU {sku}: {exc}",
            )
    elif ext == "heic":
        # Transcode HEIC to JPEG (iPhone default) at high quality — same
        # pattern used by the in-store catalogue serve.
        try:
            import io as _io
            from PIL import Image
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except ImportError:
                pass
            img = Image.open(_io.BytesIO(data))
            out = _io.BytesIO()
            img.convert("RGB").save(out, format="JPEG", quality=92, progressive=True, optimize=True)
            data = out.getvalue()
            ext = "jpg"
        except Exception as exc:
            log.warning("ranksharp_heic_transcode_failed", sku=sku, error=str(exc))
            raise HTTPException(status_code=400, detail=f"Could not transcode HEIC for SKU {sku}: {exc}")

    # SKUs may contain path-hostile chars — hash-free but sanitised filename.
    safe_sku = "".join(c if c.isalnum() or c in "-_" else "_" for c in sku)
    path = upload_dir / f"{safe_sku}.{ext}"
    path.write_bytes(data)
    return str(path), ext


class ImageUploadSummary(BaseModel):
    uploaded: int
    skipped_no_matching_sku: list[str]  # filenames that didn't match any SKU
    failed: list[dict]                  # [{filename, sku, reason}]


@router.post("/images/upload", response_model=ImageUploadSummary)
async def upload_images(
    file: UploadFile = File(...),
    sku: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Two modes based on the uploaded file:

    - **ZIP mode** — file is a .zip. Every entry is treated as an image;
      the filename stem (e.g. 'RS-1234.jpg' → 'RS-1234') must match an
      existing SKU in the DB. Files that don't match are reported in
      `skipped_no_matching_sku`.

    - **Single mode** — file is one image. Caller supplies `sku` as a
      form field; the file is bound to that SKU.

    In both modes: PDF uploads are rendered to JPEG page-1; HEIC is
    transcoded to JPEG. An existing image for the same SKU is overwritten
    (buyers correcting a bad image).
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    is_zip = file.filename.lower().endswith(".zip") or file.content_type == "application/zip"

    if is_zip:
        if len(data) > MAX_ZIP_BYTES:
            raise HTTPException(status_code=400, detail=f"ZIP exceeds {MAX_ZIP_BYTES // (1024*1024)} MB")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="File is not a valid ZIP archive")
        # Load every known SKU once, up front — one query beats N.
        all_skus_result = await db.execute(select(RanksharpProduct))
        products_by_sku = {p.sku: p for p in all_skus_result.scalars().all()}

        uploaded = 0
        skipped: list[str] = []
        failed: list[dict] = []
        for entry in zf.infolist():
            if entry.is_dir():
                continue
            filename = entry.filename
            ext = _image_ext_from_filename(filename)
            if ext is None:
                failed.append({"filename": filename, "sku": None,
                               "reason": f"unsupported extension (allowed: {', '.join(sorted(_IMAGE_EXTENSIONS))})"})
                continue
            stem_sku = _sku_from_filename(filename)
            product = products_by_sku.get(stem_sku)
            if product is None:
                skipped.append(filename)
                continue
            entry_data = zf.read(entry)
            if len(entry_data) > MAX_IMAGE_BYTES:
                failed.append({"filename": filename, "sku": stem_sku,
                               "reason": f"image exceeds {MAX_IMAGE_BYTES // (1024*1024)} MB"})
                continue
            try:
                path, stored_ext = _persist_image(stem_sku, entry_data, ext)
                product.image_path = path
                product.image_format = stored_ext
                uploaded += 1
            except HTTPException as e:
                failed.append({"filename": filename, "sku": stem_sku, "reason": e.detail})
            except Exception as e:
                failed.append({"filename": filename, "sku": stem_sku, "reason": str(e)})
        await db.commit()
        log.info("ranksharp_zip_upload", uploaded=uploaded,
                 skipped=len(skipped), failed=len(failed))
        return ImageUploadSummary(uploaded=uploaded,
                                  skipped_no_matching_sku=skipped, failed=failed)

    # Single-image mode
    if not sku:
        raise HTTPException(status_code=400, detail="Single image upload requires a 'sku' form field")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail=f"Image exceeds {MAX_IMAGE_BYTES // (1024*1024)} MB")
    ext = _image_ext_from_filename(file.filename)
    if ext is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported extension. Allowed: {', '.join(sorted(_IMAGE_EXTENSIONS))}",
        )
    product_result = await db.execute(
        select(RanksharpProduct).where(RanksharpProduct.sku == sku.strip())
    )
    product = product_result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail=f"No product with SKU '{sku}'")
    path, stored_ext = _persist_image(sku.strip(), data, ext)
    product.image_path = path
    product.image_format = stored_ext
    await db.commit()
    log.info("ranksharp_image_upload", sku=sku, format=stored_ext)
    return ImageUploadSummary(uploaded=1, skipped_no_matching_sku=[], failed=[])


@router.get("/products/{product_id}/image")
async def get_product_image(product_id: int, db: AsyncSession = Depends(get_db)):
    product = await db.get(RanksharpProduct, product_id)
    if not product or not product.image_path:
        raise HTTPException(status_code=404, detail="No image for this product")
    path = pathlib.Path(product.image_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image file missing on disk")
    ext = (product.image_format or "jpg").lower()
    media = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp",
    }.get(ext, "application/octet-stream")
    return FileResponse(path=str(path), media_type=media,
                        headers={"Cache-Control": "public, max-age=86400"})


# ── Read / browse ────────────────────────────────────────────────────────────

class SaleOut(BaseModel):
    id: int
    customer: str
    price_wholesale: Optional[float] = None
    price_retail: Optional[float] = None
    currency: Optional[str] = None
    units_purchased: Optional[int] = None
    on_sale_date: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime


class ProductListItem(BaseModel):
    id: int
    sku: str
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    has_image: bool
    sale_count: int
    total_units: int
    latest_sale_date: Optional[datetime] = None
    latest_currency: Optional[str] = None
    latest_price_wholesale: Optional[float] = None
    latest_price_retail: Optional[float] = None


class ProductListPage(BaseModel):
    total: int
    items: list[ProductListItem]


class ProductDetail(BaseModel):
    id: int
    sku: str
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    has_image: bool
    created_at: datetime
    updated_at: datetime
    sales: list[SaleOut]


@router.get("/products", response_model=ProductListPage)
async def list_products(
    q: Optional[str] = Query(default=None, description="Search SKU or name"),
    category: Optional[str] = None,
    limit: int = 48,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Paginated Ranksharp catalogue list.

    Aggregates sale data per product so the card can show total units +
    latest price + latest sale date without a second round-trip.
    """
    # Subquery — one row per product with aggregated sale metrics.
    sales_agg = (
        select(
            RanksharpProductSale.product_id.label("product_id"),
            func.count(RanksharpProductSale.id).label("sale_count"),
            func.coalesce(func.sum(RanksharpProductSale.units_purchased), 0).label("total_units"),
            func.max(RanksharpProductSale.on_sale_date).label("latest_sale_date"),
        )
        .group_by(RanksharpProductSale.product_id)
        .subquery()
    )

    base = (
        select(RanksharpProduct, sales_agg.c.sale_count,
               sales_agg.c.total_units, sales_agg.c.latest_sale_date)
        .outerjoin(sales_agg, RanksharpProduct.id == sales_agg.c.product_id)
    )
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(or_(
            RanksharpProduct.sku.ilike(pattern),
            RanksharpProduct.name.ilike(pattern),
        ))
    if category:
        base = base.where(RanksharpProduct.category == category)

    total_row = await db.execute(select(func.count()).select_from(base.subquery()))
    total = total_row.scalar_one() or 0

    page = (
        base
        .order_by(desc(func.coalesce(sales_agg.c.latest_sale_date, RanksharpProduct.created_at)))
        .limit(limit).offset(offset)
    )
    rows = (await db.execute(page)).all()

    # For "latest" price + currency, one small extra query per product is
    # acceptable at PAGE_SIZE = 48. If profiling shows this hot, promote
    # to a JOIN LATERAL later.
    items: list[ProductListItem] = []
    for product, sale_count, total_units, latest_sale_date in rows:
        latest_price_row = None
        if sale_count:
            latest_price_res = await db.execute(
                select(
                    RanksharpProductSale.price_wholesale,
                    RanksharpProductSale.price_retail,
                    RanksharpProductSale.currency,
                )
                .where(RanksharpProductSale.product_id == product.id)
                .order_by(desc(RanksharpProductSale.on_sale_date.nullslast()),
                          desc(RanksharpProductSale.id))
                .limit(1)
            )
            latest_price_row = latest_price_res.one_or_none()
        items.append(ProductListItem(
            id=product.id,
            sku=product.sku,
            name=product.name,
            description=product.description,
            category=product.category,
            subcategory=product.subcategory,
            has_image=bool(product.image_path),
            sale_count=int(sale_count or 0),
            total_units=int(total_units or 0),
            latest_sale_date=latest_sale_date,
            latest_price_wholesale=latest_price_row.price_wholesale if latest_price_row else None,
            latest_price_retail=latest_price_row.price_retail if latest_price_row else None,
            latest_currency=latest_price_row.currency if latest_price_row else None,
        ))

    return ProductListPage(total=total, items=items)


class CategoriesOut(BaseModel):
    categories: list[str]


@router.get("/categories", response_model=CategoriesOut)
async def list_categories(db: AsyncSession = Depends(get_db)):
    """Distinct categories currently in the catalogue — powers the filter
    dropdown without hardcoding a list."""
    result = await db.execute(
        select(RanksharpProduct.category)
        .where(RanksharpProduct.category.isnot(None))
        .distinct()
        .order_by(RanksharpProduct.category)
    )
    cats = [row[0] for row in result.all() if row[0]]
    return CategoriesOut(categories=cats)


@router.get("/products/{product_id}", response_model=ProductDetail)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RanksharpProduct).where(RanksharpProduct.id == product_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    sales_res = await db.execute(
        select(RanksharpProductSale)
        .where(RanksharpProductSale.product_id == product_id)
        .order_by(desc(RanksharpProductSale.on_sale_date.nullslast()),
                  desc(RanksharpProductSale.id))
    )
    sales = sales_res.scalars().all()

    return ProductDetail(
        id=product.id,
        sku=product.sku,
        name=product.name,
        description=product.description,
        category=product.category,
        subcategory=product.subcategory,
        has_image=bool(product.image_path),
        created_at=product.created_at,
        updated_at=product.updated_at,
        sales=[SaleOut(
            id=s.id, customer=s.customer,
            price_wholesale=s.price_wholesale, price_retail=s.price_retail,
            currency=s.currency, units_purchased=s.units_purchased,
            on_sale_date=s.on_sale_date, notes=s.notes, created_at=s.created_at,
        ) for s in sales],
    )


@router.delete("/products/{product_id}")
async def delete_product(product_id: int, db: AsyncSession = Depends(get_db)):
    """Hard delete a product and every sale row (CASCADE). Also removes the
    image file from disk (best effort)."""
    product = await db.get(RanksharpProduct, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.image_path:
        try:
            pathlib.Path(product.image_path).unlink(missing_ok=True)
        except Exception:
            pass
    await db.delete(product)
    await db.commit()
    return {"deleted": True, "id": product_id}


@router.delete("/sales/{sale_id}")
async def delete_sale(sale_id: int, db: AsyncSession = Depends(get_db)):
    """Remove a single sale record. Leaves the product intact."""
    sale = await db.get(RanksharpProductSale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale record not found")
    await db.delete(sale)
    await db.commit()
    return {"deleted": True, "id": sale_id}
