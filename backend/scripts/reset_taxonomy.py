"""
One-shot script: wipe `category` and `subcategory` on all products of a retailer.

Use this when introducing a new taxonomy catalog for a retailer — the wipe
ensures old (often-messy) breadcrumb-derived category strings don't linger.
The next scrape repopulates with the catalog's canonical values.

By default the wipe only touches *scraper-sourced* products (those with
`scrape_job_id IS NOT NULL`). CSV-uploaded products are preserved so users
don't lose tags from CSVs they manually classified. Pass --include-csv to
wipe everything.

Usage (inside the worker/api container):
    python scripts/reset_taxonomy.py <RETAILER_SLUG>
    python scripts/reset_taxonomy.py <RETAILER_SLUG> --include-csv
    python scripts/reset_taxonomy.py amazon-us
"""
import argparse
import sys

sys.path.insert(0, "/app")

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from config import settings
from database.models import Retailer, Product


def main() -> int:
    parser = argparse.ArgumentParser(description="Wipe category/subcategory for a retailer.")
    parser.add_argument("retailer_slug", help="Retailer slug, e.g. amazon-us")
    parser.add_argument("--include-csv", action="store_true",
                        help="Also wipe CSV-uploaded products (default: scraper-only)")
    args = parser.parse_args()

    # Build a sync engine (this is a one-shot CLI, no need for async)
    sync_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(sync_url)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        retailer = session.execute(
            select(Retailer).where(Retailer.slug == args.retailer_slug)
        ).scalar_one_or_none()
        if not retailer:
            print(f"ERROR: retailer '{args.retailer_slug}' not found", file=sys.stderr)
            return 2

        stmt = update(Product).where(Product.retailer_id == retailer.id)
        if not args.include_csv:
            stmt = stmt.where(Product.scrape_job_id.is_not(None))
        stmt = stmt.values(category=None, subcategory=None, product_segment=None)

        result = session.execute(stmt)
        session.commit()

        scope = "all products" if args.include_csv else "scraper-sourced products only"
        print(f"Wiped category/subcategory/product_segment on {result.rowcount} {scope} "
              f"for retailer '{retailer.slug}' ({retailer.name}).")
        if not args.include_csv:
            print("CSV-uploaded products preserved. Use --include-csv to wipe those too.")
        print("Trigger a scrape for this retailer to repopulate from the catalog.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
