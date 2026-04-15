"""
Debug WS: try multiple category URLs and longer wait to see if products load.
Run with: docker compose exec api python scripts/debug_ws2.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

URLS = [
    "https://www.williams-sonoma.com/shop/entertaining/serveware/",
    "https://www.williams-sonoma.com/shop/food-pantry/storage-containers/",
    "https://www.williams-sonoma.com/search/results.html?query=serveware&sortby=featured",
]

_PRODUCT_RE = re.compile(r'https://www\.williams-sonoma\.com/products/[^\s)]+')
_IMG_PRODUCT_RE = re.compile(
    r'https://assets\.wsimgs\.com/wsimgs/(?:ab|rk)/images/dp/(?:wcm|ab)/[^\s)]+\.jpg'
)

async def fetch(client, url, wait_ms):
    resp = await client.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={
            "Authorization": f"Bearer {settings.firecrawl_api_key}",
            "Content-Type": "application/json",
        },
        json={"url": url, "formats": ["markdown"], "waitFor": wait_ms, "timeout": 90000},
    )
    return resp.json().get("data", {}).get("markdown", "")

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        for url in URLS:
            print(f"\n{'='*70}")
            print(f"URL: {url}")
            # Try with 8 seconds wait
            md = await fetch(client, url, 8000)
            print(f"Markdown length: {len(md)}")

            product_urls = set(_PRODUCT_RE.findall(md))
            product_imgs = set(_IMG_PRODUCT_RE.findall(md))
            print(f"  Product URLs (/products/): {len(product_urls)}")
            print(f"  Product images (wcm/ab): {len(product_imgs)}")

            # Show first 5 product URLs
            for u in sorted(product_urls)[:5]:
                print(f"    {u[:100]}")

asyncio.run(main())
