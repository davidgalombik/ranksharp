"""
Test Firecrawl map + stealth scrape for Crate & Barrel.
Run with: docker compose exec api python scripts/debug_cnb_firecrawl.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

BASE = "https://www.crateandbarrel.com"
_PRODUCT_RE = re.compile(r'https://www\.crateandbarrel\.com/[^/?)\s"]+/s\d+')

SEARCH_TERMS = [
    "dinnerware plates bowls mugs",
    "serveware platters serving bowls",
    "cookware pots pans",
    "storage organization canisters",
    "decorative accessories vases",
    "bar drinkware glasses",
    "bedding pillows throws",
    "lighting lamps candles",
]

TEST_PRODUCT = "https://www.crateandbarrel.com/marin-white-stoneware-dinner-plates-set-of-8/s128411"

async def main():
    async with httpx.AsyncClient(timeout=90) as client:
        # 1. Test stealth scrape on product page
        print("=== Testing stealth scrape on product page ===")
        resp = await client.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
            json={"url": TEST_PRODUCT, "formats": ["markdown"], "waitFor": 4000,
                  "timeout": 60000, "proxy": "stealth"},
        )
        data = resp.json()
        if not data.get("success"):
            print(f"stealth FAILED: {data.get('error', str(data))[:200]}")
        else:
            md = data["data"]["markdown"]
            h1 = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
            prices = re.findall(r'\$[0-9.,]+', md[:6000])
            imgs = re.findall(r'https://[^\s")(\\]+\.(?:jpg|jpeg|png|webp)', md[:6000])
            sku = re.search(r'/s(\d+)', TEST_PRODUCT)
            print(f"Length : {len(md)}")
            print(f"H1     : {h1.group(1) if h1 else '(none)'}")
            print(f"Prices : {prices[:5]}")
            print(f"SKU    : {sku.group(1) if sku else '(none)'}")
            print(f"Images : {imgs[:3]}")
            print(f"\nFirst 2000 chars:\n{md[:2000]}")

        # 2. Test /v1/map across all search terms
        print("\n\n=== Testing /v1/map across categories ===")
        total_urls = set()
        for term in SEARCH_TERMS:
            r = await client.post(
                "https://api.firecrawl.dev/v1/map",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
                json={"url": BASE, "search": term, "limit": 200},
            )
            d = r.json()
            links = d.get("links", [])
            products = [u.split("?")[0] for u in links if _PRODUCT_RE.match(u)]
            products = list(dict.fromkeys(products))
            print(f"  {term!r}: {len(links)} links, {len(products)} products")
            total_urls.update(products)

        print(f"\nTotal unique product URLs: {len(total_urls)}")
        if total_urls:
            print("Sample:")
            for u in list(total_urls)[:5]:
                print(f"  {u}")

asyncio.run(main())
