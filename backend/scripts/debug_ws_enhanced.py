"""Test WS with enhanced proxy — also try longer wait and scroll actions."""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

_PRODUCT_RE = re.compile(r'https://www\.williams-sonoma\.com/products/([^/?)\s"]+)')

URLS = [
    "https://www.williams-sonoma.com/shop/entertaining/serveware/",
    "https://www.williams-sonoma.com/shop/food-pantry/storage-containers/",
]

async def fetch(client, url, proxy, wait_ms=6000, actions=None):
    payload = {
        "url": url,
        "formats": ["markdown"],
        "waitFor": wait_ms,
        "timeout": 90000,
        "proxy": proxy,
    }
    if actions:
        payload["actions"] = actions
    resp = await client.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    md = resp.json().get("data", {}).get("markdown", "")
    return md

async def main():
    async with httpx.AsyncClient() as client:
        for proxy in ["enhanced"]:
            print(f"\n=== proxy={proxy!r} ===")
            mds = []
            for url in URLS:
                label = url.split("/shop/")[1].rstrip("/")
                md = await fetch(client, url, proxy)
                products = set(_PRODUCT_RE.findall(md))
                mds.append((label, len(md), products))
                print(f"  {label}: {len(md)} bytes, {len(products)} products")

            overlap = mds[0][2] & mds[1][2]
            if len(overlap) == len(mds[0][2]) and len(overlap) == len(mds[1][2]):
                print("  ❌ Identical — bot detection active")
            else:
                print(f"  ✅ Different content — real pages!")
                print(f"  Serveware unique: {mds[0][2] - mds[1][2]}")

        # Also try enhanced + scroll actions on serveware
        print(f"\n=== enhanced + scroll actions ===")
        md = await fetch(
            client,
            URLS[0],
            "enhanced",
            wait_ms=8000,
            actions=[
                {"type": "wait", "milliseconds": 3000},
                {"type": "scroll", "direction": "down", "amount": 1000},
                {"type": "wait", "milliseconds": 2000},
                {"type": "scroll", "direction": "down", "amount": 1000},
                {"type": "wait", "milliseconds": 2000},
            ]
        )
        products = set(_PRODUCT_RE.findall(md))
        print(f"  {len(md)} bytes, {len(products)} products")
        if products:
            for p in sorted(products)[:8]:
                print(f"  /products/{p}")

asyncio.run(main())
