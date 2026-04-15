"""
Test Pottery Barn category page URL discovery.
Run with: docker compose exec api python3 scripts/test_pb_category.py
"""
import asyncio
import sys
sys.path.insert(0, "/app")

from scraper.adapters.tier3_browser.pottery_barn_smartproxy import PotteryBarnSmartproxyAdapter

TEST_CAT = "https://www.potterybarn.com/shop/decorating/decorative-accessories/"

async def main():
    adapter = PotteryBarnSmartproxyAdapter(retailer_config={
        "slug": "pottery-barn",
        "base_url": "https://www.potterybarn.com",
        "categories": {},
    })
    await adapter.before_scrape()
    try:
        html = await adapter._fetch_rendered(TEST_CAT)
        print(f"HTML length: {len(html) if html else 0}")

        if html:
            from bs4 import BeautifulSoup
            import re
            soup = BeautifulSoup(html, "lxml")

            # Count all <a> tags with href
            all_links = soup.find_all("a", href=True)
            print(f"Total <a> tags: {len(all_links)}")

            # Show sample hrefs to understand URL patterns
            print("\nSample hrefs (first 20 containing 'potterybarn' or starting with '/'):")
            shown = 0
            for a in all_links:
                href = a["href"]
                if ("potterybarn" in href or href.startswith("/")) and shown < 20:
                    print(f"  {href[:120]}")
                    shown += 1

            # Count product URLs matched by our regex
            urls = await adapter.get_product_urls(TEST_CAT)
            print(f"\nProduct URLs found: {len(urls)}")
            for u in urls[:10]:
                print(f"  {u}")

            # Check pagination signal
            next_btn = soup.find(string=re.compile(r'next|Next|pageNumber'))
            print(f"\nPagination signal found: {bool(next_btn)}")

            # Look for product count text
            for tag in soup.find_all(string=re.compile(r'\d+ (items|products|results)', re.I)):
                print(f"Count text: {tag.strip()[:100]}")

    finally:
        await adapter.after_scrape()

asyncio.run(main())
