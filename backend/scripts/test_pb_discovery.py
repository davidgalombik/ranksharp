"""
Test improved search terms for Best Sellers and Storage categories.
Run with: docker compose exec api python3 scripts/test_pb_discovery.py
"""
import asyncio, sys
sys.path.insert(0, "/app")
from scraper.adapters.tier3_browser.pottery_barn_smartproxy import PotteryBarnSmartproxyAdapter

async def main():
    adapter = PotteryBarnSmartproxyAdapter(retailer_config={
        "slug": "pottery-barn", "base_url": "https://www.potterybarn.com", "categories": {},
    })
    await adapter.before_scrape()
    try:
        for cat in ["Best Sellers", "Storage & Organization"]:
            urls = await adapter.get_product_urls(cat)
            print(f"{cat}: {len(urls)} product URLs")
            for u in urls[:5]:
                print(f"  {u}")
            print()
    finally:
        await adapter.after_scrape()

asyncio.run(main())
