"""Celery tasks for Aldi trend document analysis and product idea generation."""
import asyncio
import random
from datetime import datetime
from sqlalchemy import create_engine, text
from tasks.celery_app import app
from config import settings
from database.models import AldiUpload, AldiProductIdea, AldiUploadStatus, AldiSession
import structlog

log = structlog.get_logger()
engine = create_engine(settings.database_url_sync)


def _get_session():
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=engine)()


# ── Task 1: Vision analysis ───────────────────────────────────────────────────

@app.task(bind=True, max_retries=2)
def analyse_aldi_upload(self, upload_id: int, file_b64: str | None = None) -> int:
    """Analyse an uploaded mood-board document with Claude Vision.

    If ``file_b64`` is supplied, the image/PDF bytes are decoded and analysed
    directly. This avoids requiring a shared filesystem between the API
    container (where the file was originally written) and the worker
    container. Falls back to reading from disk if no bytes were passed.
    """
    session = _get_session()
    try:
        upload = session.get(AldiUpload, upload_id)
        if not upload:
            log.warning("aldi_upload_not_found", upload_id=upload_id)
            return upload_id

        # Update session status to analysing if needed
        if upload.session_id:
            sess_obj = session.get(AldiSession, upload.session_id)
            if sess_obj and sess_obj.status == AldiUploadStatus.PENDING:
                sess_obj.status = AldiUploadStatus.ANALYSING
                sess_obj.updated_at = datetime.utcnow()

        upload.status = AldiUploadStatus.ANALYSING
        upload.updated_at = datetime.utcnow()
        session.commit()

        try:
            if file_b64:
                import base64 as _b64
                raw_bytes = _b64.b64decode(file_b64)
                result = asyncio.run(_vision_analyse_bytes(raw_bytes, upload.file_type))
            else:
                result = asyncio.run(_vision_analyse(upload.file_path, upload.file_type))

            if result:
                upload.themes = result.get("themes", [])
                upload.colour_palette = result.get("colour_palette", [])
                upload.colour_hex = result.get("colour_hex", [])
                upload.key_materials = result.get("key_materials", [])
                upload.key_prints = result.get("key_prints", [])
                upload.product_categories = result.get("product_categories", [])
                upload.season_occasion = result.get("season_occasion")
                upload.mood_descriptors = result.get("mood_descriptors", [])
                upload.raw_analysis = result

                if upload.session_id:
                    # Session flow: mark upload DONE, check if all siblings done
                    upload.status = AldiUploadStatus.DONE
                else:
                    # Legacy single-upload flow: go to GENERATING (chain handles ideas)
                    upload.status = AldiUploadStatus.GENERATING
                log.info("aldi_vision_done", upload_id=upload_id, themes=upload.themes)
            else:
                upload.status = AldiUploadStatus.FAILED
                upload.error_message = "Vision analysis returned no data"

        except Exception as exc:
            log.error("aldi_vision_failed", upload_id=upload_id, error=str(exc))
            upload.status = AldiUploadStatus.FAILED
            upload.error_message = str(exc)
            upload.updated_at = datetime.utcnow()
            session.commit()
            raise self.retry(exc=exc, countdown=30)

        upload.updated_at = datetime.utcnow()
        session.commit()

        # If part of a session, check if all siblings are done/failed
        if upload.session_id:
            _maybe_trigger_session_ideas(session, upload.session_id)

        return upload_id

    finally:
        session.close()


# ── Task 2: Idea generation ───────────────────────────────────────────────────

@app.task(bind=True, max_retries=2)
def generate_aldi_ideas(self, upload_id: int) -> dict:
    """Generate Aldi product ideas from trend analysis + similar DB products."""
    session = _get_session()
    try:
        upload = session.get(AldiUpload, upload_id)
        if not upload:
            return {"status": "not_found", "upload_id": upload_id}
        if upload.status != AldiUploadStatus.GENERATING:
            return {"status": "skipped", "upload_id": upload_id}

        try:
            # Find similar products via embedding similarity — fetch 50, sample 20 inside generate_ideas
            similar_products = _find_similar_products(session, upload, limit=125)
            log.info("aldi_similar_products", upload_id=upload_id, count=len(similar_products))

            # Build snapshot map for idea enrichment
            product_map = {p["id"]: p for p in similar_products}

            trend_data = {
                "themes": upload.themes,
                "colour_palette": upload.colour_palette,
                "key_materials": upload.key_materials,
                "key_prints": upload.key_prints,
                "product_categories": upload.product_categories,
                "season_occasion": upload.season_occasion,
                "mood_descriptors": upload.mood_descriptors,
            }

            # Pass existing idea names so regeneration produces different results
            from sqlalchemy import select as sa_select
            existing_ideas = session.execute(
                sa_select(AldiProductIdea.name).where(AldiProductIdea.upload_id == upload_id)
            ).scalars().all()

            ideas = asyncio.run(_generate_ideas(trend_data, similar_products, previous_idea_names=list(existing_ideas)))

            if ideas:
                # Clear any stale ideas (retry-safe)
                session.execute(
                    text("DELETE FROM aldi_product_ideas WHERE upload_id = :uid"),
                    {"uid": upload_id},
                )
                session.flush()

                used_inspired_ids: set[int] = set()
                for idea_data in ideas:
                    # Only keep IDs that Claude actually referenced AND exist in product_map
                    # (guards against hallucinated sequential IDs when products list was empty)
                    inspired_ids = [
                        pid for pid in idea_data.get("inspired_by_product_ids", [])
                        if isinstance(pid, int) and pid not in used_inspired_ids and pid in product_map
                    ]
                    # Backfill to minimum 3 from unused products in the pool
                    if len(inspired_ids) < 3:
                        for p in similar_products:
                            if len(inspired_ids) >= 3:
                                break
                            if p["id"] not in used_inspired_ids and p["id"] not in inspired_ids:
                                inspired_ids.append(p["id"])
                    used_inspired_ids.update(inspired_ids)
                    inspired_snapshots = [
                        {
                            "id": pid,
                            "name": product_map[pid]["name"],
                            "retailer_name": product_map[pid]["retailer_name"],
                            "url": product_map[pid]["url"],
                            "image_url": product_map[pid].get("primary_image_url"),
                        }
                        for pid in inspired_ids if pid in product_map
                    ]
                    idea = AldiProductIdea(
                        upload_id=upload_id,
                        position=idea_data.get("position", 0),
                        name=idea_data.get("name", ""),
                        description=idea_data.get("description", ""),
                        category=idea_data.get("category", ""),
                        price_point=idea_data.get("price_point", ""),
                        rationale=idea_data.get("rationale", ""),
                        inspired_by_product_ids=inspired_ids,
                        inspired_by_products=inspired_snapshots,
                    )
                    session.add(idea)

                upload.status = AldiUploadStatus.DONE
                log.info("aldi_ideas_done", upload_id=upload_id, count=len(ideas))
            else:
                upload.status = AldiUploadStatus.FAILED
                upload.error_message = "Idea generation returned no results"

        except Exception as exc:
            log.error("aldi_ideas_failed", upload_id=upload_id, error=str(exc))
            upload.status = AldiUploadStatus.FAILED
            upload.error_message = str(exc)
            upload.updated_at = datetime.utcnow()
            session.commit()
            raise self.retry(exc=exc, countdown=30)

        upload.updated_at = datetime.utcnow()
        session.commit()
        return {"status": "done", "upload_id": upload_id, "ideas": len(ideas or [])}

    finally:
        session.close()


# ── Async helpers ─────────────────────────────────────────────────────────────

async def _vision_analyse(file_path: str, file_type: str) -> dict | None:
    from analysis.aldi_vision import MoodBoardAnalyser
    return await MoodBoardAnalyser().analyse_file(file_path, file_type)


async def _vision_analyse_bytes(data: bytes, file_type: str) -> dict | None:
    from analysis.aldi_vision import MoodBoardAnalyser
    return await MoodBoardAnalyser().analyse_file_bytes(data, file_type)


async def _generate_ideas(trend_data: dict, similar_products: list, previous_idea_names: list[str] | None = None) -> list | None:
    from analysis.aldi_vision import MoodBoardAnalyser
    return await MoodBoardAnalyser().generate_ideas(trend_data, similar_products, n=10, previous_idea_names=previous_idea_names)


# ── Similarity search (sync) ──────────────────────────────────────────────────

def _find_similar_products(session, upload: AldiUpload, limit: int = 125) -> list[dict]:
    """
    Build a keyword embedding from the trend attributes and find the most
    similar products in the DB using pgvector cosine distance.

    Fetches a pool of limit*3 from the DB (the closest neighbourhood), then
    randomly samples `limit` from that pool.  This ensures every run — initial
    or regenerated — receives a different set of products while still staying
    within the relevant similarity zone.  generate_ideas then samples 20 from
    the returned pool for further variety.
    """
    from analysis.embeddings import EmbeddingGenerator

    query_text = " | ".join(filter(None, [
        " ".join(upload.themes or []),
        "Colours: " + ", ".join(upload.colour_palette or []),
        "Materials: " + ", ".join(upload.key_materials or []),
        "Patterns: " + ", ".join(upload.key_prints or []),
        "Categories: " + ", ".join(upload.product_categories or []),
        "Season: " + (upload.season_occasion or ""),
        " ".join(upload.mood_descriptors or []),
    ]))

    if not query_text.strip():
        return []

    gen = EmbeddingGenerator()
    query_vec = gen._keyword_embedding(query_text)
    vec_str = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"
    fetch_limit = limit * 3  # fetch a wide neighbourhood, then sample

    try:
        result = session.execute(text(f"""
            SELECT p.id, p.name, p.url, p.price, p.primary_image_url,
                   p.is_best_seller,
                   r.name AS retailer_name,
                   pa.colours, pa.materials, pa.style_tags, pa.patterns
            FROM products p
            JOIN product_attributes pa ON pa.product_id = p.id
            JOIN retailers r ON r.id = p.retailer_id
            WHERE pa.embedding IS NOT NULL
              AND p.is_active = TRUE
            ORDER BY
              (pa.embedding <=> '{vec_str}'::vector)
              * (CASE WHEN p.is_best_seller THEN 0.7 ELSE 1.0 END)
            LIMIT {fetch_limit}
        """))
        pool = [
            {
                "id": row.id,
                "name": row.name,
                "url": row.url,
                "price": row.price,
                "primary_image_url": row.primary_image_url,
                "is_best_seller": row.is_best_seller,
                "retailer_name": row.retailer_name,
                "colours": row.colours or [],
                "materials": row.materials or [],
                "style_tags": row.style_tags or [],
                "patterns": row.patterns or [],
            }
            for row in result.fetchall()
        ]
        return random.sample(pool, min(limit, len(pool))) if len(pool) > limit else pool
    except Exception as exc:
        log.error("similar_products_failed", error=str(exc))
        return []


# ── Session helpers ───────────────────────────────────────────────────────────

def _maybe_trigger_session_ideas(session, session_id: int) -> None:
    """If all uploads in the session are done/failed, trigger idea generation.

    Uses SELECT ... FOR UPDATE on the session row so only one worker at a
    time evaluates the terminal-state condition — without this, three
    concurrent callers (one per upload task) can each read stale
    identity-map data for their sibling uploads and all three early-return,
    leaving the session stuck at ANALYSING.
    """
    from sqlalchemy import select as sa_select
    # Kill any stale identity-map state from earlier in the task so that
    # sibling upload rows are re-read fresh from the DB.
    session.expire_all()

    terminal = {AldiUploadStatus.DONE, AldiUploadStatus.FAILED}

    # Lock the session row — serialises concurrent callers.
    sess_obj = session.execute(
        sa_select(AldiSession).where(AldiSession.id == session_id).with_for_update()
    ).scalar_one_or_none()
    if not sess_obj:
        log.warning("trigger_session_not_found", session_id=session_id)
        session.commit()
        return

    uploads = session.execute(
        sa_select(AldiUpload).where(AldiUpload.session_id == session_id)
    ).scalars().all()

    statuses = [
        (u.id, u.status.value if hasattr(u.status, "value") else str(u.status))
        for u in uploads
    ]
    sess_status = sess_obj.status.value if hasattr(sess_obj.status, "value") else str(sess_obj.status)
    log.info(
        "trigger_check",
        session_id=session_id,
        upload_count=len(uploads),
        statuses=statuses,
        session_status=sess_status,
    )

    if not uploads:
        session.commit()
        return
    if not all(u.status in terminal for u in uploads):
        log.info("trigger_waiting_for_uploads", session_id=session_id)
        session.commit()  # releases the row lock
        return

    if sess_obj.status != AldiUploadStatus.ANALYSING:
        log.info(
            "trigger_session_not_analysing",
            session_id=session_id,
            current_status=sess_status,
        )
        session.commit()
        return

    sess_obj.status = AldiUploadStatus.GENERATING
    sess_obj.updated_at = datetime.utcnow()
    session.commit()  # releases FOR UPDATE lock

    # Dispatch session-level idea generation
    generate_aldi_session_ideas.delay(session_id)
    log.info("aldi_session_all_done", session_id=session_id, upload_count=len(uploads))


def _find_similar_products_for_session(session, sess_obj: AldiSession, limit: int = 125) -> list[dict]:
    """Find similar products using merged session trend data.

    Fetches a pool of limit*3 from the DB then randomly samples `limit` from it,
    so every call (including each Try Again regeneration) returns a different set
    of products from within the relevant similarity neighbourhood.
    """
    from analysis.embeddings import EmbeddingGenerator

    query_text = " | ".join(filter(None, [
        " ".join(sess_obj.themes or []),
        "Colours: " + ", ".join(sess_obj.colour_palette or []),
        "Materials: " + ", ".join(sess_obj.key_materials or []),
        "Patterns: " + ", ".join(sess_obj.key_prints or []),
        "Categories: " + ", ".join(sess_obj.product_categories or []),
        "Season: " + (sess_obj.season_occasion or ""),
        " ".join(sess_obj.mood_descriptors or []),
    ]))

    if not query_text.strip():
        return []

    gen = EmbeddingGenerator()
    query_vec = gen._keyword_embedding(query_text)
    vec_str = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"
    fetch_limit = limit * 3  # fetch a wide neighbourhood, then sample

    try:
        result = session.execute(text(f"""
            SELECT p.id, p.name, p.url, p.price, p.primary_image_url,
                   p.is_best_seller,
                   r.name AS retailer_name,
                   pa.colours, pa.materials, pa.style_tags, pa.patterns
            FROM products p
            JOIN product_attributes pa ON pa.product_id = p.id
            JOIN retailers r ON r.id = p.retailer_id
            WHERE pa.embedding IS NOT NULL
              AND p.is_active = TRUE
            ORDER BY
              (pa.embedding <=> '{vec_str}'::vector)
              * (CASE WHEN p.is_best_seller THEN 0.7 ELSE 1.0 END)
            LIMIT {fetch_limit}
        """))
        pool = [
            {
                "id": row.id,
                "name": row.name,
                "url": row.url,
                "price": row.price,
                "primary_image_url": row.primary_image_url,
                "is_best_seller": row.is_best_seller,
                "retailer_name": row.retailer_name,
                "colours": row.colours or [],
                "materials": row.materials or [],
                "style_tags": row.style_tags or [],
                "patterns": row.patterns or [],
            }
            for row in result.fetchall()
        ]
        return random.sample(pool, min(limit, len(pool))) if len(pool) > limit else pool
    except Exception as exc:
        log.error("similar_products_for_session_failed", error=str(exc))
        return []


# ── Task 3: Session idea generation ──────────────────────────────────────────

@app.task(bind=True, max_retries=2)
def generate_aldi_session_ideas(self, session_id: int) -> dict:
    """Generate Aldi product ideas by merging analyses from all uploads in a session."""
    from sqlalchemy import select as sa_select
    db_session = _get_session()
    try:
        sess_obj = db_session.get(AldiSession, session_id)
        if not sess_obj:
            return {"status": "not_found", "session_id": session_id}
        if sess_obj.status != AldiUploadStatus.GENERATING:
            return {"status": "skipped", "session_id": session_id}

        # Load all uploads in this session
        uploads = db_session.execute(
            sa_select(AldiUpload).where(AldiUpload.session_id == session_id)
        ).scalars().all()

        try:
            # Merge analyses: deduplicated union of all per-doc fields
            def _merge(lists):
                seen = []
                for item in [x for lst in lists for x in lst]:
                    if item not in seen:
                        seen.append(item)
                return seen

            done_uploads = [u for u in uploads if u.status == AldiUploadStatus.DONE]

            merged_themes = _merge([u.themes or [] for u in done_uploads])
            merged_colours = _merge([u.colour_palette or [] for u in done_uploads])
            merged_hex = _merge([u.colour_hex or [] for u in done_uploads])
            merged_materials = _merge([u.key_materials or [] for u in done_uploads])
            merged_prints = _merge([u.key_prints or [] for u in done_uploads])
            merged_categories = _merge([u.product_categories or [] for u in done_uploads])
            merged_mood = _merge([u.mood_descriptors or [] for u in done_uploads])
            # For season: take most common or first
            seasons = [u.season_occasion for u in done_uploads if u.season_occasion]
            merged_season = seasons[0] if seasons else None

            # Store merged analysis on session
            sess_obj.themes = merged_themes
            sess_obj.colour_palette = merged_colours
            sess_obj.colour_hex = merged_hex
            sess_obj.key_materials = merged_materials
            sess_obj.key_prints = merged_prints
            sess_obj.product_categories = merged_categories
            sess_obj.mood_descriptors = merged_mood
            sess_obj.season_occasion = merged_season
            db_session.flush()

            # Find similar products — fetch 50, sample 20 inside generate_ideas
            similar_products = _find_similar_products_for_session(db_session, sess_obj, limit=125)
            log.info("aldi_session_similar_products", session_id=session_id, count=len(similar_products))

            product_map = {p["id"]: p for p in similar_products}

            trend_data = {
                "themes": merged_themes,
                "colour_palette": merged_colours,
                "key_materials": merged_materials,
                "key_prints": merged_prints,
                "product_categories": merged_categories,
                "season_occasion": merged_season,
                "mood_descriptors": merged_mood,
            }

            # Pass existing idea names so regeneration produces different results
            existing_ideas = db_session.execute(
                sa_select(AldiProductIdea.name).where(AldiProductIdea.session_id == session_id)
            ).scalars().all()

            ideas = asyncio.run(_generate_ideas(trend_data, similar_products, previous_idea_names=list(existing_ideas)))

            if ideas:
                db_session.execute(
                    text("DELETE FROM aldi_product_ideas WHERE session_id = :sid"),
                    {"sid": session_id},
                )
                db_session.flush()

                used_inspired_ids: set[int] = set()
                for idea_data in ideas:
                    # Only keep IDs that Claude actually referenced AND exist in product_map
                    # (guards against hallucinated sequential IDs when products list was empty)
                    inspired_ids = [
                        pid for pid in idea_data.get("inspired_by_product_ids", [])
                        if isinstance(pid, int) and pid not in used_inspired_ids and pid in product_map
                    ]
                    # Backfill to minimum 3 from unused products in the pool
                    if len(inspired_ids) < 3:
                        for p in similar_products:
                            if len(inspired_ids) >= 3:
                                break
                            if p["id"] not in used_inspired_ids and p["id"] not in inspired_ids:
                                inspired_ids.append(p["id"])
                    used_inspired_ids.update(inspired_ids)
                    inspired_snapshots = [
                        {
                            "id": pid,
                            "name": product_map[pid]["name"],
                            "retailer_name": product_map[pid]["retailer_name"],
                            "url": product_map[pid]["url"],
                            "image_url": product_map[pid].get("primary_image_url"),
                        }
                        for pid in inspired_ids if pid in product_map
                    ]
                    idea = AldiProductIdea(
                        session_id=session_id,
                        upload_id=None,
                        generation=1,
                        position=idea_data.get("position", 0),
                        name=idea_data.get("name", ""),
                        description=idea_data.get("description", ""),
                        category=idea_data.get("category", ""),
                        price_point=idea_data.get("price_point", ""),
                        rationale=idea_data.get("rationale", ""),
                        inspired_by_product_ids=inspired_ids,
                        inspired_by_products=inspired_snapshots,
                    )
                    db_session.add(idea)

                sess_obj.status = AldiUploadStatus.DONE
                log.info("aldi_session_ideas_done", session_id=session_id, count=len(ideas))
            else:
                sess_obj.status = AldiUploadStatus.FAILED
                sess_obj.error_message = "Idea generation returned no results"

        except Exception as exc:
            log.error("aldi_session_ideas_failed", session_id=session_id, error=str(exc))
            sess_obj.status = AldiUploadStatus.FAILED
            sess_obj.error_message = str(exc)
            sess_obj.updated_at = datetime.utcnow()
            db_session.commit()
            raise self.retry(exc=exc, countdown=30)

        sess_obj.updated_at = datetime.utcnow()
        db_session.commit()
        return {"status": "done", "session_id": session_id, "ideas": len(ideas or [])}

    finally:
        db_session.close()


# ── Task 4: Regenerate ideas (Try Again) ─────────────────────────────────────

@app.task(bind=True, max_retries=2, queue="aldi")
def regenerate_aldi_session_ideas(self, session_id: int) -> dict:
    """Generate a fresh set of ideas for an existing session (Try Again).

    - Does NOT delete previous generations.
    - Excludes ALL previously generated idea names AND all previously used
      inspired_by_product_ids across every generation so results are truly fresh.
    - Saves new ideas with generation = max_existing_generation + 1.
    """
    from sqlalchemy import select as sa_select
    db_session = _get_session()
    try:
        sess_obj = db_session.get(AldiSession, session_id)
        if not sess_obj:
            return {"status": "not_found", "session_id": session_id}

        # Collect all existing idea names and inspired_by IDs across ALL generations
        existing_ideas_rows = db_session.execute(
            sa_select(AldiProductIdea.name, AldiProductIdea.inspired_by_product_ids,
                      AldiProductIdea.generation)
            .where(AldiProductIdea.session_id == session_id)
        ).all()

        previous_idea_names = [row.name for row in existing_ideas_rows]
        used_inspired_ids: set[int] = set()
        for row in existing_ideas_rows:
            for pid in (row.inspired_by_product_ids or []):
                if isinstance(pid, int):
                    used_inspired_ids.add(pid)

        max_generation = max((row.generation for row in existing_ideas_rows), default=0)
        next_generation = max_generation + 1

        log.info(
            "aldi_regenerate_start",
            session_id=session_id,
            next_generation=next_generation,
            excluded_names=len(previous_idea_names),
            excluded_ids=len(used_inspired_ids),
        )

        try:
            # Find similar products
            similar_products = _find_similar_products_for_session(db_session, sess_obj, limit=125)
            product_map = {p["id"]: p for p in similar_products}

            trend_data = {
                "themes": sess_obj.themes or [],
                "colour_palette": sess_obj.colour_palette or [],
                "key_materials": sess_obj.key_materials or [],
                "key_prints": sess_obj.key_prints or [],
                "product_categories": sess_obj.product_categories or [],
                "season_occasion": sess_obj.season_occasion,
                "mood_descriptors": sess_obj.mood_descriptors or [],
            }

            ideas = asyncio.run(
                _generate_ideas(trend_data, similar_products, previous_idea_names=previous_idea_names)
            )

            if ideas:
                # Deduplicate inspired_by ACROSS all generations using the existing used set.
                # Also filter out any hallucinated IDs not present in product_map.
                new_used: set[int] = set()
                for idea_data in ideas:
                    # Only keep IDs that exist in product_map and aren't already used
                    inspired_ids = [
                        pid for pid in idea_data.get("inspired_by_product_ids", [])
                        if isinstance(pid, int) and pid not in used_inspired_ids
                        and pid not in new_used and pid in product_map
                    ]
                    # Backfill to minimum 3 from unused products
                    if len(inspired_ids) < 3:
                        for p in similar_products:
                            if len(inspired_ids) >= 3:
                                break
                            if (p["id"] not in used_inspired_ids
                                    and p["id"] not in new_used
                                    and p["id"] not in inspired_ids):
                                inspired_ids.append(p["id"])
                    new_used.update(inspired_ids)

                    inspired_snapshots = [
                        {
                            "id": pid,
                            "name": product_map[pid]["name"],
                            "retailer_name": product_map[pid]["retailer_name"],
                            "url": product_map[pid]["url"],
                            "image_url": product_map[pid].get("primary_image_url"),
                        }
                        for pid in inspired_ids if pid in product_map
                    ]
                    idea = AldiProductIdea(
                        session_id=session_id,
                        upload_id=None,
                        generation=next_generation,
                        position=idea_data.get("position", 0),
                        name=idea_data.get("name", ""),
                        description=idea_data.get("description", ""),
                        category=idea_data.get("category", ""),
                        price_point=idea_data.get("price_point", ""),
                        rationale=idea_data.get("rationale", ""),
                        inspired_by_product_ids=inspired_ids,
                        inspired_by_products=inspired_snapshots,
                    )
                    db_session.add(idea)

                sess_obj.status = AldiUploadStatus.DONE
                log.info("aldi_regenerate_done", session_id=session_id,
                         generation=next_generation, count=len(ideas))
            else:
                sess_obj.status = AldiUploadStatus.FAILED
                sess_obj.error_message = "Idea regeneration returned no results"

        except Exception as exc:
            log.error("aldi_regenerate_failed", session_id=session_id, error=str(exc))
            sess_obj.status = AldiUploadStatus.FAILED
            sess_obj.error_message = str(exc)
            sess_obj.updated_at = datetime.utcnow()
            db_session.commit()
            raise self.retry(exc=exc, countdown=30)

        sess_obj.updated_at = datetime.utcnow()
        db_session.commit()
        return {"status": "done", "session_id": session_id, "generation": next_generation,
                "ideas": len(ideas or [])}

    finally:
        db_session.close()


# ── Housekeeping ──────────────────────────────────────────────────────────────

@app.task
def finalise_stale_aldi_sessions():
    """Safety net: flip any Aldi session stuck in UPLOADING for >24h.

    If all uploads already finished, dispatch idea generation directly;
    otherwise the per-upload worker's trigger helper will fire when the
    last analysis lands.
    """
    from datetime import datetime as _dt, timedelta as _td
    from sqlalchemy import select as sa_select
    cutoff = _dt.utcnow() - _td(hours=24)
    db = _get_session()
    try:
        stale = db.execute(
            sa_select(AldiSession).where(
                AldiSession.status == AldiUploadStatus.UPLOADING,
                AldiSession.created_at < cutoff,
            )
        ).scalars().all()
        finalised = 0
        dispatched = 0
        for s in stale:
            uploads = db.execute(
                sa_select(AldiUpload).where(AldiUpload.session_id == s.id)
            ).scalars().all()
            if not uploads:
                s.status = AldiUploadStatus.FAILED
                s.error_message = "Abandoned (no uploads) — auto-failed after 24h"
                s.updated_at = datetime.utcnow()
                finalised += 1
                continue
            s.status = AldiUploadStatus.ANALYSING
            s.updated_at = datetime.utcnow()
            finalised += 1
            if all(u.status in (AldiUploadStatus.DONE, AldiUploadStatus.FAILED) for u in uploads):
                s.status = AldiUploadStatus.GENERATING
                generate_aldi_session_ideas.delay(s.id)
                dispatched += 1
        db.commit()
        log.info("finalise_stale_aldi_sessions", finalised=finalised, dispatched=dispatched)
        return {"finalised": finalised, "dispatched": dispatched}
    finally:
        db.close()
