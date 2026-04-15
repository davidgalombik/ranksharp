"""
Test Firecrawl on Anthropologie product page.
Run with: docker compose exec api python scripts/debug_anthropologie_firecrawl.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

URL = "https://www.anthropologie.com/shop/floral-hibiscus-tide-ceramic-buoy-candle?color=042"

async def main():
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
            json={"url": URL, "formats": ["markdown"], "waitFor": 3000, "timeout": 60000},
        )
        data = resp.json()
        if not data.get("success"):
            print(f"FAILED: {data.get('error', data)}")
            return

        md = data["data"]["markdown"]
        print(f"Length: {len(md)}")
        h1 = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
        prices = re.findall(r'\$[0-9.,]+', md[:5000])
        imgs = re.findall(r'https://[^\s")(\\]+\.(?:jpg|jpeg|png|webp)', md[:5000])
        sku = re.search(r'(?:sku|item|product.?id|style)[:\s#"\']+([A-Za-z0-9\-]+)', md[:3000], re.I)
        print(f"H1   : {h1.group(1) if h1 else '(none)'}")
        print(f"Price: {prices[:5]}")
        print(f"SKU  : {sku.group(1) if sku else '(none)'}")
        print(f"Images: {len(imgs)}")
        for img in imgs[:3]:
            print(f"  {img}")
        print(f"\n--- First 2000 chars ---")
        print(md[:2000])

asyncio.run(main())
