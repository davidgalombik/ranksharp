"""Aldi trend document upload, analysis, and product idea generation API."""
import uuid
import pathlib
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
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

    # Dispatch Celery chain: analyse → generate. Pass bytes inline so the
    # worker doesn't need a shared filesystem with the API container.
    import base64 as _b64
    from tasks.aldi_tasks import analyse_aldi_upload, generate_aldi_ideas
    from celery import chain as celery_chain
    celery_chain(
        analyse_aldi_upload.s(upload.id, file_b64=_b64.b64encode(contents).decode()),
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
    """List all uploaded documents newest-first.

    Uses selectinload so the `ideas` relationship is eagerly loaded —
    lazy-loading a relationship on an async session would raise
    MissingGreenlet at runtime.
    """
    result = await db.execute(
        select(AldiUpload)
        .options(selectinload(AldiUpload.ideas))
        .order_by(desc(AldiUpload.created_at))
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
    result = await db.execute(
        select(AldiUpload)
        .options(selectinload(AldiUpload.ideas))
        .where(AldiUpload.id == upload_id)
    )
    upload = result.scalar_one_or_none()
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

MAX_FILES_PER_SESSION = 2000
MAX_FILES_PER_BATCH = 50  # Aldi PDFs are bigger on average


async def _aldi_process_files(
    sess_obj,
    files: list[UploadFile],
    db: AsyncSession,
) -> tuple[list[int], list[bytes], int]:
    """Validate, write to disk, create AldiUpload rows. Does NOT commit.
    Returns (upload_ids, upload_bytes, skipped_count).
    """
    upload_dir = pathlib.Path(settings.aldi_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    upload_ids: list[int] = []
    upload_bytes: list[bytes] = []
    skipped = 0

    for file in files:
        file_ext = ALLOWED_CONTENT_TYPES.get(file.content_type or "")
        if not file_ext:
            suffix = pathlib.Path(file.filename or "").suffix.lower()
            file_ext = ALLOWED_EXTENSIONS.get(suffix)
        if not file_ext:
            skipped += 1
            continue

        contents = await file.read()
        if len(contents) > MAX_FILE_BYTES:
            skipped += 1
            continue

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
        upload_bytes.append(contents)

    return upload_ids, upload_bytes, skipped


def _aldi_dispatch_analysis(upload_ids: list[int], upload_bytes: list[bytes]):
    import base64 as _b64
    from datetime import datetime as _dt, timedelta as _td
    from tasks.aldi_tasks import analyse_aldi_upload
    for i, (uid, raw) in enumerate(zip(upload_ids, upload_bytes)):
        eta = _dt.utcnow() + _td(milliseconds=i * 200)
        analyse_aldi_upload.apply_async(
            args=[uid],
            kwargs={"file_b64": _b64.b64encode(raw).decode()},
            eta=eta,
        )


@router.post("/sessions", response_model=AldiSessionOut)
async def create_session(
    files: list[UploadFile] = File(...),
    finalise: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Create a session. finalise=False → session stays UPLOADING for batching."""
    from database.models import AldiSession
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_FILES_PER_BATCH} files per batch",
        )

    initial_status = AldiUploadStatus.PENDING if finalise else AldiUploadStatus.UPLOADING
    sess_obj = AldiSession(status=initial_status)
    db.add(sess_obj)
    await db.flush()

    upload_ids, upload_bytes, skipped = await _aldi_process_files(sess_obj, files, db)
    if not upload_ids:
        raise HTTPException(status_code=400, detail="No valid files could be processed")

    await db.commit()
    await db.refresh(sess_obj)

    _aldi_dispatch_analysis(upload_ids, upload_bytes)

    return AldiSessionOut(
        id=sess_obj.id,
        status=sess_obj.status.value,
        created_at=sess_obj.created_at,
        upload_count=len(upload_ids),
        idea_count=0,
    )


@router.post("/sessions/{session_id}/uploads")
async def add_aldi_uploads(
    session_id: int,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Add more files to an UPLOADING Aldi session."""
    from database.models import AldiSession
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_FILES_PER_BATCH} files per batch",
        )

    sess_obj = await db.get(AldiSession, session_id)
    if not sess_obj:
        raise HTTPException(status_code=404, detail="Session not found")
    if sess_obj.status != AldiUploadStatus.UPLOADING:
        raise HTTPException(
            status_code=409,
            detail=f"Session is {sess_obj.status}; cannot add files.",
        )

    count_result = await db.execute(
        select(AldiUpload).where(AldiUpload.session_id == session_id)
    )
    current_count = len(count_result.scalars().all())
    if current_count + len(files) > MAX_FILES_PER_SESSION:
        raise HTTPException(
            status_code=400,
            detail=f"Session would exceed {MAX_FILES_PER_SESSION} uploads",
        )

    upload_ids, upload_bytes, skipped = await _aldi_process_files(sess_obj, files, db)
    if not upload_ids:
        raise HTTPException(status_code=400, detail="No valid files in batch")

    await db.commit()

    _aldi_dispatch_analysis(upload_ids, upload_bytes)

    return {
        "session_id": session_id,
        "added": len(upload_ids),
        "skipped": skipped,
        "total": current_count + len(upload_ids),
        "status": sess_obj.status.value,
    }


@router.post("/sessions/{session_id}/finalise")
async def finalise_aldi_session(session_id: int, db: AsyncSession = Depends(get_db)):
    """Flip session from UPLOADING → ANALYSING. Worker will trigger idea
    generation once pending analyses complete (or immediately if all done)."""
    from database.models import AldiSession
    sess_obj = await db.get(AldiSession, session_id)
    if not sess_obj:
        raise HTTPException(status_code=404, detail="Session not found")
    if sess_obj.status != AldiUploadStatus.UPLOADING:
        raise HTTPException(status_code=409, detail=f"Session is {sess_obj.status}")

    uploads_result = await db.execute(
        select(AldiUpload).where(AldiUpload.session_id == session_id)
    )
    uploads = uploads_result.scalars().all()
    if not uploads:
        raise HTTPException(status_code=400, detail="Session has no uploads")

    sess_obj.status = AldiUploadStatus.ANALYSING
    await db.commit()

    all_done = all(
        u.status in (AldiUploadStatus.DONE, AldiUploadStatus.FAILED)
        for u in uploads
    )
    if all_done:
        sess_obj.status = AldiUploadStatus.GENERATING
        await db.commit()
        from tasks.aldi_tasks import generate_aldi_session_ideas
        generate_aldi_session_ideas.delay(session_id)

    pending = sum(
        1 for u in uploads
        if u.status in (AldiUploadStatus.PENDING, AldiUploadStatus.ANALYSING)
    )
    return {
        "session_id": session_id,
        "status": sess_obj.status.value,
        "pending_count": pending,
        "ideas_dispatched": all_done,
    }


@router.get("/sessions", response_model=list[AldiSessionOut])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """List all sessions newest-first."""
    from database.models import AldiSession
    result = await db.execute(
        select(AldiSession)
        .options(
            selectinload(AldiSession.uploads),
            selectinload(AldiSession.ideas),
        )
        .order_by(desc(AldiSession.created_at))
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
    result = await db.execute(
        select(AldiSession)
        .options(
            selectinload(AldiSession.uploads),
            selectinload(AldiSession.ideas),
        )
        .where(AldiSession.id == session_id)
    )
    sess_obj = result.scalar_one_or_none()
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


@router.post("/sessions/{session_id}/kick")
async def kick_session(session_id: int, db: AsyncSession = Depends(get_db)):
    """Recovery hatch: if all uploads finished analysing but the session got
    stuck at ANALYSING (the trigger helper race-condition bug), manually
    dispatch the session idea-generation task.
    """
    from database.models import AldiSession
    sess_obj = await db.get(AldiSession, session_id)
    if not sess_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    # Count uploads and check they're all in a terminal state
    result = await db.execute(
        select(AldiUpload).where(AldiUpload.session_id == session_id)
    )
    uploads = result.scalars().all()
    if not uploads:
        raise HTTPException(status_code=400, detail="Session has no uploads")

    terminal = {AldiUploadStatus.DONE, AldiUploadStatus.FAILED}
    not_ready = [u.id for u in uploads if u.status not in terminal]
    if not_ready:
        raise HTTPException(
            status_code=409,
            detail=f"Uploads still analysing: {not_ready}",
        )

    sess_obj.status = AldiUploadStatus.GENERATING
    await db.commit()

    from tasks.aldi_tasks import generate_aldi_session_ideas
    task = generate_aldi_session_ideas.delay(session_id)
    return {"task_id": task.id, "status": "queued", "session_id": session_id}


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
    result = await db.execute(
        select(AldiSession)
        .options(selectinload(AldiSession.uploads))
        .where(AldiSession.id == session_id)
    )
    sess_obj = result.scalar_one_or_none()
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
