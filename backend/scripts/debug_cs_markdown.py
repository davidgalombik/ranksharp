"""
Debug: fetch raw Firecrawl markdown for Container Store and print first 300 lines.
Run with: docker compose exec api python scripts/debug_cs_markdown.py
"""
import asyncio, sys
sys.path.insert(0, "/app")
import httpx
from config import settings

URL = "https://www.containerstore.com/s/kitchen/pantry-organizers/12?"

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
        data = resp.json()
        md = data.get("data", {}).get("markdown", "")
        print(f"Status: {resp.status_code}")
        print(f"Markdown length: {len(md)}")
        print("--- First 300 lines ---")
        for i, line in enumerate(md.splitlines()[:300], 1):
            print(f"{i:4}: {line}")

asyncio.run(main())
