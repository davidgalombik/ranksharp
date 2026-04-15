"""
Test Firecrawl /v1/map for At Home URL discovery.
Run with: docker compose exec api python scripts/debug_athome_firecrawl_map.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

BASE = "https://www.athome.com"
_PRODUCT_RE = re.compile(r'https://www\.athome\.com/[^/?)\s"]+/[A-Z0-9]{6,}\.html')

SEARCH_TERMS = [
    "storage baskets bins organization",
    "home decor vases candles wall art",
    "outdoor garden patio furniture",
    "holiday christmas seasonal",
    "furniture accent chairs tables",
]

async def main():
    async with httpx.AsyncClient(timeout=60) as client:
        # Check credits first with a minimal call
        print("=== Checking Firecrawl credits / map endpoint ===")
        resp = await client.post(
            "https://api.firecrawl.dev/v1/map",
            headers={
                "Authorization": f"Bearer {settings.firecrawl_api_key}",
                "Content-Type": "application/json",
            },
            json={"url": BASE, "search": "storage baskets", "limit": 10},
        )
        print(f"HTTP {resp.status_code}")
        data = resp.json()

        if resp.status_code == 402:
            print("Credits exhausted — try again after monthly reset")
            return
        if resp.status_code != 200:
            print(f"Error: {data}")
            return

        links = data.get("links", [])
        product_urls = [u for u in links if _PRODUCT_RE.match(u)]
        print(f"Initial probe: {len(links)} links, {len(product_urls)} product URLs")
        for u in product_urls[:5]:
            print(f"  {u}")

        if not product_urls:
            print("\nNo products from /v1/map — At Home map approach may not work")
            print("All returned links:")
            for u in links[:20]:
                print(f"  {u}")
            return

        # If we got product URLs, run all search terms
        print("\n=== Full search across categories ===")
        total_urls = set()
        for term in SEARCH_TERMS:
            r = await client.post(
                "https://api.firecrawl.dev/v1/map",
                headers={
                    "Authorization": f"Bearer {settings.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json={"url": BASE, "search": term, "limit": 200},
            )
            d = r.json()
            all_links = d.get("links", [])
            prods = [u for u in all_links if _PRODUCT_RE.match(u)]
            print(f"  {term!r}: {len(all_links)} links, {len(prods)} products")
            total_urls.update(prods)

        print(f"\nTotal unique product URLs: {len(total_urls)}")
        if total_urls:
            print("Sample:")
            for u in list(total_urls)[:5]:
                print(f"  {u}")

asyncio.run(main())
