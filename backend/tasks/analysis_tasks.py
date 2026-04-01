"""Celery tasks for AI product analysis and trend generation."""
import asyncio
from datetime import datetime
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


@app.task(bind=True, max_retries=3, queue="analysis", rate_limit="10/m")
def analyse_product(self, product_id: int):
    """Run vision + NLP + embedding analysis on a single product."""
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

    v = vision_result or {}
    n = nlp_result or {}

    # Merge style_tags from both sources
    style_tags = list(set(v.get("style_tags", []) + n.get("style_tags", [])))

    return {
        "colours": v.get("colours", []),
        "colour_hex": v.get("colour_hex", []),
        "shape": v.get("shape"),
        "size_descriptor": v.get("size_descriptor") or (n.get("size_mentions") or [None])[0],
        "finish": v.get("finish"),
        "style_tags": style_tags,
        "materials": n.get("materials", []),
        "patterns": n.get("patterns", []),
        "fragrance": n.get("fragrance"),
        "season": v.get("season") or n.get("season"),
        "occasion": n.get("occasion"),
        "room": v.get("room") or n.get("room"),
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

        q = select(Product).where(
            Product.analysis_status.in_([ScrapeStatus.PENDING, ScrapeStatus.FAILED])
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


@app.task(queue="analysis")
def run_trend_analysis():
    """Run the weekly trend clustering and report generation."""
    asyncio.run(_run_trend_analysis())


async def _run_trend_analysis():
    from database.db import AsyncSessionLocal
    from analysis.trend_engine import TrendEngine

    async with AsyncSessionLocal() as session:
        engine_instance = TrendEngine(session)
        report = await engine_instance.run_weekly_analysis()
        if report:
            log.info("trend_report_generated", report_id=report.id, trends=len(report.trend_ids))
