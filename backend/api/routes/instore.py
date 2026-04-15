"""API routes for In-store Products feature."""
import os
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from datetime import datetime
from database.db import AsyncSessionLocal
from database.models import InStoreSession, InStoreProduct, InStoreStatus
from tasks.instore_tasks import analyse_instore_product
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
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/sessions")
async def create_session(
    files: list[UploadFile] = File(...),
    name: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Create a new session and upload product photos."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")


    upload_dir = Path(settings.instore_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    session = InStoreSession(name=name, status=InStoreStatus.PENDING)
    db.add(session)
    await db.flush()

    products = []
    for file in files:
        content_type = file.content_type or ""
        file_type = ALLOWED_CONTENT_TYPES.get(content_type)
        if not file_type:
            ext = (file.filename or "").rsplit(".", 1)[-1].lower()
            file_type = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "pdf": "pdf", "heic": "heic", "heif": "heic"}.get(ext)
        if not file_type:
            continue

        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE:
            continue

        fname = f"{uuid.uuid4()}.{file_type}"
        fpath = upload_dir / fname
        fpath.write_bytes(contents)

        product = InStoreProduct(
            session_id=session.id,
            filename=file.filename or fname,
            file_path=str(fpath),
            file_type=file_type,
            status=InStoreStatus.PENDING,
        )
        db.add(product)
        products.append(product)

    if not products:
        await db.rollback()
        raise HTTPException(status_code=400, detail="No valid files were uploaded")

    await db.commit()

    # Refresh to get IDs
    for p in products:
        await db.refresh(p)

    # Dispatch analysis tasks
    for p in products:
        analyse_instore_product.delay(p.id)

    return {"id": session.id, "product_count": len(products), "status": "pending"}


@router.get("/sessions")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(InStoreSession).order_by(desc(InStoreSession.created_at)).limit(50)
    )
    sessions = result.scalars().all()
    out = []
    for s in sessions:
        prods = await db.execute(
            select(InStoreProduct).where(InStoreProduct.session_id == s.id)
        )
        products = prods.scalars().all()
        done = sum(1 for p in products if p.status == InStoreStatus.DONE)
        out.append({
            "id": s.id,
            "name": s.name,
            "status": s.status,
            "product_count": len(products),
            "done_count": done,
            "has_trend_report": bool(s.trend_report),
            "error_message": s.error_message,
            "created_at": s.created_at,
        })
    return out


@router.get("/sessions/{session_id}")
async def get_session(session_id: int, db: AsyncSession = Depends(get_db)):
    session = await db.get(InStoreSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = await db.execute(
        select(InStoreProduct).where(InStoreProduct.session_id == session_id)
    )
    products = result.scalars().all()
    done = sum(1 for p in products if p.status == InStoreStatus.DONE)

    return {
        "id": session.id,
        "name": session.name,
        "status": session.status,
        "product_count": len(products),
        "done_count": done,
        "trend_report": session.trend_report,
        "generation_count": session.generation_count or 1,
        "trend_report_all": session.trend_report_all or [],
        "error_message": session.error_message,
        "created_at": session.created_at,
        "products": [
            {
                "id": p.id,
                "filename": p.filename,
                "file_type": p.file_type,
                "status": p.status,
                "product_name": p.product_name,
                "category": p.category,
                "price": p.price,
                "colours": p.colours,
                "materials": p.materials,
                "style_tags": p.style_tags,
                "patterns": p.patterns,
                "mood": p.mood,
                "error_message": p.error_message,
                "created_at": p.created_at,
            }
            for p in products
        ],
    }


@router.get("/sessions/{session_id}/products/{product_id}/image")
async def get_product_image(session_id: int, product_id: int, db: AsyncSession = Depends(get_db)):
    """Serve the uploaded product photo (converts HEIC → JPEG for browser display)."""
    from fastapi.responses import Response
    product = await db.get(InStoreProduct, product_id)
    if not product or product.session_id != session_id:
        raise HTTPException(status_code=404, detail="Not found")
    if not Path(product.file_path).exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    if product.file_type == "heic":
        # Browsers can't display HEIC — convert to JPEG on the fly
        import io
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        data = Path(product.file_path).read_bytes()
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=90)
        return Response(content=buf.getvalue(), media_type="image/jpeg",
                        headers={"Content-Disposition": "inline"})

    media_types = {"jpeg": "image/jpeg", "png": "image/png", "pdf": "application/pdf"}
    return FileResponse(
        product.file_path,
        media_type=media_types.get(product.file_type, "application/octet-stream"),
        headers={"Content-Disposition": "inline"},
    )


@router.post("/sessions/{session_id}/regenerate")
async def regenerate_session(session_id: int, db: AsyncSession = Depends(get_db)):
    """Trigger a fresh trend report for an existing session (Try Again)."""
    session = await db.get(InStoreSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status not in (InStoreStatus.DONE, InStoreStatus.FAILED):
        raise HTTPException(status_code=409, detail="Session is still processing")

    session.status = InStoreStatus.GENERATING
    await db.commit()

    from tasks.instore_tasks import regenerate_instore_trend_report
    task = regenerate_instore_trend_report.delay(session_id)
    return {"task_id": task.id, "status": "queued", "session_id": session_id}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, db: AsyncSession = Depends(get_db)):
    session = await db.get(InStoreSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    result = await db.execute(
        select(InStoreProduct).where(InStoreProduct.session_id == session_id)
    )
    for p in result.scalars().all():
        try:
            os.remove(p.file_path)
        except Exception:
            pass
    await db.delete(session)
    await db.commit()
    return {"status": "deleted"}
