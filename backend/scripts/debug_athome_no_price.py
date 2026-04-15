"""
Debug why some At Home products have no price.
Run with: docker compose exec api python scripts/debug_athome_no_price.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

TEST_URLS = [
    "https://www.athome.com/crossover-weave-stackable-storage-basket-navy-blue/124398740.html",
    "https://www.athome.com/light-pink-crossover-weave-storage-basket-small/125000907.html",
]

_PRICE_RE = re.compile(r'\$([0-9,]+(?:\.[0-9]{1,2})?)')

async def main():
    async with httpx.AsyncClient(timeout=60) as client:
        for url in TEST_URLS:
            print(f"\n=== {url.split('/')[-2]} ===")
            resp = await client.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
                json={"url": url, "formats": ["markdown"], "waitFor": 3000, "timeout": 60000},
            )
            data = resp.json()
            if not data.get("success"):
                print(f"FAILED: {data.get('error', data)}")
                continue
            md = data["data"]["markdown"]
            print(f"Length: {len(md)}")
            # Find all price-like patterns
            prices = _PRICE_RE.findall(md[:4000])
            print(f"Price patterns in first 4000 chars: {prices[:10]}")
            # Show relevant section around price area
            h1 = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
            if h1:
                print(f"H1: {h1.group(1)}")
            # Show 500 chars around where price should be
            price_idx = md.find('$')
            if price_idx >= 0:
                print(f"First $ at char {price_idx}:")
                print(md[max(0, price_idx-100):price_idx+200])
            else:
                print("NO $ FOUND IN MARKDOWN!")
                print("First 500 chars:")
                print(md[:500])

asyncio.run(main())
