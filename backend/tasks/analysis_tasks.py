"""Celery tasks for AI product analysis and trend generation."""
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, create_engine
from tasks.celery_app import app
from config import settings
from database.models import Product, ProductAttributes, ScrapeStatus
import structlog

log = structlog.get_logger()
engine = create_engine(settings.database_url_sync)


def _get_session():
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=engine)()


@app.task(bind=True, max_retries=3, queue="analysis", rate_limit="60/m",
          soft_time_limit=120, time_limit=150)
def analyse_product(self, product_id: int):
    """Run vision + NLP + embedding analysis on a single product."""
    from celery.exceptions import SoftTimeLimitExceeded
    session = _get_session()
    try:
        product = session.get(Product, product_id)
        if not product:
            return

        product.analysis_status = ScrapeStatus.RUNNING
        session.commit()

        try:
            result = asyncio.run(_analyse(product))

            # Upsert ProductAttributes
            existing = session.execute(
                select(ProductAttributes).where(ProductAttributes.product_id == product_id)
            ).scalar_one_or_none()

            if existing:
                for k, v in result.items():
                    setattr(existing, k, v)
                existing.updated_at = datetime.utcnow()
            else:
                attrs = ProductAttributes(product_id=product_id, **result)
                session.add(attrs)

            product.analysis_status = ScrapeStatus.SUCCESS
            product.analysed_at = datetime.utcnow()
            session.commit()
            return {"status": "success", "product_id": product_id}

        except SoftTimeLimitExceeded:
            log.warning("analysis_timeout", product_id=product_id)
            product.analysis_status = ScrapeStatus.FAILED
            session.commit()
            return {"status": "timeout", "product_id": product_id}

        except Exception as exc:
            log.error("analysis_failed", product_id=product_id, error=str(exc))
            product.analysis_status = ScrapeStatus.FAILED
            session.commit()
            raise self.retry(exc=exc, countdown=60)

    finally:
        session.close()


async def _analyse(product: Product) -> dict:
    """Run all three analysis steps concurrently where possible."""
    from analysis.vision import VisionAnalyser
    from analysis.nlp import NLPExtractor
    from analysis.embeddings import EmbeddingGenerator

    vision = VisionAnalyser()
    nlp = NLPExtractor()
    embedder = EmbeddingGenerator()

    try:
        # Vision + NLP can run concurrently
        vision_result, nlp_result = await asyncio.gather(
            vision.analyse_product(product.image_urls or []),
            nlp.extract(product.name, product.description or "", product.raw_attributes or {}),
        )

        # Embedding uses both results
        embedding = await embedder.generate(
            product.name,
            product.description or "",
            vision_result,
            nlp_result,
        )
    finally:
        # Close the Anthropic clients while the event loop is still alive so
        # their httpx connections don't get torn down on a closed loop.
        await vision.aclose()
        await nlp.aclose()

    v = vision_result or {}
    n = nlp_result or {}

    # Merge style_tags from both sources
    style_tags = list(set(v.get("style_tags", []) + n.get("style_tags", [])))

    # Normalise non-committal AI responses so they fall into useful filter buckets.
    # Without this, products end up filed as "unknown"/"any" and never match any
    # filter. Season "unknown" becomes "all-season" (i.e. works year-round);
    # room "unknown"/"any" becomes "multiple" (i.e. fits anywhere). Claude's
    # string for "I don't know" is truthy so we can't rely on the `or` fallback
    # to the NLP result either — we coerce here instead.
    def _season(val):
        if not val: return None
        s = str(val).strip().lower()
        if s in ("unknown", "null", ""): return "all-season"
        return s

    def _room(val):
        if not val: return None
        r = str(val).strip().lower()
        if r in ("unknown", "any", "null", ""): return "multiple"
        return r

    season = _season(v.get("season")) or _season(n.get("season")) or "all-season"
    room = _room(v.get("room")) or _room(n.get("room")) or "multiple"

    return {
        "colours": v.get("colours", []),
        "colour_hex": v.get("colour_hex", []),
        "shape": v.get("shape"),
        "size_descriptor": v.get("size_descriptor") or (n.get("size_mentions") or [None])[0],
        "finish": v.get("finish"),
        "style_tags": style_tags,
        "materials": n.get("materials", []),
        "patterns": n.get("patterns", []),
        "fragrance": (n.get("fragrance") or "")[:500] or None,
        "season": season,
        "occasion": n.get("occasion"),
        "room": room,
        "function_tags": n.get("function_tags", []),
        "embedding": embedding,
        "vision_confidence": v.get("confidence"),
        "nlp_confidence": n.get("confidence"),
    }


@app.task(queue="analysis")
def analyse_pending_products(retailer_id: int | None = None):
    """Queue analysis for every product that has never been analysed (or previously failed).

    Args:
        retailer_id: If supplied, only products for that retailer are queued.
                     If None, all retailers are included.
    """
    session = _get_session()
    try:
        from database.models import Product
        from sqlalchemy import select

        # Only analyse ACTIVE products — Historical (deactivated) products
        # shouldn't consume Claude tokens.
        q = select(Product).where(
            Product.is_active == True,
            Product.analysis_status.in_([ScrapeStatus.PENDING, ScrapeStatus.FAILED]),
        )
        if retailer_id is not None:
            q = q.where(Product.retailer_id == retailer_id)

        products = session.execute(q).scalars().all()

        for product in products:
            analyse_product.delay(product.id)

        log.info(
            "analyse_pending_queued",
            count=len(products),
            retailer_id=retailer_id,
        )
        return {"queued": len(products), "retailer_id": retailer_id}
    finally:
        session.close()


@app.task(bind=True, queue="reports")
def run_trend_analysis_task(self):
    """Run the trend clustering and report generation (manual trigger only)."""
    asyncio.run(_run_trend_analysis(self))


@app.task(bind=True, queue="reports")
def regenerate_trend_analysis_task(self):
    """Re-run trend analysis adding a new generation (Try Again)."""
    asyncio.run(_run_trend_analysis(self))


async def _run_trend_analysis(task):
    from database.db import AsyncSessionLocal, async_engine
    from analysis.trend_engine import TrendEngine

    await async_engine.dispose()

    async with AsyncSessionLocal() as session:
        engine_instance = TrendEngine(session, task=task)
        report = await engine_instance.regenerate_analysis()
        if report:
            log.info(
                "trend_analysis_complete",
                report_id=report.id,
                generation_count=report.generation_count,
                trends=len(report.trend_ids),
            )


@app.task(bind=True, queue="reports")
def run_fragrance_trend_analysis_task(self):
    """Run the fragrance trend clustering and report generation (manual trigger only)."""
    asyncio.run(_run_fragrance_trend_analysis(self))


@app.task(bind=True, queue="reports")
def regenerate_fragrance_trend_analysis_task(self):
    """Re-run fragrance trend analysis adding a new generation (Try Again)."""
    asyncio.run(_run_fragrance_trend_analysis(self))


async def _run_fragrance_trend_analysis(task):
    from database.db import AsyncSessionLocal, async_engine
    from analysis.fragrance_trend_engine import FragranceTrendEngine

    await async_engine.dispose()

    async with AsyncSessionLocal() as session:
        engine_instance = FragranceTrendEngine(session, task=task)
        report = await engine_instance.regenerate_analysis()
        if report:
            log.info(
                "fragrance_analysis_complete",
                report_id=report.id,
                generation_count=report.generation_count,
                trends=len(report.trend_ids),
            )


@app.task(queue="analysis")
def reset_stuck_analyses():
    """
    Reset products stuck in RUNNING back to PENDING so they get retried.
    A product is considered stuck if it has been RUNNING for more than 10 minutes.
    Runs periodically via Celery beat.
    """
    session = _get_session()
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=10)
        stuck = session.execute(
            select(Product).where(
                Product.analysis_status == ScrapeStatus.RUNNING,
                Product.analysed_at == None,  # noqa: E711
            )
        ).scalars().all()

        reset_ids = []
        for product in stuck:
            product.analysis_status = ScrapeStatus.PENDING
            reset_ids.append(product.id)

        if reset_ids:
            session.commit()
            log.warning("stuck_analyses_reset", count=len(reset_ids), product_ids=reset_ids[:10])
            for pid in reset_ids:
                analyse_product.delay(pid)

        return {"reset": len(reset_ids)}
    finally:
        session.close()
