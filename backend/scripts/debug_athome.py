"""
Debug At Home: try plain HTTP and Firecrawl to see what each returns.
Run with: docker compose exec api python scripts/debug_athome.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

URL = "https://www.athome.com/kitchen-dining/?nav=top_nav"

async def main():
    # 1. Plain HTTP
    print("=== Plain HTTP ===")
    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        follow_redirects=True, timeout=30
    ) as client:
        try:
            resp = await client.get(URL)
            print(f"Status: {resp.status_code}")
            print(f"Response length: {len(resp.text)}")
            # Check for Akamai block
            if "Access Denied" in resp.text or "Reference #" in resp.text:
                print("  ⛔ Akamai block detected")
            elif len(resp.text) < 5000:
                print("  ⚠️  Very short response — likely a redirect or block")
            else:
                product_links = re.findall(r'href="(/products/[^"]+)"', resp.text)
                print(f"  Product links found: {len(product_links)}")
        except Exception as e:
            print(f"  Error: {e}")

    # 2. Firecrawl
    print("\n=== Firecrawl ===")
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {settings.firecrawl_api_key}",
                "Content-Type": "application/json",
            },
            json={"url": URL, "formats": ["markdown"], "waitFor": 6000, "timeout": 60000},
        )
        data = resp.json()
        md = data.get("data", {}).get("markdown", "")
        print(f"Markdown length: {len(md)}")
        if md:
            product_urls = re.findall(r'athome\.com/[^)\s]+/p/', md)
            print(f"Product URLs found: {len(product_urls)}")
            prices = re.findall(r'\$[\d.,]+', md[:5000])
            print(f"Prices in first 5000 chars: {prices[:10]}")
            # Show first 30 lines
            print("\nFirst 30 non-empty lines:")
            count = 0
            for line in md.splitlines():
                if line.strip() and count < 30:
                    print(f"  {line[:150]}")
                    count += 1

asyncio.run(main())
