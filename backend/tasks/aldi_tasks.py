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
                # Claude decides whether the board is thematic enough to
                # warrant literal-keyword pre-filtering downstream.
                # Empty list means "stylistic — use pure semantic search".
                upload.filter_keywords = [
                    k.strip().lower() for k in (result.get("filter_keywords") or [])
                    if isinstance(k, str) and k.strip()
                ]
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


async def _cluster_products(trend_data: dict, similar_products: list, previous_cluster_names: list[str] | None = None) -> list | None:
    """New (2026-08-06) shape: ask Claude to cluster real catalogue products
    into buyable sub-themes. Returns [{name, description, category,
    product_ids, filter_keywords}, ...] or None on failure."""
    from analysis.aldi_vision import MoodBoardAnalyser
    return await MoodBoardAnalyser().cluster_products(
        trend_data, similar_products, previous_cluster_names=previous_cluster_names,
    )


def _persist_clusters_as_ideas(
    db_session,
    session_id: int,
    generation: int,
    clusters: list[dict],
    product_map: dict[int, dict],
) -> int:
    """Write clustering results as AldiProductIdea rows with kind='cluster'.
    Returns the number of rows written. Snapshots enough product info for
    the card's hero image mosaic without needing another DB round-trip."""
    written = 0
    for pos, cluster in enumerate(clusters):
        pids = cluster.get("product_ids") or []
        snapshots = [
            {
                "id": pid,
                "name": product_map[pid]["name"],
                "retailer_name": product_map[pid]["retailer_name"],
                "url": product_map[pid]["url"],
                "image_url": product_map[pid].get("primary_image_url"),
                "is_best_seller": bool(product_map[pid].get("is_best_seller")),
            }
            for pid in pids if pid in product_map
        ]
        # Rank the snapshots best-sellers first — the frontend uses the
        # first few for the card's hero mosaic.
        snapshots.sort(key=lambda s: (0 if s.get("is_best_seller") else 1))
        idea = AldiProductIdea(
            session_id=session_id,
            upload_id=None,
            generation=generation,
            position=pos,
            name=cluster.get("name", ""),
            description=cluster.get("description", ""),
            category=cluster.get("category", "Household > Homewares"),
            price_point="",  # not applicable for cluster shape
            rationale="",    # not applicable for cluster shape
            inspired_by_product_ids=pids,
            inspired_by_products=snapshots,
            filter_keywords=cluster.get("filter_keywords") or [],
            kind="cluster",
        )
        db_session.add(idea)
        written += 1
    return written


def _persist_out_of_scope(
    db_session,
    session_id: int,
    generation: int,
    total_matched: int,
    filter_keywords: list[str],
) -> None:
    """Write a single 'Mood board out of scope' cluster when the semantic
    search returned fewer than 5 matching products. Buyer sees an honest
    explanation instead of a wall of bad recommendations."""
    kw_hint = (", ".join(filter_keywords[:6]) if filter_keywords else "the mood board's theme")
    description = (
        f"Only {total_matched} product{'s' if total_matched != 1 else ''} in our "
        f"156k-product catalogue matched {kw_hint}. This mood board likely falls "
        f"outside our home-décor / storage / tabletop / kitchenware coverage. "
        f"Try a mood board that leans into those categories."
    )
    idea = AldiProductIdea(
        session_id=session_id,
        upload_id=None,
        generation=generation,
        position=0,
        name="Mood board out of scope",
        description=description,
        category="Household > Homewares",
        price_point="",
        rationale="",
        inspired_by_product_ids=[],
        inspired_by_products=[],
        filter_keywords=[],
        kind="out_of_scope",
    )
    db_session.add(idea)


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
    from analysis.embeddings import embed_text_sync

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

    query_vec = embed_text_sync(query_text)
    if query_vec is None:
        return []
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


def _find_similar_products_for_session(session, sess_obj: AldiSession, limit: int = 250) -> tuple[list[dict], int]:
    """Find products matching the merged session mood board.

    Two paths depending on whether Claude flagged the mood board as
    thematic (via filter_keywords) or stylistic (empty list):

      * THEMATIC (Halloween, Christmas, etc.) — pre-filter product names
        by word-boundary regex against the keywords, then semantic-rank
        WITHIN that filtered subset. Guarantees the pool is
        actually-on-theme instead of colour/material-adjacent noise.
      * STYLISTIC (default) — pure semantic search across the whole
        catalogue as before.

    Also applies the Aldi image-quality gate — buyers must never see a
    product without a proper product image.

    Returns a tuple of (top_ranked_pool, total_matched_count). The pool
    is the deterministic top-N by similarity (best-sellers weighted
    higher) — fed to Claude for clustering. The count is the total
    number of image-qualified products matching the theme, used to
    power the low-match warning banner on the frontend.
    """
    from analysis.embeddings import embed_text_sync
    from analysis.image_filter import image_ok_sql

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
        return ([], 0)

    query_vec = embed_text_sync(query_text)
    if query_vec is None:
        return ([], 0)
    vec_str = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"

    # Build the pre-filter regex if this is a thematic mood board.
    # Postgres \y is a word boundary. Case-insensitive via ~*.
    keywords = [k for k in (sess_obj.filter_keywords or []) if k]
    keyword_clause = ""
    keyword_bind: dict = {}
    total_matches = 0
    if keywords:
        import re as _re
        pattern = r"\y(?:" + "|".join(_re.escape(k) for k in keywords) + r")\y"
        keyword_clause = "AND p.name ~* :kw_pattern"
        keyword_bind["kw_pattern"] = pattern
        log.info("aldi_session_thematic_filter",
                 session_id=sess_obj.id, keywords=keywords)

    # Image-quality gate — never surface a product without a proper image.
    image_clause = f"AND {image_ok_sql()}"

    try:
        # Count matching products first so the UI knows the true scope
        count_sql = text(f"""
            SELECT COUNT(*) FROM products p
            JOIN product_attributes pa ON pa.product_id = p.id
            WHERE pa.embedding IS NOT NULL
              AND p.is_active = TRUE
              {keyword_clause}
              {image_clause}
        """)
        total_matches = session.execute(count_sql, keyword_bind).scalar() or 0

        # Deterministic top-N by similarity (best-sellers weighted higher).
        # Try Again variation comes from Claude's clustering non-determinism,
        # not from random sampling of the input pool.
        rank_sql = text(f"""
            SELECT p.id, p.name, p.url, p.price, p.primary_image_url,
                   p.is_best_seller,
                   r.name AS retailer_name,
                   pa.colours, pa.materials, pa.style_tags, pa.patterns
            FROM products p
            JOIN product_attributes pa ON pa.product_id = p.id
            JOIN retailers r ON r.id = p.retailer_id
            WHERE pa.embedding IS NOT NULL
              AND p.is_active = TRUE
              {keyword_clause}
              {image_clause}
            ORDER BY
              (pa.embedding <=> '{vec_str}'::vector)
              * (CASE WHEN p.is_best_seller THEN 0.7 ELSE 1.0 END)
            LIMIT {limit}
        """)
        result = session.execute(rank_sql, keyword_bind)
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
        return (pool, total_matches)
    except Exception as exc:
        log.error("similar_products_for_session_failed", error=str(exc))
        return ([], 0)


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
            merged_filter_keywords = _merge([u.filter_keywords or [] for u in done_uploads])
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
            sess_obj.filter_keywords = merged_filter_keywords
            db_session.flush()

            # Find similar products — fetch 50, sample 20 inside generate_ideas
            similar_products, similar_count = _find_similar_products_for_session(
                db_session, sess_obj, limit=250,
            )
            sess_obj.similar_products_count = similar_count
            log.info("aldi_session_similar_products",
                     session_id=session_id,
                     sampled=len(similar_products),
                     total_matched=similar_count,
                     thematic=bool(merged_filter_keywords))

            product_map = {p["id"]: p for p in similar_products}

            trend_data = {
                "themes": merged_themes,
                "colour_palette": merged_colours,
                "key_materials": merged_materials,
                "key_prints": merged_prints,
                "product_categories": merged_categories,
                "season_occasion": merged_season,
                "mood_descriptors": merged_mood,
                # Carried into the idea prompt so Claude can honestly
                # explain when the mood board is out-of-scope.
                "total_matched_products": similar_count,
                "filter_keywords": merged_filter_keywords,
            }

            # Wipe any prior ideas for this session — a fresh generation
            # replaces them entirely. Try Again (regenerate) preserves
            # history; this path is only the first run.
            db_session.execute(
                text("DELETE FROM aldi_product_ideas WHERE session_id = :sid"),
                {"sid": session_id},
            )
            db_session.flush()

            # Very-few-matches path — write a single 'out of scope' card
            # instead of asking Claude to invent nonsense from 2 products.
            if similar_count < 5:
                _persist_out_of_scope(
                    db_session, session_id, generation=1,
                    total_matched=similar_count,
                    filter_keywords=merged_filter_keywords,
                )
                sess_obj.status = AldiUploadStatus.DONE
                log.info("aldi_session_out_of_scope",
                         session_id=session_id, matched=similar_count)
                ideas = [None]  # so the return dict says 'ideas: 1' rather than 'ideas: 0'
            else:
                # Clustering path — group the semantic sample into buyable sub-themes
                clusters = asyncio.run(_cluster_products(trend_data, similar_products))
                if clusters:
                    _persist_clusters_as_ideas(
                        db_session, session_id, generation=1,
                        clusters=clusters, product_map=product_map,
                    )
                    sess_obj.status = AldiUploadStatus.DONE
                    log.info("aldi_session_clusters_done",
                             session_id=session_id, clusters=len(clusters))
                    ideas = clusters
                else:
                    sess_obj.status = AldiUploadStatus.FAILED
                    sess_obj.error_message = "Clustering returned no results"
                    ideas = None

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
            # Find similar products. limit=250 matches the fresh flow —
            # Claude gets a wide sample to cluster over, and total_matched
            # is the count of image-qualified products in the whole
            # thematic pool (used by the low-match warning + downstream
            # live product queries).
            similar_products, similar_count = _find_similar_products_for_session(
                db_session, sess_obj, limit=250,
            )
            sess_obj.similar_products_count = similar_count
            product_map = {p["id"]: p for p in similar_products}

            trend_data = {
                "themes": sess_obj.themes or [],
                "colour_palette": sess_obj.colour_palette or [],
                "key_materials": sess_obj.key_materials or [],
                "key_prints": sess_obj.key_prints or [],
                "product_categories": sess_obj.product_categories or [],
                "season_occasion": sess_obj.season_occasion,
                "mood_descriptors": sess_obj.mood_descriptors or [],
                "total_matched_products": similar_count,
                "filter_keywords": sess_obj.filter_keywords or [],
            }

            # Very-few-matches path — same treatment as fresh flow.
            if similar_count < 5:
                _persist_out_of_scope(
                    db_session, session_id, generation=next_generation,
                    total_matched=similar_count,
                    filter_keywords=sess_obj.filter_keywords or [],
                )
                sess_obj.status = AldiUploadStatus.DONE
                log.info("aldi_regenerate_out_of_scope",
                         session_id=session_id, generation=next_generation,
                         matched=similar_count)
                ideas = [None]
            else:
                # Clustering path — buyer clicked Try Again, so exclude
                # every prior cluster name across every set to force
                # genuinely different framings from Claude.
                clusters = asyncio.run(
                    _cluster_products(
                        trend_data, similar_products,
                        previous_cluster_names=previous_idea_names,
                    )
                )
                if clusters:
                    _persist_clusters_as_ideas(
                        db_session, session_id, generation=next_generation,
                        clusters=clusters, product_map=product_map,
                    )
                    sess_obj.status = AldiUploadStatus.DONE
                    log.info("aldi_regenerate_clusters_done",
                             session_id=session_id, generation=next_generation,
                             clusters=len(clusters))
                    ideas = clusters
                else:
                    sess_obj.status = AldiUploadStatus.FAILED
                    sess_obj.error_message = "Cluster regeneration returned no results"
                    ideas = None

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
