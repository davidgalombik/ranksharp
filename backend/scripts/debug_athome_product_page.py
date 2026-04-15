"""
Test Firecrawl scrape on an At Home product page to understand markdown structure.
Run with: docker compose exec api python scripts/debug_athome_product_page.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

TEST_URL = "https://www.athome.com/black-storage-basket-small/124385350.html"

async def main():
    async with httpx.AsyncClient(timeout=60) as client:
        print(f"Fetching: {TEST_URL}\n")
        resp = await client.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {settings.firecrawl_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "url": TEST_URL,
                "formats": ["markdown"],
                "waitFor": 3000,
                "timeout": 60000,
            },
        )
        data = resp.json()
        if not data.get("success"):
            print(f"ERROR: {data.get('error', data)}")
            return

        md = data.get("data", {}).get("markdown", "")
        print(f"Markdown length: {len(md)}")
        print(f"\n--- First 3000 chars ---")
        print(md[:3000])
        print(f"\n--- Regex probes ---")
        prices = re.findall(r'\$[0-9.,]+', md[:3000])
        print(f"Prices: {prices[:5]}")
        h1 = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
        print(f"H1: {h1.group(1) if h1 else '(none)'}")
        sku = re.search(r'(?:sku|item|product)[:\s#]+([A-Za-z0-9\-]+)', md, re.I)
        print(f"SKU: {sku.group(1) if sku else '(none)'}")
        imgs = re.findall(r'https://[^\s)"]+\.(?:jpg|jpeg|png|webp)', md)
        print(f"Image URLs: {imgs[:3]}")

asyncio.run(main())
