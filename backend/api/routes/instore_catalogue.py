"""API routes for the In-store Products catalogue (standalone — no sessions)."""
import os
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select, desc, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from database.db import AsyncSessionLocal
from database.models import InStoreCatalogueImage, InStoreCatalogueItem
from config import settings
import structlog

log = structlog.get_logger()
router = APIRouter()

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/heic": "heic",
    "image/heif": "heic",
    "application/pdf": "pdf",
}
EXT_MAP = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "pdf": "pdf", "heic": "heic", "heif": "heic"}
MAX_FILE_SIZE = 30 * 1024 * 1024   # 30 MB (room for pre-downscale slip-through)
MAX_FILES_PER_BATCH = 200
# In-store items now use the shared 3-level taxonomy. Imported lazily in the
# endpoints below via `from scraper import category_catalog as cc`.


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_batch(
    files: list[UploadFile] = File(...),
    hashes: list[str] = Form(...),
    retailer: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Upload a batch of images. `hashes` must be a parallel list of SHA-256 hashes
    (hex) computed client-side. Duplicates (matching hash already in DB) are skipped.
    `retailer` is an optional free-text tag saved on every image in the batch."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) != len(hashes):
        raise HTTPException(status_code=400, detail="files and hashes length mismatch")
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(status_code=400, detail=f"Max {MAX_FILES_PER_BATCH} files per batch")

    # Normalise retailer — trim, collapse internal whitespace, cap length
    retailer_clean = " ".join((retailer or "").split())[:100] or None

    upload_dir = Path(settings.instore_catalogue_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Pre-check existing hashes in one query to cheaply dedupe
    hash_rows = await db.execute(
        select(InStoreCatalogueImage.sha256_hash).where(
            InStoreCatalogueImage.sha256_hash.in_(hashes)
        )
    )
    existing = {h for (h,) in hash_rows.all()}

    added_images: list[InStoreCatalogueImage] = []
    added_bytes: list[bytes] = []
    skipped_dupe = 0
    skipped_bad = 0

    for file, h in zip(files, hashes):
        if not h or len(h) != 64:
            skipped_bad += 1
            continue
        if h in existing:
            skipped_dupe += 1
            continue
        existing.add(h)  # dedupe within this batch too

        file_type = ALLOWED_CONTENT_TYPES.get(file.content_type or "")
        if not file_type:
            ext = (file.filename or "").rsplit(".", 1)[-1].lower()
            file_type = EXT_MAP.get(ext)
        if not file_type:
            skipped_bad += 1
            continue

        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE or not contents:
            skipped_bad += 1
            continue

        fname = f"{uuid.uuid4()}.{file_type}"
        fpath = upload_dir / fname
        try:
            fpath.write_bytes(contents)
        except Exception as exc:
            log.error("catalogue_write_failed", error=str(exc))
            skipped_bad += 1
            continue

        image = InStoreCatalogueImage(
            filename=file.filename or fname,
            file_path=str(fpath),
            file_type=file_type,
            sha256_hash=h,
            status="pending",
            retailer=retailer_clean,
        )
        db.add(image)
        added_images.append(image)
        added_bytes.append(contents)

    if not added_images:
        # Nothing new — that's fine, not an error.
        return {
            "added": 0,
            "skipped_duplicate": skipped_dupe,
            "skipped_invalid": skipped_bad,
            "image_ids": [],
        }

    await db.commit()
    for img in added_images:
        await db.refresh(img)

    # Dispatch Celery analysis with 200ms pacing (5/sec) — reuses existing pattern
    from tasks.catalogue_tasks import analyse_catalogue_image
    import base64 as _b64
    from datetime import datetime as _dt, timedelta as _td
    for i, (img, raw) in enumerate(zip(added_images, added_bytes)):
        eta = _dt.utcnow() + _td(milliseconds=i * 200)
        analyse_catalogue_image.apply_async(
            args=[img.id],
            kwargs={"file_b64": _b64.b64encode(raw).decode()},
            eta=eta,
        )

    return {
        "added": len(added_images),
        "skipped_duplicate": skipped_dupe,
        "skipped_invalid": skipped_bad,
        "image_ids": [img.id for img in added_images],
    }


# ── List / Search ─────────────────────────────────────────────────────────────

PROMINENCE_VALUES = {"hero", "main", "peripheral", "background"}
# Default list excludes peripheral+background — i.e. only things that are clearly
# part of the framed display. Pass show_all=true to see everything.
DEFAULT_PROMINENCE = {"hero", "main"}


@router.get("/")
async def list_items(
    q: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    product_segment: Optional[str] = None,
    uncategorised_only: bool = False,    # surface items where category IS NULL
    retailer: Optional[str] = None,     # exact match; use '__none__' to filter where retailer IS NULL
    prominence: Optional[str] = None,   # e.g. "hero" or "hero,main" — overrides default
    show_all: bool = False,             # convenience: include peripheral/background too
    status: Optional[str] = None,       # 'failed' | 'pending' | 'analysing' | 'done'
    limit: int = Query(default=60, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Search the catalogue. Default: returns one row per *detected item*
    (not per image), and only items flagged hero/main by the AI. Pass
    show_all=true to include peripheral and background items."""
    stmt = (
        select(InStoreCatalogueItem, InStoreCatalogueImage)
        .join(InStoreCatalogueImage, InStoreCatalogueItem.image_id == InStoreCatalogueImage.id)
        .order_by(desc(InStoreCatalogueItem.created_at))
    )
    count_stmt = (
        select(func.count())
        .select_from(InStoreCatalogueItem)
        .join(InStoreCatalogueImage, InStoreCatalogueItem.image_id == InStoreCatalogueImage.id)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(InStoreCatalogueItem.product_name.ilike(like))
        count_stmt = count_stmt.where(InStoreCatalogueItem.product_name.ilike(like))
    if category:
        stmt = stmt.where(InStoreCatalogueItem.category == category)
        count_stmt = count_stmt.where(InStoreCatalogueItem.category == category)
    if subcategory:
        stmt = stmt.where(InStoreCatalogueItem.subcategory == subcategory)
        count_stmt = count_stmt.where(InStoreCatalogueItem.subcategory == subcategory)
    if product_segment:
        stmt = stmt.where(InStoreCatalogueItem.product_segment == product_segment)
        count_stmt = count_stmt.where(InStoreCatalogueItem.product_segment == product_segment)
    if uncategorised_only:
        stmt = stmt.where(InStoreCatalogueItem.category.is_(None))
        count_stmt = count_stmt.where(InStoreCatalogueItem.category.is_(None))
    if retailer:
        if retailer == "__none__":
            stmt = stmt.where(InStoreCatalogueImage.retailer.is_(None))
            count_stmt = count_stmt.where(InStoreCatalogueImage.retailer.is_(None))
        else:
            stmt = stmt.where(InStoreCatalogueImage.retailer == retailer)
            count_stmt = count_stmt.where(InStoreCatalogueImage.retailer == retailer)

    # Prominence filtering — explicit override wins, else show_all toggle, else defaults
    if prominence:
        requested = {p.strip().lower() for p in prominence.split(",") if p.strip().lower() in PROMINENCE_VALUES}
        if requested:
            # Include NULLs when default set is requested, so pre-prominence rows don't disappear
            if requested == DEFAULT_PROMINENCE:
                cond = or_(
                    InStoreCatalogueItem.prominence.in_(requested),
                    InStoreCatalogueItem.prominence.is_(None),
                )
            else:
                cond = InStoreCatalogueItem.prominence.in_(requested)
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
    elif not show_all:
        # Default: hero + main only, but keep rows analysed before prominence existed (NULL)
        cond = or_(
            InStoreCatalogueItem.prominence.in_(DEFAULT_PROMINENCE),
            InStoreCatalogueItem.prominence.is_(None),
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    stmt = stmt.limit(limit).offset(offset)

    rows = (await db.execute(stmt)).all()
    total = (await db.execute(count_stmt)).scalar_one()

    items = [
        {
            "id": item.id,
            "image_id": item.image_id,
            "product_name": item.product_name,
            "category": item.category,
            "subcategory": item.subcategory,
            "product_segment": item.product_segment,
            "prominence": item.prominence,
            "has_crop": bool(item.cropped_file_path),
            "colours": item.colours or [],
            "materials": item.materials or [],
            "patterns": item.patterns or [],
            "style_tags": item.style_tags or [],
            "confidence": item.confidence,
            "source_filename": image.filename,
            "retailer": image.retailer,
            "created_at": item.created_at,
        }
        for item, image in rows
    ]
    return {"total": total, "items": items}


# ── Facets — count per category given current filters ────────────────────────

@router.get("/facets")
async def get_facets(
    q: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    product_segment: Optional[str] = None,
    uncategorised_only: bool = False,
    retailer: Optional[str] = None,
    show_all: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Count of items per (category, subcategory, product_segment) given
    current filters, for the cascading dropdowns.

    Each facet's own filter is excluded from its own count (and the deeper
    levels exclude their own and the levels below), so picking Category
    narrows the Subcategory list, picking Subcategory narrows Product
    Segment, etc. — same scoping as the Online Products facets endpoint."""

    def _base(exclude: set[str]):
        stmt = (
            select(InStoreCatalogueItem)
            .select_from(InStoreCatalogueItem)
            .join(InStoreCatalogueImage, InStoreCatalogueItem.image_id == InStoreCatalogueImage.id)
        )
        if q:
            stmt = stmt.where(InStoreCatalogueItem.product_name.ilike(f"%{q}%"))
        if retailer:
            if retailer == "__none__":
                stmt = stmt.where(InStoreCatalogueImage.retailer.is_(None))
            else:
                stmt = stmt.where(InStoreCatalogueImage.retailer == retailer)
        if not show_all:
            stmt = stmt.where(or_(
                InStoreCatalogueItem.prominence.in_(DEFAULT_PROMINENCE),
                InStoreCatalogueItem.prominence.is_(None),
            ))
        if category and "category" not in exclude:
            stmt = stmt.where(InStoreCatalogueItem.category == category)
        if subcategory and "subcategory" not in exclude:
            stmt = stmt.where(InStoreCatalogueItem.subcategory == subcategory)
        if product_segment and "product_segment" not in exclude:
            stmt = stmt.where(InStoreCatalogueItem.product_segment == product_segment)
        if uncategorised_only and "uncategorised_only" not in exclude:
            stmt = stmt.where(InStoreCatalogueItem.category.is_(None))
        return stmt

    def _group_count(col, exclude: set[str]):
        sub = _base(exclude).subquery()
        return select(getattr(sub.c, col.key), func.count()).group_by(getattr(sub.c, col.key))

    cat_rows = (await db.execute(_group_count(
        InStoreCatalogueItem.category, {"category", "subcategory", "product_segment"},
    ))).all()
    categories = {c: n for c, n in cat_rows if c}

    sub_rows = (await db.execute(_group_count(
        InStoreCatalogueItem.subcategory, {"subcategory", "product_segment"},
    ))).all()
    subcategories = {s: n for s, n in sub_rows if s}

    seg_rows = (await db.execute(_group_count(
        InStoreCatalogueItem.product_segment, {"product_segment"},
    ))).all()
    product_segments = {s: n for s, n in seg_rows if s}

    # Count of NULL-category items reachable under the other filters — used
    # to hide the Uncategorised toggle when there's nothing to surface.
    uncat_stmt = _base({"uncategorised_only"}).where(
        InStoreCatalogueItem.category.is_(None)
    )
    uncategorised_ct = (await db.execute(
        select(func.count()).select_from(uncat_stmt.subquery())
    )).scalar_one()

    return {
        "categories": categories,
        "subcategories": subcategories,
        "product_segments": product_segments,
        "uncategorised": uncategorised_ct,
    }


# ── Taxonomy tree (for cascading dropdowns) ──────────────────────────────────

@router.get("/taxonomy")
async def get_taxonomy():
    """Return the shared 3-level taxonomy tree (Category -> Subcategory ->
    Product Segment). In-store items use the shared catalog regardless of
    retailer, unlike Online Products where it's per-retailer."""
    from scraper import category_catalog as cc
    return {"tree": cc.get_shared_tree()}


# ── Per-item cropped image ────────────────────────────────────────────────────

@router.get("/items/{item_id}/image")
async def get_item_image(item_id: int, db: AsyncSession = Depends(get_db)):
    """Serve the cropped thumbnail for a detected item. Falls back to the
    parent image (HEIC-converted if needed) when no crop exists."""
    item = await db.get(InStoreCatalogueItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")

    if item.cropped_file_path:
        p = Path(item.cropped_file_path)
        if p.exists():
            return FileResponse(
                item.cropped_file_path,
                media_type="image/jpeg",
                headers={"Content-Disposition": "inline", "Cache-Control": "public, max-age=86400"},
            )

    # Fallback: serve the parent image (reuses the same conversion logic)
    image = await db.get(InStoreCatalogueImage, item.image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Parent image missing")
    path = Path(image.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")

    if image.file_type == "heic":
        import io
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        data = path.read_bytes()
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        return Response(content=buf.getvalue(), media_type="image/jpeg",
                        headers={"Content-Disposition": "inline",
                                 "Cache-Control": "public, max-age=86400"})

    media_types = {"jpeg": "image/jpeg", "png": "image/png", "pdf": "application/pdf"}
    return FileResponse(
        image.file_path,
        media_type=media_types.get(image.file_type, "application/octet-stream"),
        headers={"Content-Disposition": "inline", "Cache-Control": "public, max-age=86400"},
    )


# ── Retailers endpoint — populate the filter dropdown + autocomplete ──────────

@router.get("/retailers")
async def list_retailers(db: AsyncSession = Depends(get_db)):
    """Distinct retailers used in the catalogue, with image counts.
    Sorted alphabetically. Also returns `untagged_count` for images with no retailer."""
    rows = await db.execute(
        select(InStoreCatalogueImage.retailer, func.count())
        .group_by(InStoreCatalogueImage.retailer)
        .order_by(InStoreCatalogueImage.retailer)
    )
    named: list[dict] = []
    untagged = 0
    for name, count in rows.all():
        if name is None:
            untagged = count
        else:
            named.append({"name": name, "count": count})
    return {"retailers": named, "untagged_count": untagged}


# ── Images endpoint — primary image-centric list (one row per uploaded photo) ──

SAMPLE_ITEMS_PER_IMAGE = 6   # how many detected products to include per image in the preview


def _build_item_filter(
    q: Optional[str],
    category: Optional[str],
    subcategory: Optional[str],
    product_segment: Optional[str],
    uncategorised_only: bool,
    prominence: Optional[str],
    show_all: bool,
):
    """Return a list of SQL conditions to apply to the InStoreCatalogueItem rows.

    Also returns a boolean `product_filter_active` — when True the main image
    list should be restricted to images that have at least one matching item.
    """
    conds: list = []
    active = False

    if q:
        conds.append(InStoreCatalogueItem.product_name.ilike(f"%{q}%"))
        active = True
    if category:
        conds.append(InStoreCatalogueItem.category == category)
        active = True
    if subcategory:
        conds.append(InStoreCatalogueItem.subcategory == subcategory)
        active = True
    if product_segment:
        conds.append(InStoreCatalogueItem.product_segment == product_segment)
        active = True
    if uncategorised_only:
        conds.append(InStoreCatalogueItem.category.is_(None))
        active = True

    if prominence:
        requested = {p.strip().lower() for p in prominence.split(",") if p.strip().lower() in PROMINENCE_VALUES}
        if requested:
            if requested == DEFAULT_PROMINENCE:
                conds.append(or_(
                    InStoreCatalogueItem.prominence.in_(requested),
                    InStoreCatalogueItem.prominence.is_(None),
                ))
            else:
                conds.append(InStoreCatalogueItem.prominence.in_(requested))
            # Only flag as "active" if it's narrower than the default
            if requested != DEFAULT_PROMINENCE:
                active = True
    elif not show_all:
        conds.append(or_(
            InStoreCatalogueItem.prominence.in_(DEFAULT_PROMINENCE),
            InStoreCatalogueItem.prominence.is_(None),
        ))
        # Not "active" — this is the default, we don't want to exclude
        # images that only have peripheral/background items from appearing
        # (they'd just have zero matching items in their preview).

    return conds, active


@router.get("/images")
async def list_images(
    q: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    product_segment: Optional[str] = None,
    uncategorised_only: bool = False,
    retailer: Optional[str] = None,
    prominence: Optional[str] = None,
    show_all: bool = False,
    status: Optional[str] = None,
    limit: int = Query(default=60, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Image-centric list — one row per uploaded photo, with a preview of detected products.

    Filters `q`, `category`, `subcategory`, `product_segment`, `uncategorised_only`,
    `prominence` are applied at the item level; when any is active the result is
    restricted to images that contain at least one matching item.
    Filters `retailer`, `status` are applied at the image level.
    """
    item_conds, product_filter_active = _build_item_filter(
        q, category, subcategory, product_segment, uncategorised_only, prominence, show_all,
    )

    # Stage 1: determine which image ids to return on this page
    img_stmt = select(InStoreCatalogueImage).order_by(desc(InStoreCatalogueImage.created_at))
    count_stmt = select(func.count()).select_from(InStoreCatalogueImage)

    if retailer:
        if retailer == "__none__":
            img_stmt = img_stmt.where(InStoreCatalogueImage.retailer.is_(None))
            count_stmt = count_stmt.where(InStoreCatalogueImage.retailer.is_(None))
        else:
            img_stmt = img_stmt.where(InStoreCatalogueImage.retailer == retailer)
            count_stmt = count_stmt.where(InStoreCatalogueImage.retailer == retailer)

    if status:
        img_stmt = img_stmt.where(InStoreCatalogueImage.status == status)
        count_stmt = count_stmt.where(InStoreCatalogueImage.status == status)

    if product_filter_active:
        sub = select(InStoreCatalogueItem.image_id).where(*item_conds).distinct()
        img_stmt = img_stmt.where(InStoreCatalogueImage.id.in_(sub))
        count_stmt = count_stmt.where(InStoreCatalogueImage.id.in_(sub))

    img_stmt = img_stmt.limit(limit).offset(offset)

    images = (await db.execute(img_stmt)).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    image_ids = [img.id for img in images]

    # Stage 2: fetch items for this page of images
    # - If product filter is active, only fetch matching items (so previews align with the filter)
    # - Otherwise fetch all items (or items that pass the default prominence visibility filter)
    items_by_image: dict[int, list] = {i: [] for i in image_ids}
    total_items_by_image: dict[int, int] = {i: 0 for i in image_ids}
    cats_by_image: dict[int, dict[str, int]] = {i: {} for i in image_ids}

    if image_ids:
        items_stmt = (
            select(InStoreCatalogueItem)
            .where(InStoreCatalogueItem.image_id.in_(image_ids))
            .order_by(InStoreCatalogueItem.id)
        )
        # Apply item-level filters to the preview set (so category/q/prominence narrow the preview)
        if item_conds:
            items_stmt = items_stmt.where(*item_conds)
        rows = (await db.execute(items_stmt)).scalars().all()
        for it in rows:
            items_by_image.setdefault(it.image_id, []).append(it)
            total_items_by_image[it.image_id] = total_items_by_image.get(it.image_id, 0) + 1
            if it.category:
                d = cats_by_image.setdefault(it.image_id, {})
                d[it.category] = d.get(it.category, 0) + 1

    # Stage 3: shape the response
    result = []
    for img in images:
        preview = items_by_image.get(img.id, [])[:SAMPLE_ITEMS_PER_IMAGE]
        result.append({
            "id": img.id,
            "filename": img.filename,
            "file_type": img.file_type,
            "status": img.status,
            "retailer": img.retailer,
            "item_count": total_items_by_image.get(img.id, 0),
            "total_item_count": img.item_count,  # raw column from DB (unfiltered total)
            "by_category": cats_by_image.get(img.id, {}),
            "error_message": img.error_message,
            "created_at": img.created_at,
            "preview": [
                {
                    "id": it.id,
                    "product_name": it.product_name,
                    "category": it.category,
                    "subcategory": it.subcategory,
                    "product_segment": it.product_segment,
                    "prominence": it.prominence,
                }
                for it in preview
            ],
        })

    return {"total": total, "images": result}


@router.get("/images/{image_id}")
async def get_image_detail(image_id: int, db: AsyncSession = Depends(get_db)):
    """Full image detail — all items with every attribute, for the click-through modal."""
    image = await db.get(InStoreCatalogueImage, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Not found")
    items = (await db.execute(
        select(InStoreCatalogueItem)
        .where(InStoreCatalogueItem.image_id == image_id)
        .order_by(InStoreCatalogueItem.id)
    )).scalars().all()
    return {
        "id": image.id,
        "filename": image.filename,
        "file_type": image.file_type,
        "status": image.status,
        "retailer": image.retailer,
        "error_message": image.error_message,
        "created_at": image.created_at,
        "items": [
            {
                "id": it.id,
                "product_name": it.product_name,
                "category": it.category,
                "subcategory": it.subcategory,
                "product_segment": it.product_segment,
                "prominence": it.prominence,
                "has_crop": bool(it.cropped_file_path),
                "colours": it.colours or [],
                "materials": it.materials or [],
                "patterns": it.patterns or [],
                "style_tags": it.style_tags or [],
                "confidence": it.confidence,
            }
            for it in items
        ],
    }


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db)):
    """Top-line counters for the page header + batch progress bar."""
    img_counts = await db.execute(
        select(InStoreCatalogueImage.status, func.count())
        .group_by(InStoreCatalogueImage.status)
    )
    by_status = {s: c for s, c in img_counts.all()}
    total_items = (await db.execute(select(func.count()).select_from(InStoreCatalogueItem))).scalar_one()
    by_cat_rows = await db.execute(
        select(InStoreCatalogueItem.category, func.count())
        .group_by(InStoreCatalogueItem.category)
    )
    by_category = {c: n for c, n in by_cat_rows.all()}
    by_prom_rows = await db.execute(
        select(InStoreCatalogueItem.prominence, func.count())
        .group_by(InStoreCatalogueItem.prominence)
    )
    by_prominence = {(p or "unknown"): n for p, n in by_prom_rows.all()}
    return {
        "images_total": sum(by_status.values()),
        "images_by_status": by_status,
        "items_total": total_items,
        "items_by_category": by_category,
        "items_by_prominence": by_prominence,
    }


# ── Per-image file serving ────────────────────────────────────────────────────

@router.get("/images/{image_id}/file")
async def get_image_file(image_id: int, db: AsyncSession = Depends(get_db)):
    image = await db.get(InStoreCatalogueImage, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Not found")
    path = Path(image.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")

    if image.file_type == "heic":
        import io
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        data = path.read_bytes()
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=88)
        return Response(content=buf.getvalue(), media_type="image/jpeg",
                        headers={"Content-Disposition": "inline",
                                 "Cache-Control": "public, max-age=86400"})

    media_types = {"jpeg": "image/jpeg", "png": "image/png", "pdf": "application/pdf"}
    return FileResponse(
        image.file_path,
        media_type=media_types.get(image.file_type, "application/octet-stream"),
        headers={"Content-Disposition": "inline", "Cache-Control": "public, max-age=86400"},
    )


# ── Inline edit ───────────────────────────────────────────────────────────────

class ItemPatch(BaseModel):
    product_name: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    product_segment: Optional[str] = None


@router.patch("/items/{item_id}")
async def patch_item(item_id: int, body: ItemPatch, db: AsyncSession = Depends(get_db)):
    from scraper import category_catalog as cc
    item = await db.get(InStoreCatalogueItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    if body.product_name is not None:
        name = body.product_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="product_name cannot be empty")
        item.product_name = name[:300]
    # Taxonomy patches are validated against the shared catalog. Each level
    # must be valid under the level above it (which can come from `body` or
    # already be set on `item`).
    if body.category is not None:
        if body.category == "":
            item.category = None
        else:
            label = cc.resolve_shared_label(body.category, kind="category")
            if not label:
                raise HTTPException(status_code=400, detail=f"unknown category '{body.category}'")
            item.category = label
    if body.subcategory is not None:
        if body.subcategory == "":
            item.subcategory = None
        else:
            label = cc.resolve_shared_label(body.subcategory, kind="subcategory")
            if not label or not item.category or not cc.is_valid_shared(item.category, label):
                raise HTTPException(status_code=400,
                                    detail=f"subcategory '{body.subcategory}' not valid under category '{item.category}'")
            item.subcategory = label
    if body.product_segment is not None:
        if body.product_segment == "":
            item.product_segment = None
        else:
            label = cc.resolve_shared_label(body.product_segment, kind="product_segment")
            if (not label or not item.category or not item.subcategory
                    or not cc.is_valid_shared(item.category, item.subcategory, label)):
                raise HTTPException(status_code=400,
                                    detail=f"product_segment '{body.product_segment}' not valid under "
                                           f"'{item.category}' > '{item.subcategory}'")
            item.product_segment = label
    await db.commit()
    return {
        "id": item.id,
        "product_name": item.product_name,
        "category": item.category,
        "subcategory": item.subcategory,
        "product_segment": item.product_segment,
    }


@router.delete("/items/{item_id}")
async def delete_item(item_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(InStoreCatalogueItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    crop_path = item.cropped_file_path
    await db.delete(item)
    await db.commit()
    if crop_path:
        try:
            Path(crop_path).unlink(missing_ok=True)
        except Exception:
            pass
    return {"deleted": True, "id": item_id}


class BulkDeleteBody(BaseModel):
    item_ids: list[int]


@router.post("/items/bulk-delete")
async def bulk_delete_items(body: BulkDeleteBody, db: AsyncSession = Depends(get_db)):
    """Delete multiple items in one round-trip (up to 1000 at a time)."""
    if not body.item_ids:
        return {"deleted": 0}
    ids = body.item_ids[:1000]
    result = await db.execute(
        select(InStoreCatalogueItem).where(InStoreCatalogueItem.id.in_(ids))
    )
    items = result.scalars().all()
    crop_paths = [it.cropped_file_path for it in items if it.cropped_file_path]
    for item in items:
        await db.delete(item)
    await db.commit()
    for p in crop_paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass
    return {"deleted": len(items)}


@router.delete("/everything")
async def delete_everything(
    confirm: str = Query(..., description="Must be literal string 'YES' to proceed"),
    db: AsyncSession = Depends(get_db),
):
    """Wipe the entire In-store Products catalogue: all items, all image rows,
    and their files on disk. Requires ?confirm=YES as a safety gate."""
    if confirm != "YES":
        raise HTTPException(status_code=400, detail="Pass ?confirm=YES to proceed")

    # Collect file paths before DB delete so we can unlink after commit
    images = (await db.execute(select(InStoreCatalogueImage))).scalars().all()
    paths = [Path(img.file_path) for img in images]
    image_count = len(images)

    # Delete items first then images (explicit to avoid ORDER-BY cascade surprises)
    await db.execute(
        InStoreCatalogueItem.__table__.delete()
    )
    await db.execute(
        InStoreCatalogueImage.__table__.delete()
    )
    await db.commit()

    # Best-effort file cleanup
    unlinked = 0
    for p in paths:
        try:
            p.unlink(missing_ok=True)
            unlinked += 1
        except Exception:
            pass
    # Nuke the crops/ subdir too
    crops_dir = Path(settings.instore_catalogue_dir) / "crops"
    if crops_dir.exists():
        for f in crops_dir.iterdir():
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

    log.info("catalogue_everything_deleted", images=image_count, files_unlinked=unlinked)
    return {"deleted_images": image_count, "files_unlinked": unlinked}


# ── Retry / delete images ─────────────────────────────────────────────────────

@router.post("/images/{image_id}/retry")
async def retry_image(image_id: int, db: AsyncSession = Depends(get_db)):
    image = await db.get(InStoreCatalogueImage, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Not found")
    path = Path(image.file_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="File missing on disk — cannot retry")
    image.status = "pending"
    image.error_message = None
    await db.commit()

    from tasks.catalogue_tasks import analyse_catalogue_image
    import base64 as _b64
    raw = path.read_bytes()
    analyse_catalogue_image.apply_async(
        args=[image_id],
        kwargs={"file_b64": _b64.b64encode(raw).decode()},
    )
    return {"queued": True, "image_id": image_id}


@router.post("/retry-all-pending")
async def retry_all_pending(db: AsyncSession = Depends(get_db)):
    """Re-queue images stuck in pending/analysing state. Useful after a
    Redis outage when Celery tasks were lost but the DB rows remain."""
    rows = (await db.execute(
        select(InStoreCatalogueImage).where(
            InStoreCatalogueImage.status.in_(["pending", "analysing"])
        )
    )).scalars().all()
    from tasks.catalogue_tasks import analyse_catalogue_image
    import base64 as _b64
    from datetime import datetime as _dt, timedelta as _td
    queued = 0
    missing = 0
    for i, image in enumerate(rows):
        path = Path(image.file_path)
        if not path.exists():
            missing += 1
            continue
        image.status = "pending"
        image.error_message = None
        eta = _dt.utcnow() + _td(milliseconds=i * 200)
        analyse_catalogue_image.apply_async(
            args=[image.id],
            kwargs={"file_b64": _b64.b64encode(path.read_bytes()).decode()},
            eta=eta,
        )
        queued += 1
    await db.commit()
    return {"queued": queued, "missing_files": missing}


@router.post("/retry-all-failed")
async def retry_all_failed(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(InStoreCatalogueImage).where(InStoreCatalogueImage.status == "failed")
    )).scalars().all()
    from tasks.catalogue_tasks import analyse_catalogue_image
    import base64 as _b64
    from datetime import datetime as _dt, timedelta as _td
    queued = 0
    missing = 0
    for i, image in enumerate(rows):
        path = Path(image.file_path)
        if not path.exists():
            missing += 1
            continue
        image.status = "pending"
        image.error_message = None
        eta = _dt.utcnow() + _td(milliseconds=i * 200)
        analyse_catalogue_image.apply_async(
            args=[image.id],
            kwargs={"file_b64": _b64.b64encode(path.read_bytes()).decode()},
            eta=eta,
        )
        queued += 1
    await db.commit()
    return {"queued": queued, "missing_files": missing}


@router.delete("/images/{image_id}")
async def delete_image(image_id: int, db: AsyncSession = Depends(get_db)):
    image = await db.get(InStoreCatalogueImage, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        Path(image.file_path).unlink(missing_ok=True)
    except Exception:
        pass
    await db.delete(image)  # cascade deletes items
    await db.commit()
    return {"deleted": True, "id": image_id}


class BulkDeleteImagesBody(BaseModel):
    image_ids: list[int]


@router.post("/images/bulk-delete")
async def bulk_delete_images(body: BulkDeleteImagesBody, db: AsyncSession = Depends(get_db)):
    """Bulk-delete images (and cascade their items + unlink files). Capped at 1000/call."""
    if not body.image_ids:
        return {"deleted": 0}
    ids = body.image_ids[:1000]
    rows = (await db.execute(
        select(InStoreCatalogueImage).where(InStoreCatalogueImage.id.in_(ids))
    )).scalars().all()
    paths = [Path(img.file_path) for img in rows]
    for img in rows:
        await db.delete(img)
    await db.commit()
    unlinked = 0
    for p in paths:
        try:
            p.unlink(missing_ok=True)
            unlinked += 1
        except Exception:
            pass
    return {"deleted": len(rows), "files_unlinked": unlinked}
