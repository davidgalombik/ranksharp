"""Test Container Store replacement category URLs."""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

URLS = [
    "https://www.containerstore.com/s/kitchen/countertop-organization/12?",
    "https://www.containerstore.com/s/kitchen/sink-organization/12?",
    "https://www.containerstore.com/s/storage/bins-baskets/12?",
    "https://www.containerstore.com/s/storage/baskets/12?",
    "https://www.containerstore.com/s/storage/fabric-bins/12?",
]

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        for url in URLS:
            resp = await client.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
                json={"url": url, "formats": ["markdown"], "waitFor": 4000, "timeout": 60000},
            )
            md = resp.json().get("data", {}).get("markdown", "")
            pids = re.findall(r'productId=\d+', md)
            prices = re.findall(r'\[\$[0-9.,]+\s*\\\\', md)
            print(f"{'OK' if len(pids)>5 else 'NO'} {url[-50:]:50s}  pids={len(pids):3d}  price_blocks={len(prices):3d}")

asyncio.run(main())
