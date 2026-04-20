"""Celery tasks for In-store Products feature."""
import asyncio
import random
from datetime import datetime as dt
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy import create_engine, select, text
from tasks.celery_app import app
from database.models import InStoreProduct, InStoreSession, InStoreStatus
from config import settings
import structlog

log = structlog.get_logger()
engine = create_engine(settings.database_url_sync)


def _get_session():
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=engine)()


@app.task(bind=True, max_retries=2)
def analyse_instore_product(self, product_id: int, file_b64: str | None = None):
    """Analyse a single in-store product photo via Claude Vision.

    If `file_b64` is supplied, the image bytes are decoded and analysed
    directly. This avoids requiring a shared filesystem between the API
    container (where the file was originally written) and the worker
    container. Falls back to reading from disk if no bytes were passed.
    """
    db = _get_session()
    try:
        product = db.get(InStoreProduct, product_id)
        if not product:
            return {"error": "not found"}

        product.status = InStoreStatus.ANALYSING
        db.commit()

        from analysis.instore_vision import InStoreProductAnalyser
        import base64 as _b64
        analyser = InStoreProductAnalyser()
        if file_b64:
            raw_bytes = _b64.b64decode(file_b64)
            result = asyncio.run(analyser.analyse_product_bytes(raw_bytes, product.file_type))
        else:
            result = asyncio.run(analyser.analyse_product_photo(product.file_path, product.file_type))

        if result:
            product.product_name = result.get("product_name")
            product.category = result.get("category")
            product.price = result.get("price")
            product.colours = result.get("colours", [])
            product.materials = result.get("materials", [])
            product.style_tags = result.get("style_tags", [])
            product.patterns = result.get("patterns", [])
            product.mood = result.get("mood", [])
            product.raw_analysis = result
            product.status = InStoreStatus.DONE
        else:
            product.status = InStoreStatus.FAILED
            product.error_message = "Vision analysis returned no result"

        db.commit()

        # Check if all products in session are done/failed → trigger trend report
        # BUT: if session is still UPLOADING, user may add more photos — wait for finalise.
        session_id = product.session_id
        # Row-lock the session and refresh identity map to avoid race conditions
        # when multiple workers finish simultaneously.
        db.expire_all()
        session = db.execute(
            select(InStoreSession).where(InStoreSession.id == session_id).with_for_update()
        ).scalar_one_or_none()
        if session and session.status == InStoreStatus.ANALYSING:
            all_products = db.execute(
                select(InStoreProduct).where(InStoreProduct.session_id == session_id)
            ).scalars().all()
            pending = [p for p in all_products if p.status in (InStoreStatus.PENDING, InStoreStatus.ANALYSING)]
            if not pending:
                log.info("instore_session_all_done", session_id=session_id, total=len(all_products))
                generate_instore_trend_report.delay(session_id)
        elif session and session.status == InStoreStatus.UPLOADING:
            log.info("instore_trigger_waiting_for_finalise", session_id=session_id)
        db.commit()  # release the row lock

        return {"status": "done", "product_id": product_id}
    except Exception as exc:
        db.rollback()
        log.error("analyse_instore_product_failed", product_id=product_id, error=str(exc))
        try:
            product = db.get(InStoreProduct, product_id)
            if product:
                product.status = InStoreStatus.FAILED
                product.error_message = str(exc)
                db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


def _find_products_for_trend(db, trend: dict, sample_size: int = 15) -> list[dict]:
    """Find products from the main database that align with a trend.

    Fetches the top sample_size*5 by cosine similarity, then randomly samples
    sample_size from that pool so each run returns a different set.
    """
    from analysis.embeddings import EmbeddingGenerator

    name = trend.get("name", "")
    colours = trend.get("colours") or []
    materials = trend.get("materials") or []
    style_tags = trend.get("style_tags") or []

    query_parts = list(filter(None, [
        name,
        ("Colours: " + ", ".join(colours)) if colours else "",
        ("Materials: " + ", ".join(materials)) if materials else "",
        ("Style: " + ", ".join(style_tags)) if style_tags else "",
    ]))
    query_text = " | ".join(query_parts)

    if not query_text.strip():
        return []

    fetch_limit = sample_size * 5  # wide neighbourhood to sample from

    try:
        gen = EmbeddingGenerator()
        query_vec = gen._keyword_embedding(query_text)
        vec_str = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"

        result = db.execute(text(f"""
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
                "price": float(row.price) if row.price is not None else None,
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
        return random.sample(pool, min(sample_size, len(pool))) if len(pool) > sample_size else pool
    except Exception as exc:
        log.error("instore_find_products_for_trend_failed", trend=name, error=str(exc))
        return []

def _build_trend_report(db, session, product_data: list[dict], products, previous_trend_names: list[str] | None = None) -> list[dict] | None:
    """Call Claude to generate a trend report, then enrich each trend with
    in-store photo snapshots and randomly-sampled suggested products."""
    from analysis.instore_vision import InStoreProductAnalyser
    analyser = InStoreProductAnalyser()
    result = asyncio.run(analyser.generate_trend_report(product_data, previous_trend_names=previous_trend_names))
    if not result:
        return None

    trend_list = result["trends"]
    chosen_lens = result["lens"]

    product_map = {p.id: p for p in products}
    for trend in trend_list:
        # Attach in-store photo thumbnails
        trend["products"] = [
            {
                "id": p.id,
                "product_name": p.product_name or p.filename,
                "category": p.category,
                "filename": p.filename,
                "file_type": p.file_type,
            }
            for pid in trend.get("product_ids", [])
            if (p := product_map.get(pid))
        ]
        # Randomly-sampled suggested products from the main DB
        trend["suggested_products"] = _find_products_for_trend(db, trend)

    return trend_list, chosen_lens


@app.task(bind=True, max_retries=2)
def generate_instore_trend_report(self, session_id: int):
    """Generate trend report from all analysed products in a session (generation 1)."""
    db = _get_session()
    try:
        session = db.get(InStoreSession, session_id)
        if not session:
            return {"error": "session not found"}

        session.status = InStoreStatus.GENERATING
        db.commit()

        products = db.execute(
            select(InStoreProduct).where(
                InStoreProduct.session_id == session_id,
                InStoreProduct.status == InStoreStatus.DONE,
            )
        ).scalars().all()

        if not products:
            session.status = InStoreStatus.FAILED
            session.error_message = "No products were successfully analysed"
            db.commit()
            return {"error": "no analysed products"}

        product_data = [
            {
                "id": p.id,
                "product_name": p.product_name or p.filename,
                "category": p.category,
                "colours": p.colours or [],
                "materials": p.materials or [],
                "style_tags": p.style_tags or [],
                "patterns": p.patterns or [],
                "mood": p.mood or [],
            }
            for p in products
        ]

        build_result = _build_trend_report(db, session, product_data, products)
        if build_result:
            trend_list, chosen_lens = build_result
            # Store as generation 1
            generation_entry = {
                "generation": 1,
                "lens": chosen_lens,
                "created_at": dt.utcnow().isoformat(),
                "trends": trend_list,
            }
            session.trend_report = trend_list
            session.generation_count = 1
            session.trend_report_all = [generation_entry]
            session.status = InStoreStatus.DONE
            log.info("instore_trend_report_done", session_id=session_id, trends=len(trend_list), lens=chosen_lens)
        else:
            session.status = InStoreStatus.FAILED
            session.error_message = "Trend report generation failed"

        db.commit()
        return {"status": "done", "session_id": session_id, "trends": len(trend_list) if build_result else 0}
    except Exception as exc:
        db.rollback()
        log.error("generate_instore_trend_report_failed", session_id=session_id, error=str(exc))
        try:
            session = db.get(InStoreSession, session_id)
            if session:
                session.status = InStoreStatus.FAILED
                session.error_message = str(exc)
                db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


@app.task
def finalise_stale_instore_sessions():
    """Safety net: flip any session stuck in UPLOADING for >24h to ANALYSING.

    Mirrors the /finalise endpoint's behaviour: if all products already
    finished, dispatch the trend report directly; otherwise the per-product
    worker will trigger it when the last one lands.
    """
    from datetime import datetime as _dt, timedelta as _td
    cutoff = _dt.utcnow() - _td(hours=24)
    db = _get_session()
    try:
        stale = db.execute(
            select(InStoreSession).where(
                InStoreSession.status == InStoreStatus.UPLOADING,
                InStoreSession.created_at < cutoff,
            )
        ).scalars().all()
        finalised = 0
        dispatched = 0
        for s in stale:
            products = db.execute(
                select(InStoreProduct).where(InStoreProduct.session_id == s.id)
            ).scalars().all()
            if not products:
                # Empty UPLOADING session — nothing to analyse, mark failed
                s.status = InStoreStatus.FAILED
                s.error_message = "Abandoned (no photos) — auto-failed after 24h"
                finalised += 1
                continue
            s.status = InStoreStatus.ANALYSING
            finalised += 1
            if all(p.status in (InStoreStatus.DONE, InStoreStatus.FAILED) for p in products):
                generate_instore_trend_report.delay(s.id)
                dispatched += 1
        db.commit()
        log.info("finalise_stale_instore_sessions", finalised=finalised, dispatched=dispatched)
        return {"finalised": finalised, "dispatched": dispatched}
    finally:
        db.close()


@app.task(bind=True, max_retries=2)
def regenerate_instore_trend_report(self, session_id: int):
    """Generate a fresh trend report for an existing session (Try Again).

    - Keeps all previous generations intact.
    - Passes all previous trend names as exclusions so Claude identifies fresh trends.
    - Uses a randomly-sampled product pool so suggested products differ each run.
    """
    db = _get_session()
    try:
        session = db.get(InStoreSession, session_id)
        if not session:
            return {"error": "session not found"}

        # Collect all previous trend names across all generations for exclusion
        all_reports = session.trend_report_all or []
        previous_trend_names = [
            t["name"]
            for entry in all_reports
            for t in entry.get("trends", [])
        ]
        next_generation = (session.generation_count or 1) + 1

        log.info("instore_regenerate_start", session_id=session_id,
                 next_generation=next_generation, excluded_names=len(previous_trend_names))

        products = db.execute(
            select(InStoreProduct).where(
                InStoreProduct.session_id == session_id,
                InStoreProduct.status == InStoreStatus.DONE,
            )
        ).scalars().all()

        if not products:
            session.status = InStoreStatus.FAILED
            session.error_message = "No analysed products found"
            db.commit()
            return {"error": "no analysed products"}

        product_data = [
            {
                "id": p.id,
                "product_name": p.product_name or p.filename,
                "category": p.category,
                "colours": p.colours or [],
                "materials": p.materials or [],
                "style_tags": p.style_tags or [],
                "patterns": p.patterns or [],
                "mood": p.mood or [],
            }
            for p in products
        ]

        build_result = _build_trend_report(db, session, product_data, products,
                                           previous_trend_names=previous_trend_names)
        if build_result:
            trend_list, chosen_lens = build_result
            generation_entry = {
                "generation": next_generation,
                "lens": chosen_lens,
                "created_at": dt.utcnow().isoformat(),
                "trends": trend_list,
            }
            session.trend_report = trend_list          # latest generation
            session.generation_count = next_generation
            session.trend_report_all = all_reports + [generation_entry]
            session.status = InStoreStatus.DONE
            log.info("instore_regenerate_done", session_id=session_id,
                     generation=next_generation, trends=len(trend_list), lens=chosen_lens)
        else:
            session.status = InStoreStatus.FAILED
            session.error_message = "Trend report regeneration failed"

        db.commit()
        return {"status": "done", "session_id": session_id,
                "generation": next_generation, "trends": len(trend_list) if build_result else 0}
    except Exception as exc:
        db.rollback()
        log.error("instore_regenerate_failed", session_id=session_id, error=str(exc))
        try:
            session = db.get(InStoreSession, session_id)
            if session:
                session.status = InStoreStatus.FAILED
                session.error_message = str(exc)
                db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()
