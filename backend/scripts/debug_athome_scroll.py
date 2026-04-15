"""
Test Firecrawl scroll actions on At Home category page.
Run with: docker compose exec api python scripts/debug_athome_scroll.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx, json
from config import settings

URL = "https://www.athome.com/storage-organization/?nav=top_nav"

# At Home product URLs: /some-product-name/XXXXXXXX.html
_PRODUCT_RE = re.compile(r'https://www\.athome\.com/[^)\s"]+/[A-Z0-9]{6,}\.html')
_PRICE_RE = re.compile(r'\$[\d.,]+')

async def test(client, scroll_count, wait_ms):
    actions = [{"type": "wait", "milliseconds": 3000}]
    for _ in range(scroll_count):
        actions += [
            {"type": "scroll", "direction": "down", "amount": 1500},
            {"type": "wait", "milliseconds": 1500},
        ]

    resp = await client.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
        json={
            "url": URL,
            "formats": ["markdown"],
            "waitFor": wait_ms,
            "timeout": 90000,
            "actions": actions,
        },
        timeout=120,
    )
    data = resp.json()
    if not data.get("success"):
        print(f"  ERROR: {data.get('error', data)}")
        return None
    md = data.get("data", {}).get("markdown", "")
    product_urls = list(dict.fromkeys(_PRODUCT_RE.findall(md)))
    prices = _PRICE_RE.findall(md)
    return md, product_urls, prices

async def main():
    async with httpx.AsyncClient() as client:
        print(f"Testing scroll actions on: {URL}\n")

        for scrolls in [3, 6]:
            print(f"=== {scrolls} scrolls ===")
            result = await test(client, scrolls, wait_ms=4000)
            if not result:
                break
            md, product_urls, prices = result
            print(f"  Markdown length : {len(md)}")
            print(f"  Product URLs    : {len(product_urls)}")
            print(f"  Prices          : {len(prices)}")
            if product_urls:
                print(f"  Sample URLs:")
                for u in product_urls[:4]:
                    print(f"    {u}")

        if result:
            md, product_urls, prices = result
            # Show a product block to understand structure
            if product_urls:
                idx = md.find(product_urls[0].split("athome.com")[1])
                if idx > -1:
                    print(f"\n--- Raw around first product URL ---")
                    print(repr(md[max(0,idx-200):idx+100]))

asyncio.run(main())
