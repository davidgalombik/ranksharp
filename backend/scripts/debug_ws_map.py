"""
Try Firecrawl /v1/map to discover WS product URLs, and also check
if WS has a JSON search/catalog API endpoint we can call directly.
Run with: docker compose exec api python scripts/debug_ws_map.py
"""
import asyncio, sys, re, json
sys.path.insert(0, "/app")
import httpx
from config import settings

async def main():
    async with httpx.AsyncClient(timeout=60) as client:

        # 1. Try Firecrawl /v1/map for product URL discovery
        print("=== Firecrawl /v1/map ===")
        resp = await client.post(
            "https://api.firecrawl.dev/v1/map",
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
            json={
                "url": "https://www.williams-sonoma.com",
                "search": "serveware",
                "limit": 50,
            },
        )
        data = resp.json()
        print(f"Status: {resp.status_code}")
        urls = data.get("links", [])
        product_urls = [u for u in urls if "/products/" in u]
        print(f"Total links: {len(urls)}, product links: {len(product_urls)}")
        for u in product_urls[:10]:
            print(f"  {u}")

        # 2. Try WS search JSON API endpoint (common e-commerce pattern)
        print("\n=== WS Search API (JSON) ===")
        search_urls = [
            "https://www.williams-sonoma.com/search/results.html?query=serveware&format=json",
            "https://www.williams-sonoma.com/api/2.0/catalog/search?query=serveware&maxResults=20",
        ]
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json, text/javascript, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        for url in search_urls:
            try:
                r = await client.get(url, headers=headers)
                print(f"{url[:80]} -> {r.status_code}, {len(r.text)} bytes")
                if r.status_code == 200 and "product" in r.text.lower():
                    print(f"  Contains 'product'! First 300: {r.text[:300]}")
            except Exception as e:
                print(f"  Error: {e}")

asyncio.run(main())
