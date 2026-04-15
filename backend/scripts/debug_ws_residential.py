"""
Test Williams-Sonoma with Firecrawl residential proxy.
Run with: docker compose exec api python scripts/debug_ws_residential.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

# Test two category URLs to confirm they return DIFFERENT content
# (if WS is still bot-detecting, both return identical ~45KB markdown)
URLS = [
    "https://www.williams-sonoma.com/shop/entertaining/serveware/",
    "https://www.williams-sonoma.com/shop/food-pantry/storage-containers/",
]

_PRODUCT_RE = re.compile(r'https://www\.williams-sonoma\.com/products/([^/?)\s]+)')
# WS product images use wsimgs.com/wsimgs/ab/ or /rk/ with wcm or ab path
_PROD_IMG_RE = re.compile(
    r'https://assets\.wsimgs\.com/wsimgs/(?:ab|rk)/images/dp/(?:wcm|ab)/[^\s)"]+\.jpg'
)

async def scrape(client, url, use_proxy: bool):
    payload = {
        "url": url,
        "formats": ["markdown"],
        "waitFor": 6000,
        "timeout": 90000,
    }
    if use_proxy:
        payload["proxy"] = "residential"

    resp = await client.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={
            "Authorization": f"Bearer {settings.firecrawl_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    data = resp.json()
    md = data.get("data", {}).get("markdown", "")
    return md

async def main():
    async with httpx.AsyncClient() as client:
        results = []
        for url in URLS:
            print(f"\nFetching (residential proxy): {url}")
            md = await scrape(client, url, use_proxy=True)
            product_slugs = set(_PRODUCT_RE.findall(md))
            product_imgs = set(_PROD_IMG_RE.findall(md))
            results.append((url, md, product_slugs, product_imgs))
            print(f"  Markdown length   : {len(md)}")
            print(f"  Product URLs      : {len(product_slugs)}")
            print(f"  Product images    : {len(product_imgs)}")
            if product_slugs:
                print(f"  Sample products   :")
                for slug in sorted(product_slugs)[:5]:
                    print(f"    /products/{slug}")

        # Key test: if both return the same length markdown, bot detection is still active
        md1, md2 = results[0][1], results[1][1]
        if len(md1) == len(md2):
            print(f"\n⚠️  Both pages returned identical length ({len(md1)} bytes) — bot detection still active")
        else:
            print(f"\n✅ Different lengths ({len(md1)} vs {len(md2)}) — getting real category pages!")

        # Also check if product count is plausible (real serveware page should have 20-100 products)
        p1 = len(results[0][2])
        print(f"\nServeware product URLs: {p1}")
        if p1 >= 10:
            print("✅ Looks like real product data — worth building the adapter!")
        elif p1 > 0:
            print("⚠️  Some products found but count seems low — may still be partial")
        else:
            print("❌ No products — still blocked")

asyncio.run(main())
