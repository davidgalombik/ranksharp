"""Celery tasks for the In-store Products catalogue (standalone feature)."""
import asyncio
import base64 as _b64
from datetime import datetime
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from tasks.celery_app import app
from config import settings
from database.models import InStoreCatalogueImage, InStoreCatalogueItem
import structlog

log = structlog.get_logger()
engine = create_engine(settings.database_url_sync)
Session = sessionmaker(bind=engine)


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
            from pathlib import Path
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

        # Wipe any prior items (supports retry)
        db.query(InStoreCatalogueItem).filter(InStoreCatalogueItem.image_id == image_id).delete()

        for entry in detected:
            item = InStoreCatalogueItem(
                image_id=image_id,
                product_name=entry["product_name"],
                category=entry["category"],
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
        log.info("catalogue_image_analysed", image_id=image_id, items=len(detected))
        return {"status": "done", "image_id": image_id, "items": len(detected)}

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
