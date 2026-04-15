"""
Test Firecrawl /v1/map for WS product URL discovery across multiple categories.
Run with: docker compose exec api python scripts/debug_ws_map2.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

CATEGORIES = [
    ("serveware",         "serveware platters bowls"),
    ("storage",           "food storage containers pantry organizers"),
    ("tabletop",          "dinnerware plates bowls mugs"),
    ("cookware",          "cookware pots pans skillets"),
    ("bakeware",          "bakeware cake loaf pan"),
    ("linens",            "table linens placemats runners"),
]

async def map_category(client, label, search_term, limit=200):
    resp = await client.post(
        "https://api.firecrawl.dev/v1/map",
        headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
        json={
            "url": "https://www.williams-sonoma.com",
            "search": search_term,
            "limit": limit,
        },
        timeout=60,
    )
    data = resp.json()
    all_links = data.get("links", [])
    product_urls = [u for u in all_links if "/products/" in u and "?" not in u]
    # Deduplicate
    product_urls = list(dict.fromkeys(product_urls))
    return label, product_urls

async def main():
    async with httpx.AsyncClient() as client:
        total = 0
        for label, search_term in CATEGORIES:
            label, urls = await map_category(client, label, search_term)
            print(f"  {label:20s}: {len(urls):3d} products  (search: {search_term!r})")
            total += len(urls)
        print(f"\nTotal across all categories: {total}")
        print(f"Estimated Firecrawl credits: {len(CATEGORIES)} map calls + {total} product page scrapes")

asyncio.run(main())
