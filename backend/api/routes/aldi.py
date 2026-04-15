"""Aldi trend document upload, analysis, and product idea generation API."""
import uuid
import pathlib
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from database.db import get_db
from database.models import AldiUpload, AldiProductIdea, AldiUploadStatus
from config import settings

router = APIRouter()

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB

ALLOWED_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
}
ALLOWED_EXTENSIONS = {
    ".pdf": "pdf",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
}


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class AldiIdeaOut(BaseModel):
    id: int
    generation: int = 1
    position: int
    name: str
    description: str
    category: str
    price_point: str
    rationale: str
    inspired_by_products: list = []

    class Config:
        from_attributes = True


class AldiUploadOut(BaseModel):
    id: int
    filename: str
    file_type: str
    status: str
    created_at: datetime
    idea_count: int = 0

    class Config:
        from_attributes = True


class AldiUploadDetailOut(AldiUploadOut):
    themes: list = []
    colour_palette: list = []
    colour_hex: list = []
    key_materials: list = []
    key_prints: list = []
    product_categories: list = []
    season_occasion: Optional[str] = None
    mood_descriptors: list = []
    error_message: Optional[str] = None
    ideas: list[AldiIdeaOut] = []


class AldiUploadSummary(BaseModel):
    id: int
    filename: str
    file_type: str
    status: str
    themes: list = []
    colour_palette: list = []
    colour_hex: list = []
    key_materials: list = []
    key_prints: list = []
    product_categories: list = []
    season_occasion: Optional[str] = None
    mood_descriptors: list = []
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


class AldiSessionOut(BaseModel):
    id: int
    status: str
    created_at: datetime
    upload_count: int = 0
    idea_count: int = 0

    class Config:
        from_attributes = True


class AldiSessionDetailOut(AldiSessionOut):
    themes: list = []
    colour_palette: list = []
    colour_hex: list = []
    key_materials: list = []
    key_prints: list = []
    product_categories: list = []
    season_occasion: Optional[str] = None
    mood_descriptors: list = []
    error_message: Optional[str] = None
    uploads: list[AldiUploadSummary] = []
    ideas: list[AldiIdeaOut] = []
    generation_count: int = 1
    latest_generation: int = 1


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/uploads", response_model=AldiUploadOut)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a trend mood board document (PDF, JPEG, or PNG)."""
    # Determine file type from content-type or extension
    file_ext = ALLOWED_CONTENT_TYPES.get(file.content_type or "")
    if not file_ext:
        suffix = pathlib.Path(file.filename or "").suffix.lower()
        file_ext = ALLOWED_EXTENSIONS.get(suffix)
    if not file_ext:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Please upload a PDF, JPEG, or PNG.",
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 20 MB)")

    # Save to upload directory
    upload_dir = pathlib.Path(settings.aldi_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    save_name = f"{uuid.uuid4()}.{file_ext}"
    file_path = upload_dir / save_name
    file_path.write_bytes(contents)

    # Persist DB record
    upload = AldiUpload(
        filename=file.filename or save_name,
        file_path=str(file_path),
        file_type=file_ext,
        status=AldiUploadStatus.PENDING,
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)

    # Dispatch Celery chain: analyse → generate
    from tasks.aldi_tasks import analyse_aldi_upload, generate_aldi_ideas
    from celery import chain as celery_chain
    celery_chain(
        analyse_aldi_upload.s(upload.id),
        generate_aldi_ideas.s(),
    ).delay()

    return AldiUploadOut(
        id=upload.id,
        filename=upload.filename,
        file_type=upload.file_type,
        status=upload.status.value,
        created_at=upload.created_at,
        idea_count=0,
    )


@router.get("/uploads", response_model=list[AldiUploadOut])
async def list_uploads(db: AsyncSession = Depends(get_db)):
    """List all uploaded documents newest-first."""
    result = await db.execute(
        select(AldiUpload).order_by(desc(AldiUpload.created_at))
    )
    uploads = result.scalars().all()
    return [
        AldiUploadOut(
            id=u.id,
            filename=u.filename,
            file_type=u.file_type,
            status=u.status.value,
            created_at=u.created_at,
            idea_count=len(u.ideas),
        )
        for u in uploads
    ]


@router.get("/uploads/{upload_id}", response_model=AldiUploadDetailOut)
async def get_upload(upload_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single upload with full extracted analysis and generated ideas."""
    upload = await db.get(AldiUpload, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    ideas_sorted = sorted(upload.ideas, key=lambda x: x.position)
    return AldiUploadDetailOut(
        id=upload.id,
        filename=upload.filename,
        file_type=upload.file_type,
        status=upload.status.value,
        created_at=upload.created_at,
        idea_count=len(upload.ideas),
        themes=upload.themes or [],
        colour_palette=upload.colour_palette or [],
        colour_hex=upload.colour_hex or [],
        key_materials=upload.key_materials or [],
        key_prints=upload.key_prints or [],
        product_categories=upload.product_categories or [],
        season_occasion=upload.season_occasion,
        mood_descriptors=upload.mood_descriptors or [],
        error_message=upload.error_message,
        ideas=[
            AldiIdeaOut(
                id=i.id,
                position=i.position,
                name=i.name,
                description=i.description,
                category=i.category,
                price_point=i.price_point,
                rationale=i.rationale,
                inspired_by_products=i.inspired_by_products or [],
            )
            for i in ideas_sorted
        ],
    )


@router.get("/uploads/{upload_id}/file")
async def serve_file(upload_id: int, db: AsyncSession = Depends(get_db)):
    """Serve the uploaded document for in-browser preview."""
    upload = await db.get(AldiUpload, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if not pathlib.Path(upload.file_path).exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    media_types = {"pdf": "application/pdf", "jpeg": "image/jpeg", "png": "image/png"}
    return FileResponse(
        path=upload.file_path,
        media_type=media_types.get(upload.file_type, "application/octet-stream"),
        headers={"Content-Disposition": "inline"},
    )


@router.delete("/uploads/{upload_id}")
async def delete_upload(upload_id: int, db: AsyncSession = Depends(get_db)):
    """Delete an upload and all its generated ideas."""
    upload = await db.get(AldiUpload, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    try:
        pathlib.Path(upload.file_path).unlink(missing_ok=True)
    except Exception:
        pass
    await db.delete(upload)
    await db.commit()
    return {"deleted": True, "id": upload_id}


# ── Session routes ─────────────────────────────────────────────────────────────

@router.post("/sessions", response_model=AldiSessionOut)
async def create_session(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload multiple trend documents as a single analysis session."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 files per session")

    # Create session record
    from database.models import AldiSession
    sess_obj = AldiSession(status=AldiUploadStatus.PENDING)
    db.add(sess_obj)
    await db.flush()  # Get session ID

    upload_dir = pathlib.Path(settings.aldi_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    upload_ids = []
    for file in files:
        # Validate type
        file_ext = ALLOWED_CONTENT_TYPES.get(file.content_type or "")
        if not file_ext:
            suffix = pathlib.Path(file.filename or "").suffix.lower()
            file_ext = ALLOWED_EXTENSIONS.get(suffix)
        if not file_ext:
            continue  # Skip unsupported files silently

        contents = await file.read()
        if len(contents) > MAX_FILE_BYTES:
            continue  # Skip oversized files

        save_name = f"{uuid.uuid4()}.{file_ext}"
        file_path = upload_dir / save_name
        file_path.write_bytes(contents)

        upload = AldiUpload(
            session_id=sess_obj.id,
            filename=file.filename or save_name,
            file_path=str(file_path),
            file_type=file_ext,
            status=AldiUploadStatus.PENDING,
        )
        db.add(upload)
        await db.flush()
        upload_ids.append(upload.id)

    if not upload_ids:
        raise HTTPException(status_code=400, detail="No valid files could be processed")

    await db.commit()
    await db.refresh(sess_obj)

    # Dispatch analysis task for each upload
    from tasks.aldi_tasks import analyse_aldi_upload
    for uid in upload_ids:
        analyse_aldi_upload.delay(uid)

    return AldiSessionOut(
        id=sess_obj.id,
        status=sess_obj.status.value,
        created_at=sess_obj.created_at,
        upload_count=len(upload_ids),
        idea_count=0,
    )


@router.get("/sessions", response_model=list[AldiSessionOut])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """List all sessions newest-first."""
    from database.models import AldiSession
    result = await db.execute(
        select(AldiSession).order_by(desc(AldiSession.created_at))
    )
    sessions = result.scalars().all()
    return [
        AldiSessionOut(
            id=s.id,
            status=s.status.value,
            created_at=s.created_at,
            upload_count=len(s.uploads),
            idea_count=len(s.ideas),
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}", response_model=AldiSessionDetailOut)
async def get_session(session_id: int, db: AsyncSession = Depends(get_db)):
    """Get a session with all uploads and generated ideas."""
    from database.models import AldiSession
    sess_obj = await db.get(AldiSession, session_id)
    if not sess_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    uploads_sorted = sorted(sess_obj.uploads, key=lambda x: x.id)
    ideas_sorted = sorted(sess_obj.ideas, key=lambda x: (x.generation, x.position))

    generations = [i.generation for i in sess_obj.ideas] if sess_obj.ideas else [1]
    generation_count = max(generations) if generations else 1
    latest_generation = max(generations) if generations else 1

    return AldiSessionDetailOut(
        id=sess_obj.id,
        status=sess_obj.status.value,
        created_at=sess_obj.created_at,
        upload_count=len(sess_obj.uploads),
        idea_count=len(sess_obj.ideas),
        themes=sess_obj.themes or [],
        colour_palette=sess_obj.colour_palette or [],
        colour_hex=sess_obj.colour_hex or [],
        key_materials=sess_obj.key_materials or [],
        key_prints=sess_obj.key_prints or [],
        product_categories=sess_obj.product_categories or [],
        season_occasion=sess_obj.season_occasion,
        mood_descriptors=sess_obj.mood_descriptors or [],
        error_message=sess_obj.error_message,
        uploads=[
            AldiUploadSummary(
                id=u.id,
                filename=u.filename,
                file_type=u.file_type,
                status=u.status.value,
                themes=u.themes or [],
                colour_palette=u.colour_palette or [],
                colour_hex=u.colour_hex or [],
                key_materials=u.key_materials or [],
                key_prints=u.key_prints or [],
                product_categories=u.product_categories or [],
                season_occasion=u.season_occasion,
                mood_descriptors=u.mood_descriptors or [],
                error_message=u.error_message,
            )
            for u in uploads_sorted
        ],
        ideas=[
            AldiIdeaOut(
                id=i.id,
                generation=i.generation,
                position=i.position,
                name=i.name,
                description=i.description,
                category=i.category,
                price_point=i.price_point,
                rationale=i.rationale,
                inspired_by_products=i.inspired_by_products or [],
            )
            for i in ideas_sorted
        ],
        generation_count=generation_count,
        latest_generation=latest_generation,
    )


@router.post("/sessions/{session_id}/regenerate")
async def regenerate_session_ideas(session_id: int, db: AsyncSession = Depends(get_db)):
    """Trigger a new round of idea generation for an existing session (Try Again)."""
    from database.models import AldiSession
    sess_obj = await db.get(AldiSession, session_id)
    if not sess_obj:
        raise HTTPException(status_code=404, detail="Session not found")
    if sess_obj.status not in (AldiUploadStatus.DONE, AldiUploadStatus.FAILED):
        raise HTTPException(status_code=409, detail="Session is still processing")

    # Mark session as generating again
    sess_obj.status = AldiUploadStatus.GENERATING
    await db.commit()

    from tasks.aldi_tasks import regenerate_aldi_session_ideas
    task = regenerate_aldi_session_ideas.delay(session_id)
    return {"task_id": task.id, "status": "queued", "session_id": session_id}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a session and all its uploads and ideas."""
    from database.models import AldiSession
    sess_obj = await db.get(AldiSession, session_id)
    if not sess_obj:
        raise HTTPException(status_code=404, detail="Session not found")
    # Delete files on disk
    for upload in sess_obj.uploads:
        try:
            pathlib.Path(upload.file_path).unlink(missing_ok=True)
        except Exception:
            pass
    await db.delete(sess_obj)
    await db.commit()
    return {"deleted": True, "id": session_id}
