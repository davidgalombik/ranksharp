"""Test more kitchen Container Store categories."""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

URLS = [
    "https://www.containerstore.com/s/kitchen/spice-organization/12?",
    "https://www.containerstore.com/s/kitchen/refrigerator-freezer-organizers/12?",
    "https://www.containerstore.com/s/kitchen/lazy-susans/12?",
    "https://www.containerstore.com/s/kitchen/over-door-organizers/12?",
    "https://www.containerstore.com/s/kitchen/under-sink-organizers/12?",
]

_PRODUCT_LINK_RE = re.compile(
    r'\[\$([0-9.,]+(?:\s*[–\-]\s*\$[0-9.,]+)?)\s*\\{1,2}\s*\n.*?\]\('
    r'https://www\.containerstore\.com/s/[^)]+productId=[^)]+\)',
    re.DOTALL,
)

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        for url in URLS:
            resp = await client.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
                json={"url": url, "formats": ["markdown"], "waitFor": 4000, "timeout": 60000},
            )
            md = resp.json().get("data", {}).get("markdown", "")
            matches = list(_PRODUCT_LINK_RE.finditer(md))
            pids = re.findall(r'productId=\d+', md)
            slug = url.split("/s/kitchen/")[1].rstrip("/?")
            print(f"{'✅' if len(matches)>0 else '❌'} {slug:45s}  products={len(matches):3d}  pids={len(pids)}")

asyncio.run(main())
