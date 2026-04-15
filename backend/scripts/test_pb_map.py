"""
Test Pottery Barn using Firecrawl /v1/map + longer waitFor.
Run with: docker compose exec api python3 scripts/test_pb_map.py
"""
import asyncio, sys, re, httpx
sys.path.insert(0, "/app")
from config import settings

_MAP_ENDPOINT = "https://api.firecrawl.dev/v1/map"
_SCRAPE_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
_PRODUCT_RE = re.compile(r'https://www\.potterybarn\.com/products/[^/?)\s"#]+/', re.I)

async def test_map(search_term: str):
    print(f"\n=== /v1/map search='{search_term}' ===")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _MAP_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
            json={
                "url": "https://www.potterybarn.com",
                "search": search_term,
                "limit": 30,
            },
        )
    data = resp.json()
    links = data.get("links", [])
    print(f"Links returned: {len(links)}")
    prod_links = [l for l in links if _PRODUCT_RE.match(l.split("?")[0].rstrip("/") + "/")]
    print(f"Product links: {len(prod_links)}")
    for l in prod_links[:10]:
        print(f"  {l}")
    return prod_links

async def test_longer_wait():
    """Try category page with 10s wait."""
    print("\n=== Firecrawl stealth + 10s wait ===")
    TEST_CAT = "https://www.potterybarn.com/shop/decorating/decorative-accessories/"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            _SCRAPE_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
            json={
                "url": TEST_CAT,
                "formats": ["links"],
                "waitFor": 10000,
                "timeout": 60000,
                "proxy": "stealth",
                "actions": [
                    {"type": "wait", "milliseconds": 5000},
                    {"type": "scroll", "direction": "down", "amount": 2000},
                    {"type": "wait", "milliseconds": 3000},
                ],
            },
        )
    data = resp.json()
    print(f"Success: {data.get('success')}")
    links = data.get("data", {}).get("links", [])
    print(f"Links: {len(links)}")
    prod_links = [l for l in links if "potterybarn.com/products/" in l or "/shop/" in l.lower()]
    print(f"PB links (products + shop): {len(prod_links)}")
    for l in list(dict.fromkeys(prod_links))[:20]:
        print(f"  {l}")

async def main():
    # Test /v1/map with different search terms
    await test_map("decorative accessories vase candle")
    await test_map("storage organization canister")
    await test_map("candles holders vases")
    # Also test longer wait on category page
    await test_longer_wait()

asyncio.run(main())
