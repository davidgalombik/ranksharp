"""
Test Pottery Barn: /v1/map discovery + Smartproxy product page.
Run with: docker compose exec api python3 scripts/test_pottery_barn.py
"""
import asyncio, sys
sys.path.insert(0, "/app")
from scraper.adapters.tier3_browser.pottery_barn_smartproxy import PotteryBarnSmartproxyAdapter

async def main():
    adapter = PotteryBarnSmartproxyAdapter(retailer_config={
        "slug": "pottery-barn",
        "base_url": "https://www.potterybarn.com",
        "categories": {},
    })
    await adapter.before_scrape()
    try:
        # 1. Test URL discovery for one category
        print("=== URL discovery: Vases ===")
        urls = await adapter.get_product_urls("Vases")
        print(f"Found {len(urls)} product URLs")
        for u in urls[:8]:
            print(f"  {u}")

        # 2. Test product page parsing on the first result
        if urls:
            print(f"\n=== Product page: {urls[0]} ===")
            result = await adapter.parse_product(urls[0])
            if result:
                print(f"✅ SUCCESS")
                print(f"   Name:   {result.name}")
                print(f"   Price:  {result.price} {result.currency}")
                print(f"   SKU:    {result.sku}")
                print(f"   Images: {len(result.image_urls)}")
                for img in result.image_urls[:3]:
                    print(f"     - {img}")
            else:
                print("❌ parse_product returned None")
    finally:
        await adapter.after_scrape()

asyncio.run(main())
