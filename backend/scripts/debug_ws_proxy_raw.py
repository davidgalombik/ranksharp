"""Check raw Firecrawl response for residential proxy request."""
import asyncio, sys
sys.path.insert(0, "/app")
import httpx, json
from config import settings

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        # Try with residential proxy
        resp = await client.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
            json={
                "url": "https://www.williams-sonoma.com/shop/entertaining/serveware/",
                "formats": ["markdown"],
                "waitFor": 6000,
                "timeout": 90000,
                "proxy": "residential",
            },
        )
        print(f"HTTP Status: {resp.status_code}")
        data = resp.json()
        # Print everything except the markdown
        summary = {k: v for k, v in data.items() if k != "data"}
        if "data" in data:
            d = data["data"]
            summary["data_keys"] = list(d.keys()) if isinstance(d, dict) else type(d).__name__
            if isinstance(d, dict):
                summary["markdown_len"] = len(d.get("markdown", ""))
                summary["metadata"] = d.get("metadata", {})
        print(json.dumps(summary, indent=2))

asyncio.run(main())
