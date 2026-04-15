"""
Test WS with Firecrawl stealth/enhanced proxy options.
Run with: docker compose exec api python scripts/debug_ws_proxy2.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

URL = "https://www.williams-sonoma.com/shop/entertaining/serveware/"
_PRODUCT_RE = re.compile(r'https://www\.williams-sonoma\.com/products/([^/?)\s]+)')

async def test_proxy(client, proxy_mode: str):
    resp = await client.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
        json={
            "url": URL,
            "formats": ["markdown"],
            "waitFor": 6000,
            "timeout": 90000,
            "proxy": proxy_mode,
        },
        timeout=120,
    )
    data = resp.json()
    if not data.get("success"):
        return proxy_mode, 0, 0, f"ERROR: {data.get('error', data)}"
    md = data.get("data", {}).get("markdown", "")
    products = set(_PRODUCT_RE.findall(md))
    return proxy_mode, len(md), len(products), list(products)[:5]

async def main():
    async with httpx.AsyncClient() as client:
        for mode in ["stealth", "enhanced", "auto"]:
            print(f"\nTesting proxy={mode!r} ...")
            mode, md_len, prod_count, sample = await test_proxy(client, mode)
            print(f"  Markdown length : {md_len}")
            print(f"  Product URLs    : {prod_count}")
            if isinstance(sample, str):
                print(f"  {sample}")
            elif sample:
                for s in sample:
                    print(f"    /products/{s}")
            # Stop early if we found products
            if prod_count >= 10:
                print(f"\n✅ proxy={mode!r} works — {prod_count} products found!")
                break
            elif prod_count > 0:
                print(f"  ⚠️  Partial — only {prod_count} products")
            else:
                print(f"  ❌ No products")

asyncio.run(main())
