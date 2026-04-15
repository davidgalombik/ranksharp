"""
Verify WS stealth proxy returns real category pages vs bot-detection fallback.
Key test: serveware and storage-containers should return DIFFERENT products.
Run with: docker compose exec api python scripts/debug_ws_verify.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

URLS = [
    ("serveware",          "https://www.williams-sonoma.com/shop/entertaining/serveware/"),
    ("storage-containers", "https://www.williams-sonoma.com/shop/food-pantry/storage-containers/"),
]

_PRODUCT_RE = re.compile(r'https://www\.williams-sonoma\.com/products/([^/?)\s"]+)')

async def fetch(client, url):
    resp = await client.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
        json={"url": url, "formats": ["markdown"], "waitFor": 6000, "timeout": 90000, "proxy": "stealth"},
        timeout=120,
    )
    md = resp.json().get("data", {}).get("markdown", "")
    return md

async def main():
    async with httpx.AsyncClient() as client:
        results = {}
        for label, url in URLS:
            print(f"Fetching {label}...")
            md = await fetch(client, url)
            products = set(_PRODUCT_RE.findall(md))
            results[label] = {"md_len": len(md), "products": products}
            print(f"  {label}: {len(md)} bytes, {len(products)} product URLs")

        sw = results["serveware"]["products"]
        sc = results["storage-containers"]["products"]

        overlap = sw & sc
        sw_only = sw - sc
        sc_only = sc - sw

        print(f"\n--- Overlap analysis ---")
        print(f"  Serveware only     : {len(sw_only)} products")
        print(f"  Storage only       : {len(sc_only)} products")
        print(f"  In both (shared)   : {len(overlap)} products")
        print(f"  MD lengths         : {results['serveware']['md_len']} vs {results['storage-containers']['md_len']}")

        if len(overlap) == len(sw) and len(overlap) == len(sc):
            print("\n❌ IDENTICAL product sets — bot detection still serving same fallback page")
        elif len(sw_only) >= 5 and len(sc_only) >= 5:
            print("\n✅ Distinct product sets — getting real category pages!")
            print("\nServeware sample:")
            for p in sorted(sw_only)[:8]: print(f"  {p}")
            print("Storage sample:")
            for p in sorted(sc_only)[:8]: print(f"  {p}")
        else:
            print("\n⚠️  Partial overlap — inconclusive")

asyncio.run(main())
