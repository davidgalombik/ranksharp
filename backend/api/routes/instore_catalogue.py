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
CATEGORIES = {"Kitchen & Dining", "Home & Decor", "Candles", "Other"}


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_batch(
    files: list[UploadFile] = File(...),
    hashes: list[str] = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a batch of images. `hashes` must be a parallel list of SHA-256 hashes
    (hex) computed client-side. Duplicates (matching hash already in DB) are skipped."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) != len(hashes):
        raise HTTPException(status_code=400, detail="files and hashes length mismatch")
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(status_code=400, detail=f"Max {MAX_FILES_PER_BATCH} files per batch")

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
    if category and category in CATEGORIES:
        stmt = stmt.where(InStoreCatalogueItem.category == category)
        count_stmt = count_stmt.where(InStoreCatalogueItem.category == category)

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
            "prominence": item.prominence,
            "colours": item.colours or [],
            "materials": item.materials or [],
            "patterns": item.patterns or [],
            "style_tags": item.style_tags or [],
            "confidence": item.confidence,
            "source_filename": image.filename,
            "created_at": item.created_at,
        }
        for item, image in rows
    ]
    return {"total": total, "items": items}


# ── Images endpoint — separate list for the image-centric view (failed retry etc) ──

@router.get("/images")
async def list_images(
    status: Optional[str] = None,
    limit: int = Query(default=60, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(InStoreCatalogueImage).order_by(desc(InStoreCatalogueImage.created_at))
    count_stmt = select(func.count()).select_from(InStoreCatalogueImage)
    if status:
        stmt = stmt.where(InStoreCatalogueImage.status == status)
        count_stmt = count_stmt.where(InStoreCatalogueImage.status == status)
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    return {
        "total": total,
        "images": [
            {
                "id": i.id,
                "filename": i.filename,
                "file_type": i.file_type,
                "status": i.status,
                "item_count": i.item_count,
                "error_message": i.error_message,
                "created_at": i.created_at,
            }
            for i in rows
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


@router.patch("/items/{item_id}")
async def patch_item(item_id: int, body: ItemPatch, db: AsyncSession = Depends(get_db)):
    item = await db.get(InStoreCatalogueItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    if body.product_name is not None:
        name = body.product_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="product_name cannot be empty")
        item.product_name = name[:300]
    if body.category is not None:
        if body.category not in CATEGORIES:
            raise HTTPException(status_code=400, detail=f"category must be one of {sorted(CATEGORIES)}")
        item.category = body.category
    await db.commit()
    return {"id": item.id, "product_name": item.product_name, "category": item.category}


@router.delete("/items/{item_id}")
async def delete_item(item_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(InStoreCatalogueItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(item)
    await db.commit()
    return {"deleted": True, "id": item_id}


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
