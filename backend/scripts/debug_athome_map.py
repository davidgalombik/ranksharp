"""
Test Firecrawl /v1/map for At Home product URL discovery,
then verify a product page scrapes correctly.
Run with: docker compose exec api python scripts/debug_athome_map.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

SEARCH_TERMS = [
    "storage organization baskets bins",
    "home decor vases candles decorative",
    "kitchen dining serveware tabletop",
]

_PRODUCT_RE = re.compile(r'https://www\.athome\.com/[^/?)\s"]+/[A-Z0-9]{8,}\.html')

async def main():
    async with httpx.AsyncClient(timeout=60) as client:

        # 1. Test /v1/map
        print("=== Firecrawl /v1/map for athome.com ===")
        total_urls = set()
        for search in SEARCH_TERMS:
            resp = await client.post(
                "https://api.firecrawl.dev/v1/map",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
                json={"url": "https://www.athome.com", "search": search, "limit": 200},
            )
            data = resp.json()
            links = data.get("links", [])
            # At Home product URLs end in a product code like /XXXXXXXX.html
            product_urls = [u for u in links if re.search(r'/[A-Z0-9]{6,}\.html', u)]
            print(f"  {search!r}: {len(links)} links, {len(product_urls)} product URLs")
            for u in product_urls[:3]:
                print(f"    {u}")
            total_urls.update(product_urls)

        print(f"\nTotal unique product URLs: {len(total_urls)}")

        if not total_urls:
            print("No product URLs found — map approach won't work for At Home")
            return

        # 2. Test scraping a product page
        print("\n=== Test product page scrape ===")
        test_url = next(iter(total_urls))
        print(f"Testing: {test_url}")
        resp = await client.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
            json={"url": test_url, "formats": ["markdown"], "waitFor": 2000, "timeout": 60000},
        )
        md = resp.json().get("data", {}).get("markdown", "")
        prices = re.findall(r'\$[\d.,]+', md[:3000])
        h1 = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
        print(f"  Markdown length : {len(md)}")
        print(f"  H1 name         : {h1.group(1) if h1 else '(none)'}")
        print(f"  Prices found    : {prices[:5]}")

asyncio.run(main())
