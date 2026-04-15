"""
Debug Williams-Sonoma adapter — show raw listing markdown and matched products.
Run with: docker compose exec api python scripts/debug_ws.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

URL = "https://www.williams-sonoma.com/shop/entertaining/serveware/"

_LISTING_BLOCK_RE = re.compile(
    r'-\s*\[!\[([^\]]+)\]\((https://assets\.wsimgs\.com/[^)]+)\)'
    r'.*?'
    r'-\s+([^\]]+)\]\((https://www\.williams-sonoma\.com/products/[^)]+)\)',
    re.DOTALL,
)

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {settings.firecrawl_api_key}",
                "Content-Type": "application/json",
            },
            json={"url": URL, "formats": ["markdown"], "waitFor": 5000, "timeout": 60000},
        )
        md = resp.json().get("data", {}).get("markdown", "")
        print(f"Markdown length: {len(md)}")

        # Show lines containing /products/ URLs
        print("\n--- Lines with /products/ URLs ---")
        for i, line in enumerate(md.splitlines()):
            if "williams-sonoma.com/products/" in line:
                print(f"{i:4}: {line[:180]}")

        # Show regex matches
        print("\n--- _LISTING_BLOCK_RE matches ---")
        for i, m in enumerate(list(_LISTING_BLOCK_RE.finditer(md))[:5], 1):
            print(f"\nMatch {i}:")
            print(f"  img_alt : {m.group(1)!r}")
            print(f"  img_url : {m.group(2)[:80]!r}")
            print(f"  name    : {m.group(3)!r}")
            print(f"  url     : {m.group(4)[:80]!r}")

        # Also show raw lines 1-80 to understand structure
        print("\n--- Raw lines 1-80 ---")
        for i, line in enumerate(md.splitlines()[1:80], 2):
            if line.strip():
                print(f"{i:4}: {line[:150]}")

asyncio.run(main())
