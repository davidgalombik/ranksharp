"""
Test Pottery Barn category page via Firecrawl stealth (same approach as West Elm).
Run with: docker compose exec api python3 scripts/test_pb_firecrawl.py
"""
import asyncio, sys, re, httpx
sys.path.insert(0, "/app")
from config import settings

TEST_CAT = "https://www.potterybarn.com/shop/decorating/decorative-accessories/"
_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
_PRODUCT_RE = re.compile(r'https://www\.potterybarn\.com/products/[^/?)\s"#]+/', re.I)

async def main():
    print(f"Testing Firecrawl stealth on: {TEST_CAT}\n")
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            _FIRECRAWL_ENDPOINT,
            headers={
                "Authorization": f"Bearer {settings.firecrawl_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "url": TEST_CAT,
                "formats": ["markdown", "links"],
                "waitFor": 5000,
                "timeout": 60000,
                "proxy": "stealth",
            },
        )

    data = resp.json()
    print(f"Success: {data.get('success')}")
    if not data.get("success"):
        print(f"Error: {data.get('error')}")
        return

    page_data = data.get("data", {})
    md = page_data.get("markdown", "")
    links = page_data.get("links", [])
    print(f"Markdown length: {len(md):,}")
    print(f"Links count: {len(links)}")

    # Find product URLs in links
    prod_from_links = []
    for u in links:
        clean = u.split("?")[0].rstrip("/") + "/"
        if _PRODUCT_RE.match(clean):
            prod_from_links.append(clean)
    prod_from_links = list(dict.fromkeys(prod_from_links))
    print(f"\nProduct URLs from links array: {len(prod_from_links)}")
    for u in prod_from_links[:10]:
        print(f"  {u}")

    # Find product URLs in markdown
    prod_from_md = list(dict.fromkeys(_PRODUCT_RE.findall(md)))
    print(f"\nProduct URLs from markdown: {len(prod_from_md)}")
    for u in prod_from_md[:10]:
        print(f"  {u}")

    print(f"\nFirst 500 chars of markdown:\n{md[:500]}")

asyncio.run(main())
