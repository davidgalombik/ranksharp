"""
Debug Container Store empty categories — show what markdown and regex matches look like.
Run with: docker compose exec api python scripts/debug_cs_categories.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

URLS = [
    "https://www.containerstore.com/s/kitchen/drawer-organizers/12?",
    "https://www.containerstore.com/s/closet/bins-baskets/12?",
    "https://www.containerstore.com/s/closet/drawer-organizers/12?",
]

_PRODUCT_LINK_RE = re.compile(
    r'\[\$([0-9.,]+(?:\s*[–\-]\s*\$[0-9.,]+)?)\s*\\{1,2}\s*\n'
    r'(.*?)'
    r'\]\((https://www\.containerstore\.com/s/[^)]+productId=[^)]+)\)',
    re.DOTALL,
)

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        for url in URLS:
            resp = await client.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
                json={"url": url, "formats": ["markdown"], "waitFor": 4000, "timeout": 60000},
            )
            md = resp.json().get("data", {}).get("markdown", "")
            matches = list(_PRODUCT_LINK_RE.finditer(md))
            print(f"\n{'='*70}")
            print(f"URL: {url}")
            print(f"Markdown length: {len(md)}")
            print(f"_PRODUCT_LINK_RE matches: {len(matches)}")

            # Find any price patterns
            prices = re.findall(r'\[\$[0-9.,]+', md)
            print(f"Price patterns: {len(prices)} — first 3: {prices[:3]}")

            # Find productId patterns
            pids = re.findall(r'productId=\d+', md)
            print(f"productId patterns: {len(pids)} — first 3: {pids[:3]}")

            # Show first product-like block
            idx = md.find("productId=")
            if idx > -1:
                print(f"\nRaw around first productId:")
                print(repr(md[max(0, idx-300):idx+100]))

asyncio.run(main())
