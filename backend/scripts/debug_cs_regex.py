"""
Debug Container Store regex matching.
Run with: docker compose exec api python scripts/debug_cs_regex.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
from config import settings

URL = "https://www.containerstore.com/s/kitchen/pantry-organizers/12?"

_PRODUCT_LINK_RE = re.compile(
    r'\[\$([0-9.,]+(?:\s*[–\-]\s*\$[0-9.,]+)?)\s*\\\s*\n'
    r'(.*?)'
    r'\]\((https://www\.containerstore\.com/s/[^)]+productId=[^)]+)\)',
    re.DOTALL,
)

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        payload = {
            "url": URL,
            "formats": ["markdown"],
            "waitFor": 4000,
            "timeout": 60000,
        }
        resp = await client.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {settings.firecrawl_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        md = resp.json().get("data", {}).get("markdown", "")

        # Show first product block raw bytes
        idx = md.find("containerstore.com/s/kitchen/pantry-organizers/the-home-edit")
        if idx > -1:
            snippet = md[max(0, idx-200):idx+200]
            print("=== RAW snippet around first product URL ===")
            print(repr(snippet))

        # Try regex
        matches = list(_PRODUCT_LINK_RE.finditer(md))
        print(f"\n_PRODUCT_LINK_RE found {len(matches)} matches")

        # Try looser regex: two backslashes
        loose_re = re.compile(
            r'\[\$([0-9.,]+(?:\s*[–\-]\s*\$[0-9.,]+)?)\s*\\{1,2}\s*\n'
            r'(.*?)'
            r'\]\((https://www\.containerstore\.com/s/[^)]+productId=[^)]+)\)',
            re.DOTALL,
        )
        matches2 = list(loose_re.finditer(md))
        print(f"loose regex (1-2 backslashes) found {len(matches2)} matches")
        if matches2:
            m = matches2[0]
            print(f"\nFirst match price: {m.group(1)!r}")
            print(f"First match middle: {m.group(2)!r}")
            print(f"First match url: {m.group(3)!r}")

asyncio.run(main())
