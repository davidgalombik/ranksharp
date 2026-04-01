"""
One-off script: import products from an existing Apify run into the DB.

Usage (inside the worker container):
    python scripts/import_apify_run.py <RETAILER> <RUN_ID>
    python scripts/import_apify_run.py <RETAILER> --latest

RETAILER options: wayfair, target

Examples:
    python scripts/import_apify_run.py target --latest
    python scripts/import_apify_run.py wayfair 9NtLtAVABj9Ub8AdM

The run can be timed-out or succeeded — we just read its dataset.
"""
import sys
from datetime import datetime

sys.path.insert(0, "/app")

from apify_client import ApifyClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from config import settings
from database.models import Retailer, ScrapeJob, Product, ScrapeStatus
from scraper.adapters.tier1_api.wayfair_apify import WayfairApifyAdapter
from scraper.adapters.tier1_api.wayfair_apify import _ACTOR_ID as WAYFAIR_ACTOR_ID
from scraper.adapters.tier1_api.target_apify import TargetApifyAdapter
from scraper.adapters.tier1_api.target_apify import _ACTOR_ID as TARGET_ACTOR_ID

engine = create_engine(settings.database_url_sync)
SessionLocal = sessionmaker(bind=engine)

RETAILERS = {
    "wayfair": {
        "slug": "wayfair",
        "actor_id": WAYFAIR_ACTOR_ID,
        "adapter_config": {"base_url": "https://www.wayfair.com", "categories": {}},
        "adapter_class": WayfairApifyAdapter,
    },
    "target": {
        "slug": "target-us",
        "actor_id": TARGET_ACTOR_ID,
        "adapter_config": {"base_url": "https://www.target.com", "categories": {}},
        "adapter_class": TargetApifyAdapter,
    },
}


def get_dataset_items(actor_id: str, run_id: str) -> list[dict]:
    client = ApifyClient(settings.apify_api_token)

    if run_id == "--latest":
        print(f"Fetching recent runs for actor {actor_id}...")
        runs = list(client.actor(actor_id).runs().list(limit=20).items)
        if not runs:
            print("No runs found.")
            sys.exit(1)
        print("  Recent runs (checking dataset sizes):")
        best_run_id = None
        best_count = 0
        for r in runs:
            dataset_id = r.get("defaultDatasetId", "")
            try:
                count = client.dataset(dataset_id).get().get("itemCount", 0) if dataset_id else 0
            except Exception:
                count = 0
            print(f"    {r['id']}  status={r['status']}  items={count}")
            if count > best_count:
                best_count = count
                best_run_id = r["id"]
        run_id = best_run_id
        print(f"  Selecting run with most items: {run_id} ({best_count} items)")

    print(f"Fetching run {run_id}...")
    run = client.run(run_id).get()
    if not run:
        print(f"Run {run_id} not found.")
        sys.exit(1)

    status = run.get("status")
    dataset_id = run.get("defaultDatasetId")
    print(f"  Status: {status} | Dataset: {dataset_id}")

    if status not in ("SUCCEEDED", "TIMED-OUT", "FAILED"):
        print(f"  Run status is '{status}' — cannot import from this run.")
        sys.exit(1)

    items = list(client.dataset(dataset_id).iterate_items())
    print(f"  Fetched {len(items)} items from dataset.")
    return items


def save_products(session, retailer, job_id: int, raw_products) -> dict:
    found = new = updated = 0
    for raw in raw_products:
        found += 1
        existing = session.execute(
            select(Product).where(
                Product.retailer_id == retailer.id,
                Product.url == raw.url,
            )
        ).scalar_one_or_none()

        if existing:
            existing.name = raw.name
            existing.price = raw.price
            existing.description = raw.description
            existing.image_urls = raw.image_urls
            existing.raw_attributes = raw.raw_attributes
            existing.last_seen_at = datetime.utcnow()
            existing.scrape_job_id = job_id
            updated += 1
        else:
            product = Product(
                retailer_id=retailer.id,
                scrape_job_id=job_id,
                url=raw.url,
                external_id=raw.external_id,
                sku=raw.sku,
                name=raw.name,
                description=raw.description,
                price=raw.price,
                currency=raw.currency,
                category=raw.category,
                subcategory=raw.subcategory,
                brand=raw.brand,
                image_urls=raw.image_urls,
                raw_attributes=raw.raw_attributes,
            )
            session.add(product)
            new += 1

        if found % 50 == 0:
            session.commit()
            print(f"  ...{found} processed")

    session.commit()
    return {"found": found, "new": new, "updated": updated}


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    retailer_key = sys.argv[1].lower()
    run_id = sys.argv[2]

    if retailer_key not in RETAILERS:
        print(f"ERROR: Unknown retailer '{retailer_key}'. Choose from: {', '.join(RETAILERS.keys())}")
        sys.exit(1)

    if not settings.apify_api_token:
        print("ERROR: APIFY_API_TOKEN not set in .env")
        sys.exit(1)

    cfg = RETAILERS[retailer_key]
    items = get_dataset_items(cfg["actor_id"], run_id)
    if not items:
        print("No items returned from dataset.")
        sys.exit(0)

    adapter = cfg["adapter_class"](cfg["adapter_config"])
    raw_products = [p for item in items if (p := adapter._map_item(item))]
    skipped = len(items) - len(raw_products)
    print(f"  Mapped {len(raw_products)} valid products (skipped {skipped} with missing data).")

    session = SessionLocal()
    try:
        retailer = session.execute(
            select(Retailer).where(Retailer.slug == cfg["slug"])
        ).scalar_one_or_none()

        if not retailer:
            print(f"ERROR: Retailer '{cfg['slug']}' not found in DB.")
            sys.exit(1)

        job = ScrapeJob(
            retailer_id=retailer.id,
            status=ScrapeStatus.RUNNING,
            started_at=datetime.utcnow(),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        print(f"  Created scrape job #{job.id}. Saving products...")

        result = save_products(session, retailer, job.id, raw_products)

        job.status = ScrapeStatus.SUCCESS
        job.products_found = result["found"]
        job.products_new = result["new"]
        job.products_updated = result["updated"]
        job.finished_at = datetime.utcnow()
        session.commit()

        print(f"\n✅ Done! {result['new']} new · {result['updated']} updated · {result['found']} total saved to DB.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
