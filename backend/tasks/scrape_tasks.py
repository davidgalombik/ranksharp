"""Celery tasks for scraping."""
import asyncio
import re
import json
from contextlib import contextmanager
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
import redis as redis_lib
from tasks.celery_app import app
from config import settings
from database.models import Retailer, ScrapeJob, Product, ScrapeStatus
from scraper.registry import AdapterRegistry
import structlog

log = structlog.get_logger()

_PATENT_RE = re.compile(r'\bpatent', re.IGNORECASE)


def _detect_patent(raw_product) -> bool:
    """Return True if any text field on the product mentions 'patent'."""
    texts = [
        raw_product.name or "",
        raw_product.description or "",
        json.dumps(raw_product.raw_attributes) if raw_product.raw_attributes else "",
    ]
    return any(_PATENT_RE.search(t) for t in texts)

# Synchronous DB session for Celery tasks
engine = create_engine(settings.database_url_sync)

# Redis client for distributed scrape locks
_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)
_LOCK_TTL = 7200  # 2 hours — max time a scrape should ever take


@contextmanager
def _scrape_lock(slug: str):
    """Acquire a per-retailer Redis lock. Yields True if acquired, False if already held."""
    key = f"scrape_lock:{slug}"
    acquired = _redis.set(key, "1", nx=True, ex=_LOCK_TTL)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            _redis.delete(key)


def _get_session():
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


@app.task(bind=True, max_retries=2, queue="scrape")
def scrape_retailer(self, retailer_id: int, skip_analysis: bool = False):
    """Scrape a single retailer and upsert products.

    Args:
        retailer_id:    DB id of the retailer row to scrape.
        skip_analysis:  When True, no Claude analysis tasks are queued after
                        scraping — useful for test runs or bulk data imports
                        where you want to control AI spend manually.
    """
    # Look up slug first so we can use it for the lock key
    _session = _get_session()
    try:
        _retailer = _session.get(Retailer, retailer_id)
        retailer_slug = _retailer.slug if _retailer else str(retailer_id)
    finally:
        _session.close()

    with _scrape_lock(retailer_slug) as acquired:
        if not acquired:
            log.warning("scrape_already_running", retailer=retailer_slug,
                        hint="Another worker is already scraping this retailer — skipping duplicate")
            return {"status": "skipped", "retailer": retailer_slug, "reason": "already_running"}

        return _run_scrape_task(self, retailer_id, skip_analysis)


def _run_scrape_task(self, retailer_id: int, skip_analysis: bool):
    """Inner implementation of scrape_retailer, called only when lock is held."""
    session = _get_session()
    try:
        retailer = session.get(Retailer, retailer_id)
        if not retailer or not retailer.is_active:
            return {"status": "skipped", "retailer_id": retailer_id}

        # Create scrape job record
        job = ScrapeJob(
            retailer_id=retailer_id,
            status=ScrapeStatus.RUNNING,
            started_at=datetime.utcnow(),
        )
        session.add(job)
        session.commit()

        retailer_config = {
            "slug": retailer.slug,
            "base_url": retailer.base_url,
            "adapter_class": retailer.adapter_class,
            "categories": retailer.categories,
            "country": retailer.country,
        }

        try:
            result = asyncio.run(_run_scrape(retailer_config, retailer_id, job.id, session))
            job.status = ScrapeStatus.SUCCESS
            job.products_found = result["found"]
            job.products_new = result["new"]
            job.products_updated = result["updated"]
        except Exception as exc:
            log.error("scrape_failed", retailer=retailer.slug, error=str(exc))
            job.status = ScrapeStatus.FAILED
            job.error_message = str(exc)
            raise self.retry(exc=exc, countdown=300)
        finally:
            job.finished_at = datetime.utcnow()
            session.commit()

        if skip_analysis:
            log.info("analysis_skipped", retailer=retailer.slug, new=result["new"])
        else:
            enqueue_product_analysis.delay(job.id)
        return {"status": "success", "retailer": retailer.slug, "skip_analysis": skip_analysis, **result}

    finally:
        session.close()


async def _run_scrape(retailer_config: dict, retailer_id: int, job_id: int, session) -> dict:
    """Async scrape execution."""
    adapter = AdapterRegistry.build(retailer_config)
    found = new = updated = 0
    scrape_start = datetime.utcnow()
    seen_urls: set[str] = set()

    async for raw_product in adapter.scrape():
        found += 1
        seen_urls.add(raw_product.url)
        existing = session.execute(
            select(Product).where(
                Product.retailer_id == retailer_id,
                Product.url == raw_product.url,
            )
        ).scalar_one_or_none()

        if existing:
            # Update fields that change over time
            existing.name = raw_product.name
            existing.price = raw_product.price
            existing.description = raw_product.description
            existing.image_urls = raw_product.image_urls
            existing.primary_image_url = raw_product.primary_image_url
            existing.raw_attributes = raw_product.raw_attributes
            existing.last_seen_at = datetime.utcnow()
            existing.is_active = True  # re-activate if it was previously deactivated
            # Always update category/price/name so re-scrapes can fix bad data
            if raw_product.category:
                existing.category = raw_product.category
            if raw_product.subcategory:
                existing.subcategory = raw_product.subcategory
            if raw_product.product_segment:
                existing.product_segment = raw_product.product_segment
            if raw_product.price is not None:
                existing.price = raw_product.price
            existing.name = raw_product.name
            # Do NOT reset analysis_status — only newly added products get analysed
            existing.scrape_job_id = job_id
            # Product has been seen before — no longer new
            existing.is_new = False
            # Promote to best-seller if now seen on a best-seller page; never demote
            if raw_product.is_best_seller:
                existing.is_best_seller = True
            # Promote to patent if detected; never demote
            if _detect_patent(raw_product):
                existing.has_patent = True
            updated += 1
        else:
            product = Product(
                retailer_id=retailer_id,
                scrape_job_id=job_id,
                url=raw_product.url,
                external_id=raw_product.external_id,
                sku=raw_product.sku,
                name=raw_product.name,
                description=raw_product.description,
                price=raw_product.price,
                currency=raw_product.currency,
                category=raw_product.category,
                subcategory=raw_product.subcategory,
                product_segment=raw_product.product_segment,
                brand=raw_product.brand,
                image_urls=raw_product.image_urls,
                primary_image_url=raw_product.primary_image_url,
                raw_attributes=raw_product.raw_attributes,
                is_best_seller=raw_product.is_best_seller,
                has_patent=_detect_patent(raw_product),
            )
            session.add(product)
            new += 1

        if found % 50 == 0:
            session.commit()
            log.info("scrape_progress", retailer=retailer_config["slug"], found=found)

    session.commit()

    # Deactivate products for this retailer that were NOT seen in this scrape run.
    # These are products that have disappeared from the retailer's site.
    from sqlalchemy import update as sa_update
    deactivated = session.execute(
        sa_update(Product)
        .where(
            Product.retailer_id == retailer_id,
            Product.is_active == True,
            Product.last_seen_at < scrape_start,
        )
        .values(is_active=False)
    ).rowcount
    session.commit()

    if deactivated:
        log.info("products_deactivated", retailer=retailer_config["slug"], count=deactivated)

    return {"found": found, "new": new, "updated": updated, "deactivated": deactivated}


@app.task(queue="scrape")
def scrape_all_retailers(skip_analysis: bool = False):
    """Fan out a scrape task per active retailer."""
    session = _get_session()
    try:
        retailers = session.execute(
            select(Retailer).where(Retailer.is_active == True)
        ).scalars().all()

        job_ids = []
        for retailer in retailers:
            result = scrape_retailer.delay(retailer.id, skip_analysis=skip_analysis)
            job_ids.append(result.id)
            log.info("scrape_queued", retailer=retailer.slug, skip_analysis=skip_analysis)

        return {"queued": len(job_ids), "task_ids": job_ids}
    finally:
        session.close()


@app.task(queue="analysis")
def enqueue_product_analysis(job_id: int):
    """Queue analysis tasks for all unanalysed products from a scrape job."""
    from tasks.analysis_tasks import analyse_product

    session = _get_session()
    try:
        products = session.execute(
            select(Product).where(
                Product.scrape_job_id == job_id,
                Product.analysis_status == ScrapeStatus.PENDING,
            )
        ).scalars().all()

        for product in products:
            analyse_product.delay(product.id)

        return {"queued": len(products)}
    finally:
        session.close()
