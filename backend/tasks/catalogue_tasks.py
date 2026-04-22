"""Celery tasks for the In-store Products catalogue (standalone feature)."""
import asyncio
import base64 as _b64
import io
import uuid
from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from tasks.celery_app import app
from config import settings
from database.models import InStoreCatalogueImage, InStoreCatalogueItem
import structlog

log = structlog.get_logger()
engine = create_engine(settings.database_url_sync)
Session = sessionmaker(bind=engine)

# Crop padding (fraction of the bbox size added on each side before clamping)
CROP_PADDING = 0.08
# Where cropped JPEGs live on the volume
CROP_SUBDIR = "crops"


def _open_source_image(raw_bytes: bytes, file_type: str):
    """Open uploaded bytes as a PIL image regardless of format. Returns an RGB PIL Image or None."""
    try:
        from PIL import Image
        ft = (file_type or "").lower().lstrip(".")
        if ft in ("heic", "heif"):
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except ImportError:
                pass
        if ft == "pdf":
            # Rasterise first page of the PDF for cropping
            try:
                from pdf2image import convert_from_bytes
                pages = convert_from_bytes(raw_bytes, first_page=1, last_page=1, dpi=180)
                if pages:
                    return pages[0].convert("RGB")
            except Exception as exc:
                log.warning("crop_pdf_rasterise_failed", error=str(exc))
                return None
            return None
        img = Image.open(io.BytesIO(raw_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception as exc:
        log.warning("crop_open_failed", file_type=file_type, error=str(exc))
        return None


def _crop_item(source_img, bbox_norm: list[float], crops_dir: Path) -> str | None:
    """Crop a region from source_img using normalised [x,y,w,h] with 8% padding.
    Saves JPEG to crops_dir and returns the absolute file path, or None on failure."""
    try:
        W, H = source_img.size
        x, y, w, h = bbox_norm
        # Apply padding
        px = w * CROP_PADDING
        py = h * CROP_PADDING
        x -= px
        y -= py
        w += 2 * px
        h += 2 * py
        # Clamp to image bounds
        x = max(0.0, x)
        y = max(0.0, y)
        w = min(1.0 - x, w)
        h = min(1.0 - y, h)
        if w <= 0 or h <= 0:
            return None
        left = int(round(x * W))
        top = int(round(y * H))
        right = int(round((x + w) * W))
        bottom = int(round((y + h) * H))
        if right - left < 24 or bottom - top < 24:
            return None  # crop too small to be useful
        cropped = source_img.crop((left, top, right, bottom))
        fname = f"{uuid.uuid4()}.jpg"
        fpath = crops_dir / fname
        cropped.save(fpath, format="JPEG", quality=88, optimize=True)
        return str(fpath)
    except Exception as exc:
        log.warning("crop_save_failed", error=str(exc))
        return None


@app.task(bind=True, max_retries=2, queue="aldi")
def analyse_catalogue_image(self, image_id: int, file_b64: str | None = None):
    """Run Claude Vision on one catalogue image and write InStoreCatalogueItem rows
    for every detected product."""
    db = Session()
    try:
        image = db.get(InStoreCatalogueImage, image_id)
        if not image:
            return {"error": "not found"}

        image.status = "analysing"
        image.updated_at = datetime.utcnow()
        db.commit()

        from analysis.catalogue_vision import CatalogueVision
        analyser = CatalogueVision()

        if file_b64:
            raw_bytes = _b64.b64decode(file_b64)
        else:
            p = Path(image.file_path)
            if not p.exists():
                image.status = "failed"
                image.error_message = "File missing on disk"
                db.commit()
                return {"error": "file missing"}
            raw_bytes = p.read_bytes()

        detected = asyncio.run(analyser.analyse_image_bytes(raw_bytes, image.file_type))

        if detected is None:
            image.status = "failed"
            image.error_message = "Vision analysis returned no result"
            db.commit()
            return {"status": "failed", "image_id": image_id}

        # Wipe any prior items and their cropped files (supports retry)
        prior = db.query(InStoreCatalogueItem).filter(
            InStoreCatalogueItem.image_id == image_id
        ).all()
        for p in prior:
            if p.cropped_file_path:
                try:
                    Path(p.cropped_file_path).unlink(missing_ok=True)
                except Exception:
                    pass
        db.query(InStoreCatalogueItem).filter(InStoreCatalogueItem.image_id == image_id).delete()

        # Prepare crop directory + source image (lazy: only open if a hero/main bbox exists)
        crops_dir = Path(settings.instore_catalogue_dir) / CROP_SUBDIR
        crops_dir.mkdir(parents=True, exist_ok=True)
        source_img = None
        wants_crops = any(
            (e.get("prominence") in ("hero", "main")) and e.get("bbox")
            for e in detected
        )
        if wants_crops:
            source_img = _open_source_image(raw_bytes, image.file_type)

        crop_count = 0
        for entry in detected:
            cropped_path = None
            bbox = entry.get("bbox")
            prominence = entry.get("prominence")
            if source_img and bbox and prominence in ("hero", "main"):
                cropped_path = _crop_item(source_img, bbox, crops_dir)
                if cropped_path:
                    crop_count += 1

            item = InStoreCatalogueItem(
                image_id=image_id,
                product_name=entry["product_name"],
                category=entry["category"],
                prominence=prominence,
                bbox=bbox,
                cropped_file_path=cropped_path,
                colours=entry.get("colours") or [],
                materials=entry.get("materials") or [],
                patterns=entry.get("patterns") or [],
                style_tags=entry.get("style_tags") or [],
                confidence=entry.get("confidence"),
            )
            db.add(item)

        image.item_count = len(detected)
        image.raw_analysis = detected
        image.status = "done"
        image.error_message = None
        db.commit()
        log.info(
            "catalogue_image_analysed",
            image_id=image_id, items=len(detected), crops=crop_count,
        )
        return {"status": "done", "image_id": image_id, "items": len(detected), "crops": crop_count}

    except Exception as exc:
        db.rollback()
        log.error("analyse_catalogue_image_failed", image_id=image_id, error=str(exc))
        try:
            image = db.get(InStoreCatalogueImage, image_id)
            if image:
                image.status = "failed"
                image.error_message = str(exc)[:1000]
                db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
